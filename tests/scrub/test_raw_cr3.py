"""Phase 4 M18 — Canon CR3: the MP4 container around a still photo, nothing moves.

Two findings shape these tests. CR3 keeps its metadata in four little TIFF files
inside Canon's own box (`CMT1`..`CMT4`), so the TIFF field tables apply block by
block. And the timed-metadata track (`CTMD`) carries the shot count beside the
ColorData LibRaw reads a CR3's white balance from: zeroed whole, the camera white
balance became [0, 1, 0, 0]. So CTMD is edited record by record.

CI runs on a hand-built CR3 (structure); the real R6 Mark III file runs the decode
checks where it exists -- no hand-built file can carry Canon's raw codec.
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
from src.scrub.formats.mp4.handler import Mp4Handler  # noqa: E402
from src.scrub.formats.raw import cr3  # noqa: E402
from src.scrub.standards import isobmff as iso  # noqa: E402
from tests.scrub import raw_corpus as rc  # noqa: E402

PLANTED = (rc.DATE, rc.ARTIST, rc.COPYRIGHT, rc.OWNER, rc.BODY_SERIAL,
           rc.LENS_SERIAL, rc.MN_INTERNAL, rc.GPS_DATE, rc.PREVIEW_EXIF,
           rc.CTMD_TIME, rc.UNIQUE_ID[:16], b"XMP-SENTINEL",
           struct.pack("<I", rc.SHUTTER_COUNT))


def test_a_cr3_is_a_raw_and_not_a_movie():
    data = rc.cr3()
    assert not Mp4Handler().claims(data)
    assert default_dispatcher().resolve(data).format_id == "raw"


def test_every_planted_value_goes_and_what_the_decoder_needs_stays():
    data = rc.cr3()
    out = cr3.scrub(data)
    assert len(out) == len(data)
    for value in PLANTED:
        assert value in data and value not in out, value
    assert rc.COLOR_DATA in out, "ColorData is the white balance LibRaw reads"
    assert rc.CR3_RAW in out, "the sensor data moved or changed"
    assert cr3.residuals(out) == []


def test_header_times_are_zeroed_and_the_xmp_packet_is_empty_but_valid():
    out = cr3.scrub(rc.cr3())
    moov_h = next(b for b in iso.scan(out) if b.type == b"moov")
    for box in iso.parse(out[moov_h.offset:moov_h.end])[0].walk():
        if box.type in iso.TIMESTAMP_BOXES:
            assert not box.payload[4:12].strip(b"\x00"), box.type
    assert b"<x:xmpmeta" in out and b'<?xpacket end="w"?>' in out


def test_cr3_f1_is_a_fixed_point():
    once = cr3.scrub(rc.cr3())
    assert cr3.scrub(once) == once


def test_residuals_see_a_surviving_timestamp():
    data = bytearray(cr3.scrub(rc.cr3()))
    at = data.rfind(struct.pack("<IH", 24, 1))    # CTMD sits at the end of mdat
    data[at + 12:at + 24] = rc.CTMD_TIME
    assert any("CTMD timestamp" in r for r in cr3.residuals(bytes(data)))


def test_a_ctmd_record_that_overruns_its_sample_is_refused():
    data = bytearray(rc.cr3())
    at = data.rfind(struct.pack("<IH", 24, 1))    # CTMD sits at the end of mdat
    struct.pack_into("<I", data, at, 1 << 20)
    with pytest.raises(ParseError, match="CTMD"):
        cr3.scrub(bytes(data))


# --------------------------------------------------------------------------- #
# The real file
# --------------------------------------------------------------------------- #
SRC = os.path.join(os.path.expanduser(os.environ.get("RAW_SAMPLES",
                                                     "~/metadata-research/raw")),
                   "canon_r6m3.CR3")
real = pytest.mark.skipif(not os.path.exists(SRC), reason="real CR3 not on this machine")


@real
def test_the_real_cr3_decodes_identically():
    rawpy = pytest.importorskip("rawpy")
    np = pytest.importorskip("numpy")
    data = open(SRC, "rb").read()
    out = cr3.scrub(data)
    assert cr3.residuals(out) == []

    def decode(blob, name):
        path = os.path.join(os.environ.get("TMPDIR", "/tmp"), name)
        with open(path, "wb") as f:
            f.write(blob)
        try:
            with rawpy.imread(path) as r:
                return (r.raw_image.copy(), list(r.camera_whitebalance),
                        r.postprocess(use_camera_wb=True, half_size=True,
                                      no_auto_bright=True))
        finally:
            os.unlink(path)
    a, b = decode(data, "cr3_a.CR3"), decode(out, "cr3_b.CR3")
    assert np.array_equal(a[0], b[0]), "sensor data changed"
    assert a[1] == b[1], "camera white balance changed"
    assert np.array_equal(a[2], b[2]), "the camera-white-balance render changed"


@real
@pytest.mark.skipif(shutil.which("exiftool") is None, reason="exiftool not installed")
def test_the_real_cr3_loses_its_counters_serials_and_dates(tmp_path):
    out = tmp_path / "out.CR3"
    out.write_bytes(cr3.scrub(open(SRC, "rb").read()))

    def read(path):
        found = json.loads(subprocess.run(
            ["exiftool", "-j", "-a", "-G1", "-u", "-ee", "-ImageCount",
             "-ImageUniqueID", "-*Serial*", "-OwnerName", "-Artist", "-time:all",
             "-gps:all", str(path)], capture_output=True, text=True).stdout)[0]
        return {k: str(v) for k, v in found.items() if ":" in k
                and k.split(":")[0] not in ("System", "File", "ExifTool", "Composite")}
    before, after = read(SRC), read(out)
    survived = {k for k, v in after.items() if before.get(k) == v
                and v.strip(" 0:.+-") and "TimeScale" not in k and "Time" != k[-4:]}
    for key in ("Canon:ImageCount", "Track4:ImageCount", "Canon:ImageUniqueID",
                "Canon:InternalSerialNumber", "ExifIFD:SerialNumber"):
        assert key not in survived, key
    assert not [k for k in after if k.startswith(("GPS:", "GPS"))
                and after[k].strip(" 0:.")], after
