"""A second independent reader per family (`docs/p6_tail_plan.md` §14).

ExifTool is the measuring stick, and it has been measured missing things: the photo
inside an SVG or an EPUB, Canon's third owner-name copy, the iPhone's stale GPS in
`mdat`. So each family is read again by a tool sharing no code with ExifTool or
with us -- exiv2 (C++) for pictures and camera raws, MediaInfo for video tracks,
qpdf (decompressing every stream) for PDF.

Every case asserts both directions: the reader SEES the planted values in the input
(otherwise the check proves nothing), and sees none of them in the output.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from src.scrub.dispatch import default_dispatcher  # noqa: E402
from tests.scrub import corpus as imgc  # noqa: E402
from tests.scrub import heic_corpus as hc  # noqa: E402
from tests.scrub import pdf_corpus as pc  # noqa: E402
from tests.scrub import raw_corpus as rc  # noqa: E402
from tests.scrub import telemetry_corpus as tlc  # noqa: E402
from tests.scrub import tiff_corpus as tfc  # noqa: E402
from tests.scrub import webp_corpus as wbc  # noqa: E402
from tests.scrub import zip_corpus as zc  # noqa: E402

PLANTED = re.compile(r"sentinel|secret", re.I)


def _pair(tmp_path, name: str, data: bytes, **options) -> tuple[str, str]:
    src, out = tmp_path / name, tmp_path / f"clean_{name}"
    src.write_bytes(data)
    out.write_bytes(default_dispatcher(**options).resolve(data).scrub(data, "F1"))
    return str(src), str(out)


def _exiv2(path: str) -> str:
    return subprocess.run(["exiv2", "-pa", path], capture_output=True,
                          text=True, errors="replace").stdout


PICTURES = {
    "torture.jpg": lambda tmp: imgc.build_torture_jpeg(),
    "photo.jpg": lambda tmp: zc.photo(1),
    "scan.tif": lambda tmp: tfc.build(),
    "frame.webp": lambda tmp: wbc.build(),
    "camera.orf": lambda tmp: rc.build("olympus"),
    "camera.cr3": lambda tmp: rc.cr3(),
    "phone.heic": lambda tmp: open(hc.torture(str(tmp / "src.heic")), "rb").read(),
}


@pytest.mark.skipif(shutil.which("exiv2") is None, reason="exiv2 not installed")
@pytest.mark.parametrize("name", sorted(PICTURES))
def test_exiv2_sees_the_planted_values_before_and_none_after(name, tmp_path):
    if name.endswith(".heic") and not hc.HAVE_HEIF:
        pytest.skip("pillow-heif not installed")
    src, out = _pair(tmp_path, name, PICTURES[name](tmp_path))
    before, after = _exiv2(src), _exiv2(out)
    if not before.strip():
        # Ubuntu's exiv2 0.27 is built without BMFF (HEIC, CR3): it reads nothing,
        # which is a missing reader, not a pass. A reader that reads the file and
        # misses the planted values still fails below.
        pytest.skip(f"this exiv2 build does not read {name.rsplit('.', 1)[1]}")
    assert PLANTED.search(before), "exiv2 cannot see the planted values: vacuous"
    assert not PLANTED.findall(after)
    assert "GPSInfo" not in after


def tc_build(shape: str) -> bytes:
    return tlc.build(1, shape=shape)


def _mediainfo(path: str) -> dict:
    r = subprocess.run(["mediainfo", "--Output=JSON", "--Full", path],
                       capture_output=True, text=True)
    return json.loads(r.stdout)["media"]


@pytest.mark.skipif(shutil.which("mediainfo") is None, reason="MediaInfo not installed")
@pytest.mark.parametrize("shape,options", [("gopro", {}), ("camm", {}),
                                           ("android", {}),
                                           ("drone", {"drop_subtitles": True})])
def test_mediainfo_sees_the_telemetry_before_and_only_picture_and_sound_after(
        shape, options, tmp_path):
    src, out = _pair(tmp_path, f"{shape}.mp4", tc_build(shape), **options)
    before, after = _mediainfo(src), _mediainfo(out)
    kinds_before = {t["@type"] for t in before["track"]}
    kinds_after = {t["@type"] for t in after["track"]}
    text_before = json.dumps(before)
    assert kinds_before - {"General", "Video", "Audio"} or PLANTED.search(text_before), \
        "MediaInfo sees no telemetry and no planted value: vacuous"
    assert kinds_after <= {"General", "Video", "Audio"}
    assert not PLANTED.findall(json.dumps(after))



@pytest.mark.skipif(shutil.which("qpdf") is None, reason="qpdf not installed")
def test_qpdf_decompresses_everything_and_finds_none_of_it(tmp_path):
    """qpdf rewrites the file with every stream decompressed (`--qdf`), so a
    planted value hidden in a compressed stream -- XMP on an image, an annotation,
    the inline JPEG's EXIF -- is plain text to search. It also checks the
    structure."""
    path = pc.torture_pdf(str(tmp_path / "src.pdf"))
    src, out = _pair(tmp_path, "doc.pdf", open(path, "rb").read())

    def expanded(p):
        return subprocess.run(["qpdf", "--qdf", "--object-streams=disable", p, "-"],
                              capture_output=True).stdout.decode("latin-1")
    assert PLANTED.search(expanded(src)), "qpdf cannot see the planted values"
    assert not PLANTED.findall(expanded(out))
    check = subprocess.run(["qpdf", "--check", out], capture_output=True, text=True)
    assert check.returncode == 0, check.stdout + check.stderr
