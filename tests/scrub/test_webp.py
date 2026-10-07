"""Phase 6 M2 -- WebP at F1: every metadata chunk gone, every frame unchanged.

Pillow is the independent decoder; libwebp's own `dwebp`/`webpmux` are a second
opinion where installed. The survey's files (cwebp, webpmux, Pillow, gif2webp,
ExifTool-tagged) run where they exist.
"""
from __future__ import annotations

import ast
import io
import os
import shutil
import subprocess
import sys

import pytest
from PIL import Image, ImageChops

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from src.scrub import cli  # noqa: E402
from src.scrub.dispatch import default_dispatcher  # noqa: E402
from src.scrub.errors import ParseError  # noqa: E402
from src.scrub.formats.webp import f1  # noqa: E402
from src.scrub.formats.webp.handler import WebpHandler  # noqa: E402
from tests.scrub import tiff_corpus as tfc  # noqa: E402
from tests.scrub import webp_corpus as wc  # noqa: E402


def _frames(data: bytes) -> list[Image.Image]:
    out = []
    with Image.open(io.BytesIO(data)) as im:
        for k in range(getattr(im, "n_frames", 1)):
            im.seek(k)
            out.append(im.convert("RGBA").copy())
    return out


def _same(a: bytes, b: bytes) -> bool:
    fa, fb = _frames(a), _frames(b)
    return len(fa) == len(fb) and all(ImageChops.difference(x, y).getbbox() is None
                                      for x, y in zip(fa, fb, strict=True))


def _iccp(data: bytes) -> bytes:
    return next(bytes(data[a:a + n]) for f, a, n in f1.chunks(data, 12, len(data))
                if f == b"ICCP")


def test_the_corpus_imports_nothing_from_the_scrubber():
    tree = ast.parse(open(os.path.join(os.path.dirname(__file__), "webp_corpus.py"),
                          encoding="utf-8").read())
    imported = [n.module for n in ast.walk(tree)
                if isinstance(n, ast.ImportFrom) and n.module]
    assert not [m for m in imported if m.split(".")[0] in ("src", "scrub")]


@pytest.mark.parametrize("frames", [1, 3], ids=["still", "animated"])
def test_a_decoder_reads_the_corpus_and_the_planted_values_are_there(frames):
    data = wc.build(frames=frames)
    assert len(_frames(data)) == frames
    for value in wc.planted(frames=frames):
        assert value in data


@pytest.mark.parametrize("frames", [1, 3], ids=["still", "animated"])
def test_every_planted_value_is_gone_and_every_frame_is_unchanged(frames):
    data = wc.build(frames=frames)
    out = f1.scrub(data)
    for value in wc.planted(frames=frames):
        assert value not in out, f"{value!r} survived"
    assert _same(data, out)
    assert f1.residuals(out) == []


def test_the_flags_and_the_size_are_rewritten():
    out = f1.scrub(wc.build(frames=3))
    flags = out[20]
    assert not flags & (f1.FLAG_EXIF | f1.FLAG_XMP)
    assert flags & f1.FLAG_ANIM and flags & f1.FLAG_ICC
    assert int.from_bytes(out[4:8], "little") == len(out) - 8


def test_a_published_profile_stays_and_a_personal_one_is_sanitized():
    assert _iccp(f1.scrub(wc.build(icc="standard"))) == tfc.STANDARD_ICC
    kept = _iccp(f1.scrub(wc.build(icc="custom")))
    assert kept != tfc.CUSTOM_ICC and kept[128:] == tfc.CUSTOM_ICC[128:]


def test_two_files_that_differ_only_in_metadata_come_out_identical():
    for frames in (1, 3):
        assert f1.scrub(wc.build(1, frames=frames)) == \
            f1.scrub(wc.build(2, frames=frames))


def test_scrubbing_twice_changes_nothing():
    once = f1.scrub(wc.build(frames=3))
    assert f1.scrub(once) == once


@pytest.mark.parametrize("cut", [0.1, 0.5, 0.95])
def test_a_truncated_webp_is_refused(cut):
    data = wc.build(trailing=False)
    with pytest.raises(ParseError):
        f1.scrub(data[:int(len(data) * cut)])


def test_a_wav_file_is_not_taken_for_a_webp():
    wav = b"RIFF" + (36).to_bytes(4, "little") + b"WAVEfmt " + bytes(28)
    assert not WebpHandler().claims(wav)


def test_the_dispatcher_and_the_cli(tmp_path):
    data = wc.build()
    assert isinstance(default_dispatcher().resolve(data), WebpHandler)
    src, out = tmp_path / "a.webp", tmp_path / "b.webp"
    src.write_bytes(data)
    cli.scrub_file(str(src), str(out), "F1")
    assert out.read_bytes() == f1.scrub(data)


@pytest.mark.skipif(shutil.which("webpmux") is None, reason="libwebp tools absent")
def test_libwebps_own_tools_read_the_result(tmp_path):
    path = tmp_path / "a.webp"
    path.write_bytes(f1.scrub(wc.build(frames=3)))
    info = subprocess.run(["webpmux", "-info", str(path)], capture_output=True,
                          text=True)
    assert info.returncode == 0 and "Number of frames: 3" in info.stdout


SURVEY = os.path.expanduser(os.environ.get("WEBP_SAMPLES",
                                           "~/metadata-research/step2/p6/webp"))
SURVEY_FILES = ["cwebp_all.webp", "cwebp_lossless.webp", "cwebp_none.webp",
                "webpmux_xmp.webp", "pillow.webp", "anim.webp", "anim_tagged.webp"]


@pytest.mark.parametrize("name", SURVEY_FILES)
def test_the_survey_webps_scrub_clean(name):
    path = os.path.join(SURVEY, name)
    if not os.path.exists(path):
        pytest.skip(f"{name}: survey file not on this machine (p6 plan §0)")
    data = open(path, "rb").read()
    out = f1.scrub(data)
    assert f1.residuals(out) == [] and b"SENTINEL" not in out
    assert _same(data, out)
