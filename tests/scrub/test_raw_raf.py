"""Phase 4 M18 — Fujifilm RAF: the metadata the decoder reads lives in the preview.

Every identity value in a RAF sits in the preview JPEG's own EXIF, Fujifilm's maker
note included, and that EXIF cannot be dropped: stripped, LibRaw no longer decodes
the file. So it is cleaned in place. And a search of the bytes then found the serial
and a date again in the Fuji header block, which ExifTool does not decode -- so every
removed text value is searched for there too.

CI runs on a hand-built RAF (structure); the real X-T4 file runs the decode checks.
"""
from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from src.scrub.dispatch import default_dispatcher  # noqa: E402
from src.scrub.errors import ParseError  # noqa: E402
from src.scrub.formats.raw import raf  # noqa: E402
from tests.scrub import raw_corpus as rc  # noqa: E402

PLANTED = (rc.DATE, rc.ARTIST, rc.BODY_SERIAL, rc.MN_INTERNAL, b"XMP-SENTINEL",
           struct.pack("<I", rc.RAF_IMAGE_COUNT))


def test_a_raf_is_claimed_as_a_raw():
    assert default_dispatcher().resolve(rc.raf()).format_id == "raw"


def test_every_planted_value_goes_from_the_preview_and_the_fuji_block():
    data = rc.raf()
    out = raf.scrub(data)
    assert len(out) == len(data)
    for value in PLANTED:
        assert value in data and value not in out, value
    assert rc.RAF_GEOMETRY in out, "the Fuji block's geometry was touched"
    assert rc.RAF_SENSOR in out, "the sensor data changed"
    assert out[:148] == data[:148], "the RAF header moved"
    assert raf.residuals(out) == []


def test_the_copies_in_the_fuji_block_are_found_by_value():
    """The serial and date in the Fuji block are named by no table: only the value
    search over what the EXIF pass removed finds them."""
    data = rc.raf()
    hdr = struct.unpack_from(">I", data, 92)[0]
    assert rc.BODY_SERIAL in data[hdr:]
    out, removed = raf.scrub_with_report(data)
    assert rc.BODY_SERIAL not in out[hdr:]
    assert any("Fuji header block" in r for r in removed)


def test_raf_f1_is_a_fixed_point():
    once = raf.scrub(rc.raf())
    assert raf.scrub(once) == once


def test_an_offset_outside_the_file_is_refused():
    data = bytearray(rc.raf())
    struct.pack_into(">I", data, 100, len(data) + 10)
    with pytest.raises(ParseError, match="outside the file"):
        raf.scrub(bytes(data))


SRC = os.path.join(os.path.expanduser(os.environ.get("RAW_SAMPLES",
                                                     "~/metadata-research/raw")),
                   "fuji_xt4.RAF")
real = pytest.mark.skipif(not os.path.exists(SRC), reason="real RAF not on this machine")


@real
def test_the_real_raf_decodes_identically(tmp_path):
    rawpy = pytest.importorskip("rawpy")
    np = pytest.importorskip("numpy")
    data = open(SRC, "rb").read()
    out = raf.scrub(data)
    assert raf.residuals(out) == []

    def decode(blob, name):
        path = tmp_path / name
        path.write_bytes(blob)
        with rawpy.imread(str(path)) as r:
            return (r.raw_image.copy(), list(r.camera_whitebalance),
                    r.postprocess(use_camera_wb=True, half_size=True,
                                  no_auto_bright=True))
    a, b = decode(data, "a.RAF"), decode(out, "b.RAF")
    assert np.array_equal(a[0], b[0]), "sensor data changed"
    assert a[1] == b[1], "camera white balance changed"
    assert np.array_equal(a[2], b[2]), "the camera-white-balance render changed"


@real
@pytest.mark.skipif(shutil.which("exiftool") is None, reason="exiftool not installed")
def test_the_real_raf_loses_every_identity_value_from_the_bytes(tmp_path):
    out = raf.scrub(open(SRC, "rb").read())
    found = json.loads(subprocess.run(
        ["exiftool", "-j", "-a", "-G1", "-u", "-*Serial*", "-ImageCount",
         "-ExposureCount", "-Artist", "-Copyright", "-time:all", SRC],
        capture_output=True, text=True).stdout)[0]
    values = [str(v).strip() for k, v in found.items()
              if ":" in k and k.split(":")[0] not in ("System", "File", "ExifTool")
              and len(str(v).strip()) >= 6]
    assert values, "the original carries no identity values; nothing is proven"
    for value in values:
        assert value.encode() not in out, "an identity value is still in the bytes"
