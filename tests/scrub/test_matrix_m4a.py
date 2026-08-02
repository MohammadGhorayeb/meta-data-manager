"""The generated M4A Pareto matrix records honest verdicts.

M4A is the format MAT2 refuses outright, so these cells are capability the benchmark
alternative does not have. The A2 story is deliberately partial and must stay that
way in writing: F2 normalises the muxer, F3 collapses same-source producers to
byte-identical output, and a primary-encoding trace remains when the source's own
first encode differed.
"""
from __future__ import annotations

import pytest

from tests.harness.runner import matrix
from tests.scrub import gen_matrix_m4a
from tests.scrub import m4a_corpus as mc

pytestmark = pytest.mark.skipif(not mc.HAVE_FFMPEG, reason="ffmpeg not installed")


def _cell(doc, adv, fid):
    return next(c for c in doc["cells"]
                if c["adversary"] == adv and c["fidelity"] == fid)


@pytest.fixture(scope="module")
def doc(tmp_path_factory):
    return gen_matrix_m4a.build_doc(str(tmp_path_factory.mktemp("m4a_matrix")))


def test_matrix_builds_and_validates(doc):
    matrix.validate(doc)
    assert doc["scrubber_fingerprint"]["verdict"] == "pass"


def test_a1_passes_at_every_tier(doc):
    """Tags, cover art and the structural timestamps go at every fidelity."""
    for fid in ("F1", "F2", "F3"):
        assert _cell(doc, "A1", fid)["verdict"] == "pass"


def test_a2_fails_are_specific_about_which_channel(doc):
    """Two producers live in one M4A — the muxer and the audio encoder. A verdict
    that does not say which one leaked is not evidence."""
    for fid in ("F1", "F2", "F3"):
        cell = _cell(doc, "A2", fid)
        assert cell["verdict"] == "fail"
        assert cell["leaks"], "a fail with no named leak is not evidence"
    assert "muxer layout" in _cell(doc, "A2", "F1")["reason"]


def test_a2_at_f3_records_the_collapse_and_the_residual(doc):
    """The measured finding, in the cell: same-source producers become byte-identical
    and what remains is the primary-encoding trace — not a blanket failure."""
    reason = _cell(doc, "A2", "F3")["reason"]
    assert "BYTE-IDENTICAL" in reason
    assert "primary-encoding trace" in reason
    assert "not a pass" in reason, "an unmeasured residual must not read as measured"
