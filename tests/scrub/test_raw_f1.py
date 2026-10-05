"""Phase 4 M17 — camera RAW F1 (TIFF family): when, where, what wrote it, previews.

M16 removed identity. F1 adds everything else a raw carries about its making --
dates in whichever IFD a maker put them, GPS, software and XMP, the dates and time
zones inside maker notes, a DNG's original-file blocks, the previews' own metadata
and a DNG semantic mask -- with nothing moving, and the same two decode checks
(sensor data, camera-white-balance render).
"""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from src.scrub import cli  # noqa: E402
from src.scrub.dispatch import default_dispatcher  # noqa: E402
from src.scrub.errors import ParseError, ScrubError, UnsupportedFormatError  # noqa: E402
from src.scrub.formats.jpeg import segments as jseg  # noqa: E402
from src.scrub.formats.raw import f1, identity  # noqa: E402
from src.scrub.formats.raw.handler import RawHandler  # noqa: E402
from src.scrub.standards import tiff_ifd as t  # noqa: E402
from tests.scrub import raw_corpus as rc  # noqa: E402

rawpy = pytest.importorskip("rawpy")
np = pytest.importorskip("numpy")
HAVE_EXIFTOOL = shutil.which("exiftool") is not None
VARIANTS = [(n, o) for n in rc.NOTES if n != "unknown" for o in ("<", ">")]
IDS = [f"{n}-{'II' if o == '<' else 'MM'}" for n, o in VARIANTS]


def _decode(data: bytes, tmp_path, name: str):
    path = tmp_path / name
    path.write_bytes(data)
    with rawpy.imread(str(path)) as r:
        return (r.raw_image.copy(),
                r.postprocess(use_camera_wb=True, no_auto_bright=True))


@pytest.mark.parametrize("note,order", VARIANTS, ids=IDS)
def test_f1_removes_every_planted_value_and_decodes_identically(note, order,
                                                                 tmp_path):
    data = rc.build(note, order)
    out = f1.scrub(data)
    assert len(out) == len(data)
    for secret in rc.SECRETS + rc.F1_SECRETS + rc.DNG_SECRETS:
        assert secret not in out, f"{secret!r} survived in a {note} file"
    if note in ("nikon", "olympus"):
        assert rc.MN_DATE[:8] not in out, "a maker-note date survived"
    assert f1.residuals(out) == []
    a, b = _decode(data, tmp_path, "a.dng"), _decode(out, tmp_path, "b.dng")
    assert np.array_equal(a[0], b[0]), "sensor data changed"
    assert np.array_equal(a[1], b[1]), "the camera-white-balance render changed"


@pytest.mark.parametrize("note,order", VARIANTS, ids=IDS)
def test_f1_is_a_fixed_point(note, order):
    once = f1.scrub(rc.build(note, order))
    assert f1.scrub(once) == once


def test_the_gps_ifd_is_gone_not_emptied():
    out = f1.scrub(rc.build())
    tree = t.parse(out, strict=True)
    assert tree.ifd("GPSIFD") is None
    assert all(i.get(f1.TAG_GPS) is None for i in tree.ifds)


def test_the_preview_keeps_its_picture_and_colour_profile_and_loses_the_rest():
    """The iPhone DNG shape: a preview with its own EXIF (GPS there), a colour
    profile, and a secondary image after EOI. The picture must decode to the same
    pixels, and the profile must stay -- dropping it recolours every viewer that
    shows the preview."""
    import io

    from PIL import Image
    data = rc.build()
    tree = t.parse(data, strict=True)
    ifd = tree.ifd("SubIFD1")
    at, length = ifd.get(0x0111).raw_value, ifd.get(0x0117).raw_value
    out = f1.scrub(data)
    before, after = data[at:at + length], out[at:at + length]
    kinds = [s.kind for s in jseg.walk(after).segments]
    assert "app1_exif" not in kinds and "app2_icc" in kinds
    assert jseg.walk(after).trailer.strip(b"\x00") == b""
    pixels = [np.asarray(Image.open(io.BytesIO(x)).convert("RGB")) for x in (before,
                                                                             after)]
    assert np.array_equal(*pixels)


def test_the_semantic_mask_is_dropped_and_the_raw_stays_first():
    out = f1.scrub(rc.build())
    tree = t.parse(out, strict=True)
    assert all(i.get(f1.TAG_SEMANTIC_NAME) is None for i in tree.ifds)
    assert tree.ifd("IFD0").get(t.TAG_SUBIFDS).count == 2
    assert tree.ifd("SubIFD0").get(0x0106).raw_value & 0xFFFF == 32803


def test_0xc634_is_removed_in_a_dng_and_kept_everywhere_else():
    """Adobe's private block in a DNG carries the original maker note; in a Sony
    ARW the same tag points at the enciphered white balance the decoder needs."""
    assert rc.PRIVATE not in f1.scrub(rc.build())
    other = f1.scrub(rc.build(dng=False))
    assert rc.PRIVATE in other and rc.DATE not in other


def test_the_image_data_check_refuses_a_scrub_that_touched_the_sensor(monkeypatch):
    """The tier's own promise, checked on the bytes: sabotage the identity pass so
    it also flips one byte of sensor data, and F1 must refuse rather than return."""
    data = rc.build()
    raw_at = t.parse(data, strict=True).ifd("SubIFD0").get(0x0111).raw_value
    real = identity.blank_identity

    def sabotage(buf, *a, **k):
        removed = real(buf, *a, **k)
        buf[raw_at + 7] ^= 0xFF
        return removed
    monkeypatch.setattr(identity, "blank_identity", sabotage)
    with pytest.raises(ScrubError, match="altered image data"):
        f1.scrub(data)


def test_a_maker_note_in_an_unmodelled_layout_is_refused():
    with pytest.raises(ParseError, match="unmodelled"):
        f1.scrub(rc.build("unknown"))


def test_panasonic_is_claimed_so_that_it_is_refused_by_name():
    rw2 = rc.build("canon", magic=0x0055)
    assert default_dispatcher().resolve(rw2).format_id == "raw"
    with pytest.raises(ParseError, match="RW2"):
        f1.scrub(rw2)


def test_a_plain_tiff_picture_is_not_claimed_as_a_raw(tmp_path):
    from PIL import Image
    path = tmp_path / "plain.tif"
    Image.new("RGB", (8, 8), (1, 2, 3)).save(path)
    data = path.read_bytes()
    assert not RawHandler().claims(data)
    with pytest.raises(UnsupportedFormatError):
        default_dispatcher().resolve(data)


def test_malformed_input_fails_closed_with_a_parse_error():
    data = bytearray(rc.build())
    exif = t.parse(bytes(data), strict=True).ifd("ExifIFD")
    struct.pack_into("<I", data, exif.offset + 2 + 8, 0x7FFFFFF0)   # wild pointer
    with pytest.raises(ParseError):
        f1.scrub(bytes(data))


def test_the_cli_scrubs_a_raw_and_reports_what_went(tmp_path, capsys):
    src, out = tmp_path / "in.dng", tmp_path / "out.dng"
    src.write_bytes(rc.build("nikon"))
    assert cli.main([str(src), str(out), "--no-verify"]) == 0
    report = capsys.readouterr().out
    assert "GPS" in report and "DateTimeOriginal" in report and "Nikon" in report
    assert out.stat().st_size == src.stat().st_size


# --------------------------------------------------------------------------- #
# The real files, where they exist
# --------------------------------------------------------------------------- #
RAW_DIR = os.path.expanduser(os.environ.get("RAW_SAMPLES", "~/metadata-research/raw"))
REAL = ["apple_iphone12pro.DNG", "canon_80d.CR2", "nikon_d750.NEF",
        "sony_a7m3.ARW", "olympus_em10m4.ORF"]


def _real(name: str) -> str:
    path = os.path.join(RAW_DIR, name)
    if not os.path.exists(path):
        pytest.skip(f"{name} not on this machine (see tests/corpus/raw/manifest.txt)")
    return path


def _dates_and_places(path: str) -> dict:
    import json
    out = subprocess.run(["exiftool", "-j", "-a", "-G1", "-ee", "-time:all",
                          "-gps:all", path], capture_output=True, text=True).stdout
    found = json.loads(out)[0]
    return {k: str(v) for k, v in found.items()
            if ":" in k and k.split(":")[0] not in ("System", "File", "ExifTool",
                                                     "Composite")}


@pytest.mark.skipif(not HAVE_EXIFTOOL, reason="exiftool not installed")
@pytest.mark.parametrize("name", REAL)
def test_real_raw_files_lose_dates_and_places_and_decode_identically(name, tmp_path):
    """No date or GPS value survives unchanged, and nothing date-shaped is left.
    What ExifTool still prints are blanked enumerations it decodes from zero (a
    time zone of +00:00, a city of "n/a", a date-display preference)."""
    import re
    src = _real(name)
    data = open(src, "rb").read()
    out = f1.scrub(data)
    assert f1.residuals(out) == []
    dst = tmp_path / name
    dst.write_bytes(out)
    before, after = _dates_and_places(src), _dates_and_places(str(dst))
    survived = {k for k, v in after.items()
                if before.get(k) == v and v.strip(" 0:.+-") and not k.endswith(
                    ("TimeZone", "TimeZoneCity", "DaylightSavings",
                     "DateDisplayFormat"))}
    assert not survived, survived
    assert not [k for k, v in after.items() if re.search(r"\d{4}:\d{2}:\d{2}", v)
                and v.strip(" 0:.")], after
    assert not [k for k in after if k.startswith("GPS")], after
    a, b = _decode(data, tmp_path, "a" + name), _decode(out, tmp_path, "b" + name)
    assert np.array_equal(a[0], b[0]), "sensor data changed"
    assert np.array_equal(a[1], b[1]), "the camera-white-balance render changed"


@pytest.mark.parametrize("name", ["canon_r6m3.CR3", "fuji_xt4.RAF"])
def test_cr3_and_raf_are_not_claimed_yet(name):
    with pytest.raises(UnsupportedFormatError):
        default_dispatcher().resolve(open(_real(name), "rb").read())


def test_real_rw2_is_refused_by_name():
    with pytest.raises(ParseError, match="RW2"):
        f1.scrub(open(_real("panasonic_g9.RW2"), "rb").read())
