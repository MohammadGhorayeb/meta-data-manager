"""Scrubbing an already-scrubbed file: what must not change, and what must.

A canonical form that is not a fixed point is not canonical. If `F1(F1(x))` differed
from `F1(x)`, the tier would be leaving something for a second pass to find — and
since the whole A2 defense is "every output looks like every other output", that
would mean our outputs are still carrying an input-dependent trace.

The asymmetry with F3 is the point of testing both halves. F3 is a *lossy re-encode*,
so running it twice is a second generation: the file changes, and it is supposed to.
Asserting that explicitly stops a future reader from "fixing" it, and it is the
reason `docs/limits.md` #29 tells users not to scrub twice.

Distinct from determinism (`test_determinism_cross_process.py`), which asks whether
the same *input* gives the same output. This asks whether the *output* is a fixed
point, which is a stronger claim and the one that makes a canonical form canonical.
"""
from __future__ import annotations

import pytest

from src.scrub.dispatch import default_dispatcher

# (format, tier) pairs whose output must be a fixed point: every bit-preserving and
# every lossless tier the tool offers.
LOSSLESS_CASES = [
    ("jpeg", "F1"), ("jpeg", "F2"),
    ("png", "F1"), ("png", "F2"),
    ("pdf", "F1"), ("pdf", "F2"),
    ("docx", "F1"), ("docx", "F2"),
    ("mp3", "F1"),
    ("flac", "F1"), ("flac", "F2"),
    ("m4a", "F1"), ("m4a", "F2"),
]


def _sample(fmt: str, tmp_path) -> bytes | None:
    from . import corpus as imgc
    from . import docx_corpus as dc
    from . import flac_corpus as fc
    from . import m4a_corpus as mc
    from . import mp3_corpus as m3c
    from . import pdf_corpus as pc
    from .test_png import _png

    if fmt == "jpeg":
        return imgc.build_torture_jpeg()
    if fmt == "png":
        return _png()
    if fmt == "pdf":
        return open(pc.torture_pdf(str(tmp_path / "t.pdf")), "rb").read()
    if fmt == "docx":
        return open(dc.synthetic(str(tmp_path / "s.docx")), "rb").read()
    if fmt == "mp3":
        return (open(m3c.torture_mp3(str(tmp_path / "t.mp3")), "rb").read()
                if m3c.HAVE_FFMPEG else None)
    if fmt == "flac":
        return (open(fc.torture_flac(str(tmp_path / "t.flac")), "rb").read()
                if fc.HAVE_FFMPEG else None)
    if fmt == "m4a":
        return (open(mc.torture_m4a(str(tmp_path / "t.m4a")), "rb").read()
                if mc.HAVE_FFMPEG else None)
    raise AssertionError(fmt)


@pytest.mark.parametrize("fmt,fidelity", LOSSLESS_CASES,
                         ids=[f"{f}-{t}" for f, t in LOSSLESS_CASES])
def test_a_scrubbed_file_is_a_fixed_point(fmt, fidelity, tmp_path):
    """`scrub(scrub(x)) == scrub(x)` byte for byte.

    A second pass finding anything to change would mean the first pass left an
    input-dependent trace behind — which is precisely what the A2 defense claims it
    does not do.
    """
    data = _sample(fmt, tmp_path)
    if data is None:
        pytest.skip(f"{fmt}: corpus tool absent")

    handler = default_dispatcher().resolve(data)
    once = handler.scrub(data, fidelity)
    twice = handler.scrub(once, fidelity)
    assert twice == once, (
        f"{fmt} {fidelity} is not a fixed point: {len(once)} bytes became "
        f"{len(twice)} on a second pass, so the first pass left something behind")


def test_the_lossy_tier_is_deliberately_not_a_fixed_point(tmp_path):
    """F3 re-encodes, so a second pass is a second generation of loss.

    Asserted rather than left as folklore: it is the difference between a tier that
    is *supposed* to change the file and one that has a bug, and it is why limit #29
    tells users to scrub once from the original rather than repeatedly.
    """
    from . import corpus as imgc
    data = imgc.build_torture_jpeg()
    handler = default_dispatcher().resolve(data)
    once = handler.scrub(data, "F3")
    twice = handler.scrub(once, "F3")
    assert twice != once, (
        "JPEG F3 became a fixed point — if that is genuinely true the tier stopped "
        "re-encoding, which would be a much bigger change than this test")


def test_the_report_shows_nothing_left_on_a_second_pass(tmp_path):
    """The user-visible consequence of the fixed-point property: re-scrubbing a
    scrubbed file finds no metadata to remove."""
    from src.scrub import report as rep

    from . import corpus as imgc
    data = imgc.build_torture_jpeg()
    handler = default_dispatcher().resolve(data)
    once = handler.scrub(data, "F1")

    second = rep.build(handler, once, handler.scrub(once, "F1"), "F1")
    assert not second.removed, (
        f"a second scrub still reports removals: "
        f"{[i.locus for i in second.removed]}")
