"""Phase 6 M1 -- plain TIFF at F1: every locus gone, every page's pixels unchanged.

The hand-built corpus (`tiff_corpus.py`) plants every locus the survey measured;
Pillow is the independent decoder and ExifTool the independent reader. The survey's
real files (sips, Pillow, libtiff, ExifTool-tagged) run where they exist.
"""
from __future__ import annotations

import ast
import glob
import os
import shutil
import subprocess
import sys
import tempfile

import pytest
from PIL import Image, ImageChops

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from src.scrub import cli  # noqa: E402
from src.scrub.dispatch import default_dispatcher  # noqa: E402
from src.scrub.errors import ParseError, UnsupportedFormatError  # noqa: E402
from src.scrub.formats.tiff import f1  # noqa: E402
from src.scrub.formats.tiff.handler import TiffHandler  # noqa: E402
from src.scrub.standards import icc  # noqa: E402
from src.scrub.standards import tiff_ifd as t  # noqa: E402
from tests.scrub import tiff_corpus as tc  # noqa: E402

HAVE_EXIFTOOL = shutil.which("exiftool") is not None


def _pages(data: bytes, n: int) -> list[Image.Image]:
    with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as f:
        f.write(data)
        path = f.name
    try:
        out = []
        with Image.open(path) as im:
            for k in range(n):
                im.seek(k)
                out.append(im.convert("RGB").copy())
        return out
    finally:
        os.unlink(path)


def _same_pixels(a: bytes, b: bytes, n: int) -> bool:
    return all(ImageChops.difference(x, y).getbbox() is None
               for x, y in zip(_pages(a, n), _pages(b, n), strict=True))


def _icc(data: bytes) -> bytes:
    e = t.parse(data, strict=True).ifd("IFD0").get(f1.TAG_ICC)
    return t.value_bytes(data, e)


# --------------------------------------------------------------------------- #
# The corpus
# --------------------------------------------------------------------------- #
def test_the_corpus_imports_nothing_from_the_scrubber():
    tree = ast.parse(open(os.path.join(os.path.dirname(__file__), "tiff_corpus.py"),
                          encoding="utf-8").read())
    imported = [n.module for n in ast.walk(tree)
                if isinstance(n, ast.ImportFrom) and n.module]
    imported += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
                 for a in n.names]
    assert not [m for m in imported if m.split(".")[0] in ("src", "scrub")]


@pytest.mark.skipif(not HAVE_EXIFTOOL, reason="exiftool not installed")
def test_an_independent_reader_finds_what_the_corpus_plants(tmp_path):
    path = tmp_path / "t.tif"
    path.write_bytes(tc.build())
    out = subprocess.run(["exiftool", "-a", "-G1", "-s", str(path)],
                         capture_output=True, text=True).stdout
    for value in (tc.document_path(), tc.secret("ARTIST"), tc.secret("SERIAL"),
                  tc.secret("HOST"), tc.secret("PAGE1")):
        assert value.decode() in out
    assert "GPSLatitude" in out and "SENTINEL calibrated display" in out


# --------------------------------------------------------------------------- #
# F1
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("order", ["<", ">"], ids=["II", "MM"])
def test_every_planted_value_is_gone_and_every_page_is_unchanged(order):
    data = tc.build(order=order)
    out = f1.scrub(data)
    for value in tc.planted():
        assert value not in out, f"{value!r} survived"
    assert len(out) == len(data)
    assert _same_pixels(data, out, 2)
    assert f1.residuals(out) == []


def test_a_published_colour_profile_stays_byte_for_byte():
    """Zeroing a standard profile's header would turn the commonest bytes in
    imaging into a mark of this tool."""
    data = tc.build(icc="standard")
    out = f1.scrub(data)
    assert _icc(out) == tc.STANDARD_ICC


def test_a_personal_colour_profile_keeps_its_colours_and_loses_its_maker():
    data = tc.build(icc="custom")
    profile = _icc(f1.scrub(data))
    assert profile != tc.CUSTOM_ICC and len(profile) == len(tc.CUSTOM_ICC)
    assert profile[128:] == tc.CUSTOM_ICC[128:], "the colour tables changed"
    header = icc.parse_header(profile)
    assert not header.creator.strip(b"\0") and not header.manufacturer.strip(b"\0")
    assert profile[84:100] == icc.compute_profile_id(profile), "stale profile ID"


def test_the_thumbnail_loses_its_own_metadata_and_still_decodes():
    data = tc.build()
    out = f1.scrub(data)
    assert tc.secret("THUMB") not in out
    tree = t.parse(out, strict=True)
    thumb = next(i for i in tree.ifds if i.get(0x0201) is not None)
    at, n = thumb.base + thumb.get(0x0201).raw_value, thumb.get(0x0202).raw_value
    import io
    Image.open(io.BytesIO(out[at:at + n])).load()


def test_two_tiffs_that_differ_only_in_metadata_come_out_identical():
    assert f1.scrub(tc.build(1)) == f1.scrub(tc.build(2))


def test_scrubbing_twice_changes_nothing():
    once = f1.scrub(tc.build())
    assert f1.scrub(once) == once


def test_the_report_names_what_was_there():
    found = f1.describe(tc.build())
    assert found["IFD0:DocumentName"] == tc.document_path().decode()
    assert "GPS" in found and "ExifIFD:MakerNote" in found


# --------------------------------------------------------------------------- #
# Fail closed, and routing
# --------------------------------------------------------------------------- #
def test_bigtiff_is_refused_by_name():
    with pytest.raises(UnsupportedFormatError, match="BigTIFF"):
        f1.scrub(b"II+\x00\x08\x00\x00\x00" + bytes(64))


@pytest.mark.parametrize("cut", [0.05, 0.4, 0.9])
def test_a_truncated_tiff_is_refused(cut):
    data = tc.build()
    with pytest.raises(ParseError):
        f1.scrub(data[:int(len(data) * cut)])


def test_a_plain_tiff_goes_here_and_a_raw_does_not(tmp_path):
    from tests.scrub import raw_corpus as rc
    d = default_dispatcher()
    assert isinstance(d.resolve(tc.build()), TiffHandler)
    assert d.resolve(rc.build("canon")).format_id == "raw"
    src, out = tmp_path / "a.tif", tmp_path / "b.tif"
    src.write_bytes(tc.build())
    cli.scrub_file(str(src), str(out), "F1")
    assert out.read_bytes() == f1.scrub(tc.build())


@pytest.mark.skipif(not HAVE_EXIFTOOL, reason="exiftool not installed")
def test_exiftool_leaves_the_document_path_and_f1_does_not(tmp_path):
    """Survey §2, pinned: `exiftool -all=` keeps DocumentName, the file's
    original path. If ExifTool ever changes, this says so."""
    src, et = tmp_path / "a.tif", tmp_path / "et.tif"
    src.write_bytes(tc.build())
    subprocess.run(["exiftool", "-q", "-all=", "-o", str(et), str(src)], check=True)
    assert tc.document_path() in et.read_bytes()
    assert tc.document_path() not in f1.scrub(src.read_bytes())


# --------------------------------------------------------------------------- #
# The survey's real files, where they exist
# --------------------------------------------------------------------------- #
SURVEY = os.path.expanduser(os.environ.get("TIFF_SAMPLES",
                                           "~/metadata-research/step2/p6/tiff"))
SURVEY_FILES = ["sips.tiff", "sips_from_jpeg.tiff", "sips_tagged.tiff", "pillow.tiff",
                "pillow_exif.tiff", "pillow_lzw.tiff", "pillow_multipage.tiff",
                "tiffcp.tiff"]


@pytest.mark.parametrize("name", SURVEY_FILES)
def test_the_survey_tiffs_scrub_clean(name):
    path = os.path.join(SURVEY, name)
    if not os.path.exists(path):
        pytest.skip(f"{name}: survey file not on this machine (p6 plan §0)")
    data = open(path, "rb").read()
    out = f1.scrub(data)
    assert f1.residuals(out) == [] and b"SENTINEL" not in out
    with Image.open(path) as im:
        pages = getattr(im, "n_frames", 1)
    assert _same_pixels(data, out, pages)


def test_the_survey_list_is_the_survey():
    present = {os.path.basename(p) for p in glob.glob(os.path.join(SURVEY, "*.tiff"))}
    assert not present or set(SURVEY_FILES) <= present
