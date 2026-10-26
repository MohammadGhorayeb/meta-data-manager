"""BMP at F1 (`docs/bmp_plan.md`): every locus gone, every pixel unchanged."""
from __future__ import annotations

import ast
import glob
import io
import os
import shutil
import struct
import subprocess
import sys
import zipfile

import pytest
from PIL import Image

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from src.scrub import cli  # noqa: E402
from src.scrub.dispatch import default_dispatcher  # noqa: E402
from src.scrub.errors import ParseError  # noqa: E402
from src.scrub.formats.bmp import f1  # noqa: E402
from src.scrub.formats.bmp.handler import BmpHandler  # noqa: E402
from src.scrub.standards import icc  # noqa: E402
from tests.scrub import bmp_corpus as bc  # noqa: E402
from tests.scrub import tiff_corpus as tfc  # noqa: E402

# (label, build() layout): every header version, depth and compression handled.
LAYOUTS = [
    ("v5-custom-icc", dict(header="v5", icc="custom")),
    ("v5-standard-icc", dict(header="v5", icc="standard")),
    ("v5-icc-before-pixels", dict(header="v5", icc="custom", profile_at="gap")),
    ("v5-linked", dict(header="v5", icc="linked")),
    ("v5-srgb-32-bitfields", dict(header="v5", bpp=32, compression="bitfields",
                                  icc="none")),
    ("v5-rle8-icc", dict(header="v5", bpp=8, compression="rle8", icc="custom")),
    ("v5-top-down", dict(header="v5", icc="none", top_down=True)),
    ("v4-calibrated", dict(header="v4", icc="calibrated")),
    ("v4-srgb-16-bitfields", dict(header="v4", bpp=16, compression="bitfields",
                                  icc="none")),
    ("v3-32-bitfields", dict(header="v3", bpp=32, compression="bitfields")),
    ("info-1", dict(header="info", bpp=1)),
    ("info-4", dict(header="info", bpp=4)),
    ("info-8", dict(header="info", bpp=8)),
    ("info-8-16-colours", dict(header="info", bpp=8, colors=16)),
    ("info-16", dict(header="info", bpp=16)),
    ("info-16-bitfields", dict(header="info", bpp=16, compression="bitfields")),
    ("info-24", dict(header="info", bpp=24)),
    ("info-32", dict(header="info", bpp=32)),
    ("info-rle8", dict(header="info", bpp=8, compression="rle8")),
    ("info-rle4", dict(header="info", bpp=4, compression="rle4")),
    ("core-1", dict(header="core", bpp=1)),
    ("core-8", dict(header="core", bpp=8)),
    ("core-24", dict(header="core", bpp=24)),
]
IDS = [label for label, _ in LAYOUTS]


def _rgba(data: bytes) -> tuple:
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        return im.size, im.convert("RGBA").tobytes()


# Every planted value, wherever the corpus put it: its sentinels, the linked path's
# user folder, the personal profile's creator code.
IDENTITY = (b"SENTINEL", b"sentinel", b"SNTL")


def test_the_corpus_imports_nothing_from_the_scrubber():
    tree = ast.parse(open(os.path.join(os.path.dirname(__file__), "bmp_corpus.py"),
                          encoding="utf-8").read())
    imported = [n.module for n in ast.walk(tree)
                if isinstance(n, ast.ImportFrom) and n.module]
    assert not [m for m in imported if m.split(".")[0] in ("src", "scrub")]


@pytest.mark.parametrize("label,layout", LAYOUTS, ids=IDS)
def test_a_decoder_reads_the_corpus_as_it_was_meant(label, layout):
    data = bc.build(1, **layout)
    for value in bc.planted(1):
        assert value in data
    if layout.get("bpp", 24) == 16:
        return                     # a decoder widens 5- and 6-bit channels its own way
    with Image.open(io.BytesIO(data)) as im:
        got = list(im.convert("RGB").get_flattened_data())
    assert got == bc.expected_rgba(layout.get("bpp", 24),
                                   layout.get("compression", "rgb"),
                                   colors=layout.get("colors"))


@pytest.mark.parametrize("label,layout", LAYOUTS, ids=IDS)
def test_every_planted_value_is_gone_and_the_picture_is_unchanged(label, layout):
    data = bc.build(1, **layout)
    out = f1.scrub(data)
    for value in IDENTITY:
        assert value not in out, f"{value!r} survived"
    assert _rgba(out) == _rgba(data)
    assert f1.residuals(out) == []


@pytest.mark.parametrize("label,layout", LAYOUTS, ids=IDS)
def test_two_files_that_differ_only_in_metadata_come_out_identical(label, layout):
    assert f1.scrub(bc.build(1, **layout)) == f1.scrub(bc.build(2, **layout))


@pytest.mark.parametrize("label,layout", LAYOUTS, ids=IDS)
def test_scrubbing_twice_changes_nothing(label, layout):
    once = f1.scrub(bc.build(1, **layout))
    assert f1.scrub(once) == once


def test_what_the_rebuilt_header_says():
    out = f1.scrub(bc.build(1, header="v5", icc="custom"))
    size, r1, r2, off = struct.unpack_from("<IHHI", out, 2)
    assert size == len(out) and (r1, r2) == (0, 0)
    stride = ((24 * bc.W + 31) // 32) * 4
    assert off == 14 + 124                              # no palette, no gap
    simg, xppm, yppm, used, important = struct.unpack_from("<IiiII", out, 34)
    assert (simg, xppm, yppm, used, important) == (stride * bc.H, 0, 0, 0, 0)
    prof_at, prof_size, reserved = struct.unpack_from("<III", out, 14 + 112)
    assert 14 + prof_at == off + stride * bc.H and reserved == 0
    assert out[14 + prof_at:] == icc.sanitize(bc.custom_icc(1))
    assert out[74:122] == bytes(48)                     # sRGB-unused fields zeroed


@pytest.mark.parametrize("label,layout", [
    (lbl, lay) for lbl, lay in LAYOUTS if lay.get("icc") not in ("custom", "linked")],
    ids=[lbl for lbl, lay in LAYOUTS if lay.get("icc") not in ("custom", "linked")])
def test_a_bitmap_with_nothing_to_remove_is_untouched(label, layout):
    """Pixels, masks, palette colours, a published profile: byte for byte -- and a
    planted file comes out as the same file without its plants."""
    data = bc.build(1, planted=False, **layout)
    if layout.get("profile_at") == "gap":
        data = f1.scrub(data)                           # the profile moves once
    assert f1.scrub(data) == data
    assert f1.scrub(bc.build(1, **layout)) == data


def test_a_published_profile_stays_and_a_personal_one_is_sanitized():
    out = f1.scrub(bc.build(1, header="v5", icc="standard"))
    assert out.endswith(tfc.STANDARD_ICC)
    out = f1.scrub(bc.build(1, header="v5", icc="custom"))
    kept = out[-len(bc.custom_icc(1)):]
    assert kept == icc.sanitize(bc.custom_icc(1))
    table = icc.tags(bc.custom_icc(1))
    end = 128 + 4 + 12 * len(table)
    assert kept[128:end] == bc.custom_icc(1)[128:end]


def test_a_linked_profile_loses_its_path_and_becomes_srgb():
    data = bc.build(1, header="v5", icc="linked")
    assert b"\\Users\\sentinel01\\" in data
    out = f1.scrub(data)
    assert out[70:74] == bc.CS_SRGB
    assert struct.unpack_from("<II", out, 14 + 112) == (0, 0)
    assert b"Users" not in out and b".icm" not in out
    report = f1.scrub_with_report(data)[1]
    assert any("linked colour profile" in r for r in report)


@pytest.mark.skipif(shutil.which("exiftool") is None, reason="exiftool not installed")
def test_exiftool_reads_the_linked_path_before_and_not_after(tmp_path):
    src, out = tmp_path / "a.bmp", tmp_path / "b.bmp"
    src.write_bytes(bc.build(1, header="v5", icc="linked"))
    cli.scrub_file(str(src), str(out), "F1")

    def read(p):
        return subprocess.run(["exiftool", "-s", "-LinkedProfileName",
                               "-ColorSpace", str(p)], capture_output=True,
                              text=True).stdout
    assert "sentinel01" in read(src)
    after = read(out)
    assert "LinkedProfileName" not in after and "sRGB" in after


@pytest.mark.skipif(shutil.which("exiftool") is None, reason="exiftool not installed")
def test_the_report_cross_check_counts_the_linked_path(tmp_path):
    """ExifTool files `LinkedProfileName` under `File`, the group the cross-check
    otherwise treats as derived: it is stored in the file, so it is counted and the
    removed path is searched for."""
    src, out = tmp_path / "a.bmp", tmp_path / "b.bmp"
    src.write_bytes(bc.build(1, header="v5", icc="linked"))
    report = cli.scrub_file_reported(str(src), str(out), "F1",
                                     verify_with_exiftool=True)[1]
    check = report.check
    assert check and not check.error and not check.leaked
    assert [k for k in check.before if k.endswith("LinkedProfileName")]
    assert not [k for k in check.after if k.endswith("LinkedProfileName")]


@pytest.mark.parametrize("compression", ["rle8", "rle4"])
def test_an_rle_stream_ends_at_its_end_of_bitmap_code_and_its_pads_are_zero(
        compression):
    layout = dict(header="info", bpp=8 if compression == "rle8" else 4,
                  compression=compression)
    planted, clean = bc.build(1, **layout), bc.build(1, planted=False, **layout)
    out = f1.scrub(planted)
    off = struct.unpack_from("<I", out, 10)[0]
    assert out.endswith(b"\0\1") and b"RLE-SLACK" not in out
    assert struct.unpack_from("<I", out, 34)[0] == len(out) - off
    # The unplanted build pads with zeros: the same stream, byte for byte.
    assert out[off:] == clean[struct.unpack_from("<I", clean, 10)[0]:]
    assert out[off:] != planted[struct.unpack_from("<I", planted, 10)[0]:][:len(out)
                                                                           - off]


def test_an_odd_rle4_literal_is_left_exactly_as_it_was():
    """Pillow reads an odd RLE4 literal one byte short and takes its last byte and
    pad as the next code: changing those would change what Pillow draws."""
    pal = b"".join(bytes((i * 16, i * 8, 255 - i * 16, 0)) for i in range(16))
    rows = []
    for _ in range(2):                       # literal of 5 (odd), then a run of 3
        rows.append(bytes((0, 5, 0x12, 0x34, 0x5F, 0xEE, 3, 0x77)) + b"\0\0")
    stream = b"".join(rows)[:-2] + b"\0\1"
    info = struct.pack("<IiiHHIIiiII", 40, 8, 2, 1, 4, 2, len(stream), 0, 0, 0, 0)
    off = 14 + 40 + len(pal)
    data = b"BM" + struct.pack("<IHHI", off + len(stream), 0, 0, off) + info + pal \
        + stream
    assert f1.scrub(data) == data
    with pytest.raises(ValueError):          # and Pillow cannot draw it either way
        _rgba(data)


def test_a_top_down_bitmap_stays_top_down():
    out = f1.scrub(bc.build(1, header="v5", icc="none", top_down=True))
    assert struct.unpack_from("<i", out, 22)[0] == -bc.H


def test_the_alpha_in_a_32_bit_bitmap_is_kept():
    """32-bit BI_RGB's fourth byte is 'unused' by the format and alpha to most
    writers (Pillow, ffmpeg): pixel data, kept."""
    data = bc.build(1, header="info", bpp=32, planted=False)
    off = struct.unpack_from("<I", data, 10)[0]
    assert set(data[off + 3::4]) == {255, 96}
    assert f1.scrub(data) == data


def test_a_header_with_no_pixels_is_cleaned_as_a_template():
    """Office ships `patternHeader.bmp` -- the header it stamps onto each pattern
    fill -- in OAPatterns.zip: a file header, an info header, a palette, and no
    pixels. Every byte is accounted for, so it is cleaned rather than refused, and
    its sizes still describe the bitmap it heads."""
    full = bc.build(1, header="info", bpp=1, planted=False)
    off = struct.unpack_from("<I", full, 10)[0]
    template = bytearray(full[:off])
    struct.pack_into("<iiII", template, 38, 2953, 2953, 2, 2)     # as Office wrote
    out = f1.scrub(bytes(template))
    assert out == full[:off]
    assert struct.unpack_from("<I", out, 2)[0] == len(full)
    assert f1.residuals(out) == []
    assert f1.describe(out)["Pixels"].startswith("none")


@pytest.mark.parametrize("cut", [0.3, 0.6, 0.95])
def test_a_truncated_bitmap_is_refused(cut):
    data = bc.build(1, header="info", bpp=24, planted=False)
    off = struct.unpack_from("<I", data, 10)[0]
    with pytest.raises(ParseError):
        f1.scrub(data[:off + int((len(data) - off) * cut)])


def _patched(data: bytes, at: int, fmt: str, *values) -> bytes:
    buf = bytearray(data)
    struct.pack_into(fmt, buf, at, *values)
    return bytes(buf)


REFUSALS = {
    "os2-2x-header": lambda: _patched(bc.build(planted=False, header="info"), 14,
                                      "<I", 64),
    "jpeg-compression": lambda: _patched(bc.build(planted=False, header="info"), 30,
                                         "<I", 4),
    "png-compression": lambda: _patched(bc.build(planted=False, header="info"), 30,
                                        "<I", 5),
    "cmyk": lambda: _patched(bc.build(planted=False, header="info"), 30, "<I", 11),
    "2-bit": lambda: _patched(bc.build(planted=False, header="info"), 28, "<H", 2),
    "two-planes": lambda: _patched(bc.build(planted=False, header="info"), 26, "<H",
                                   2),
    "top-down-rle": lambda: _patched(
        bc.build(planted=False, header="info", bpp=8, compression="rle8"), 22, "<i",
        -bc.H),
    "rle-without-end": lambda: bc.build(planted=False, header="info", bpp=8,
                                        compression="rle8")[:-2],
    "rle-outside-the-picture": lambda: _patched(
        bc.build(planted=False, header="info", bpp=8, compression="rle8"), 18, "<i",
        4),
    "colour-the-palette-lacks": lambda: _patched(
        bc.build(planted=False, header="info", bpp=8), 46, "<I", 4),
    "v4-naming-a-profile": lambda: _patched(
        bc.build(planted=False, header="v4", icc="none"), 70, "4s", bc.CS_EMBEDDED),
    "unknown-colour-space": lambda: _patched(
        bc.build(planted=False, header="v5", icc="none"), 70, "4s", b"XXXX"),
    "profile-inside-the-pixels": lambda: _patched(
        bc.build(planted=False, header="v5", icc="custom"), 14 + 112, "<I",
        124 + 40),
    "embedded-profile-not-icc": lambda: _patched(
        bc.build(planted=False, header="v5", icc="linked"), 70, "4s", bc.CS_EMBEDDED),
}


@pytest.mark.parametrize("name", sorted(REFUSALS))
def test_what_was_not_measured_is_refused_by_name(name):
    with pytest.raises(ParseError):
        f1.scrub(REFUSALS[name]())


def test_the_dispatcher_and_the_cli(tmp_path):
    data = bc.build(1)
    assert isinstance(default_dispatcher().resolve(data), BmpHandler)
    src, out = tmp_path / "a.bmp", tmp_path / "b.bmp"
    src.write_bytes(data)
    cli.scrub_file(str(src), str(out), "F1")
    assert out.read_bytes() == f1.scrub(data)


def test_a_text_file_that_begins_bm_is_not_a_bitmap():
    assert not BmpHandler().claims(b"BMW and Mercedes sold more cars this year.\n")


def test_a_zip_holding_bitmaps_is_cleaned_member_by_member():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("pattern.bmp", bc.build(1, header="v5", icc="linked"))
        z.writestr("icons/pal.bmp", bc.build(1, header="info", bpp=8))
    data = buf.getvalue()
    out = default_dispatcher().resolve(data).scrub(data, "F1")
    with zipfile.ZipFile(io.BytesIO(out)) as z:
        for name in z.namelist():
            member = z.read(name)
            assert b"SENTINEL" not in member and b"sentinel" not in member
            assert f1.residuals(member) == []


SURVEY = os.path.expanduser(os.environ.get("BMP_SAMPLES",
                                           "~/metadata-research/step2/bmp"))
SURVEY_FILES = sorted(glob.glob(os.path.join(SURVEY, "*", "*.bmp")))


@pytest.mark.skipif(not SURVEY_FILES, reason="BMP survey files not on this machine "
                                             "(docs/bmp_plan.md §0)")
def test_the_survey_bitmaps_scrub_clean():
    """Office's 55 pattern fills (twice), Pillow, ffmpeg, sips, ImageMagick and the
    planted files: cleaned, no residuals, the picture unchanged wherever Pillow
    can draw it."""
    for path in SURVEY_FILES:
        data = open(path, "rb").read()
        out = f1.scrub(data)
        assert f1.residuals(out) == [], path
        assert b"SENTINEL" not in out and b"sentinel" not in out, path
        try:
            before = _rgba(data)
        except OSError:
            continue          # a header template, or 4-4-4 bitfields Pillow lacks
        assert _rgba(out) == before, path


VENDOR_ARCHIVES = [p for p in (
    "/Applications/Keynote.app/Contents/Resources/OAPatterns.zip",
    "/System/Library/PrivateFrameworks/OfficeImport.framework/Versions/A/Resources/"
    "OAPatterns.zip") if os.path.exists(p)]


@pytest.mark.skipif(not VENDOR_ARCHIVES, reason="Office's pattern archives are on Macs")
def test_the_vendor_pattern_archives_are_cleaned():
    """The two refusals the 2026-10-26 sweep left besides GPU code: 55 BMPs each."""
    for path in VENDOR_ARCHIVES:
        data = open(path, "rb").read()
        handler = default_dispatcher().resolve(data)
        out = handler.scrub(data, "F1")
        assert handler.verify(out, "F1") == []
        with zipfile.ZipFile(io.BytesIO(out)) as z, zipfile.ZipFile(path) as orig:
            assert sorted(z.namelist()) == sorted(orig.namelist())
            for name in z.namelist():
                a, b = orig.read(name), z.read(name)
                if len(a) > struct.unpack_from("<I", a, 10)[0]:
                    assert _rgba(a) == _rgba(b), name


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("sips"),
                    reason="sips (macOS ImageIO) is the second decoder, on Macs")
@pytest.mark.parametrize("label,layout", [
    (lbl, lay) for lbl, lay in LAYOUTS if lay.get("icc") != "linked"],
    ids=[lbl for lbl, lay in LAYOUTS if lay.get("icc") != "linked"])
def test_macos_imageio_draws_the_same_picture_after(label, layout, tmp_path):
    """A decoder sharing no code with Pillow. ImageIO refuses any V4/V5 bitmap whose
    colour space is an embedded or linked profile, and a V4 16-bit one (measured:
    §1); a bitmap it refuses before cleaning it must refuse after, too."""
    data = bc.build(1, **layout)
    pngs = []
    for tag, blob in (("in", data), ("out", f1.scrub(data))):
        src, png = tmp_path / f"{tag}.bmp", tmp_path / f"{tag}.png"
        src.write_bytes(blob)
        done = subprocess.run(["sips", "-s", "format", "png", str(src), "--out",
                               str(png)], capture_output=True)
        pngs.append(_rgba(png.read_bytes()) if done.returncode == 0 else None)
    assert pngs[0] == pngs[1]


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("sips"),
                    reason="sips (macOS ImageIO) is the second decoder, on Macs")
def test_macos_imageio_opens_a_linked_profile_bitmap_only_after_cleaning(tmp_path):
    """Measured, and stated so a change in macOS is noticed: ImageIO cannot open a
    bitmap that links a profile; the cleaned one says sRGB, and opens."""
    data = bc.build(1, header="v5", icc="linked")
    for tag, blob, expect in (("in", data, False), ("out", f1.scrub(data), True)):
        src = tmp_path / f"{tag}.bmp"
        src.write_bytes(blob)
        got = subprocess.run(["sips", "-g", "pixelWidth", str(src)],
                             capture_output=True, text=True).stdout
        assert (f"pixelWidth: {bc.W}" in got) is expect
