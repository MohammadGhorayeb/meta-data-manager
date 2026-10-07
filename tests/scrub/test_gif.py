"""Phase 6 M3 -- GIF at F1: every metadata block gone, every frame unchanged."""
from __future__ import annotations

import ast
import io
import os
import sys

import pytest
from PIL import Image, ImageChops

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from src.scrub import cli  # noqa: E402
from src.scrub.dispatch import default_dispatcher  # noqa: E402
from src.scrub.errors import ParseError  # noqa: E402
from src.scrub.formats.gif import f1  # noqa: E402
from src.scrub.formats.gif.handler import GifHandler  # noqa: E402
from tests.scrub import gif_corpus as gc  # noqa: E402
from tests.scrub import tiff_corpus as tfc  # noqa: E402


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


def test_the_corpus_imports_nothing_from_the_scrubber():
    tree = ast.parse(open(os.path.join(os.path.dirname(__file__), "gif_corpus.py"),
                          encoding="utf-8").read())
    imported = [n.module for n in ast.walk(tree)
                if isinstance(n, ast.ImportFrom) and n.module]
    assert not [m for m in imported if m.split(".")[0] in ("src", "scrub")]


@pytest.mark.parametrize("frames", [1, 3], ids=["still", "animated"])
def test_a_decoder_reads_the_corpus_and_the_values_are_there(frames):
    data = gc.build(frames=frames)
    assert len(_frames(data)) == frames
    for value in gc.planted():
        assert value in data


@pytest.mark.parametrize("frames", [1, 3], ids=["still", "animated"])
def test_every_planted_value_is_gone_and_every_frame_is_unchanged(frames):
    data = gc.build(frames=frames)
    out = f1.scrub(data)
    for value in gc.planted():
        assert value not in out, f"{value!r} survived"
    assert _same(data, out) and f1.residuals(out) == []


def test_what_draws_the_picture_stays():
    out = f1.scrub(gc.build(frames=3))
    assert b"PLAIN-TEXT-CONTENT" in out and b"NETSCAPE2.0" in out
    with Image.open(io.BytesIO(out)) as im:
        assert im.info.get("loop") == 0 and im.info.get("duration") == 80


def test_a_published_profile_stays_and_a_personal_one_is_sanitized():
    assert tfc.STANDARD_ICC in f1.scrub(gc.build(icc="standard"))
    out = f1.scrub(gc.build(icc="custom"))
    assert tfc.CUSTOM_ICC[128:200] in out and tfc.CUSTOM_ICC[:84] not in out


def test_two_files_that_differ_only_in_metadata_come_out_identical():
    for frames in (1, 3):
        assert f1.scrub(gc.build(1, frames=frames)) == f1.scrub(gc.build(2, frames=frames))


def test_scrubbing_twice_changes_nothing():
    once = f1.scrub(gc.build(frames=3))
    assert f1.scrub(once) == once


@pytest.mark.parametrize("cut", [0.1, 0.5, 0.95])
def test_a_truncated_gif_is_refused(cut):
    data = gc.build(trailing=False)
    with pytest.raises(ParseError):
        f1.scrub(data[:int(len(data) * cut)])


def test_the_dispatcher_and_the_cli(tmp_path):
    data = gc.build()
    assert isinstance(default_dispatcher().resolve(data), GifHandler)
    src, out = tmp_path / "a.gif", tmp_path / "b.gif"
    src.write_bytes(data)
    cli.scrub_file(str(src), str(out), "F1")
    assert out.read_bytes() == f1.scrub(data)


SURVEY = os.path.expanduser(os.environ.get("GIF_SAMPLES",
                                           "~/metadata-research/step2/p6/gif"))
SURVEY_FILES = ["pillow.gif", "pillow_comment.gif", "sips.gif", "anim.gif",
                "anim_tagged.gif", "exiftool_tagged.gif"]


@pytest.mark.parametrize("name", SURVEY_FILES)
def test_the_survey_gifs_scrub_clean(name):
    path = os.path.join(SURVEY, name)
    if not os.path.exists(path):
        pytest.skip(f"{name}: survey file not on this machine (p6 plan §0)")
    data = open(path, "rb").read()
    out = f1.scrub(data)
    assert f1.residuals(out) == [] and b"SENTINEL" not in out and _same(data, out)
