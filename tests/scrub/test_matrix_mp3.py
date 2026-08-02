"""The generated MP3 Pareto matrix validates and records honest verdicts:
A1 pass at F1/F3 (F2 n/a), A2 fail at F1 but pass at F3 (F2 n/a)."""
from __future__ import annotations

import pytest

from tests.harness.runner import matrix
from tests.scrub import gen_matrix_mp3
from tests.scrub import mp3_corpus as mc

pytestmark = pytest.mark.skipif(not mc.HAVE_FFMPEG, reason="ffmpeg not installed")


def _cell(doc, adv, fid):
    return next(c for c in doc["cells"]
               if c["adversary"] == adv and c["fidelity"] == fid)


def test_mp3_matrix_builds_and_validates(tmp_path):
    doc = gen_matrix_mp3.build_doc(str(tmp_path))
    matrix.validate(doc)   # raises on schema violation

    assert _cell(doc, "A1", "F1")["verdict"] == "pass"
    assert _cell(doc, "A1", "F2")["verdict"] == "not_applicable"
    assert _cell(doc, "A2", "F2")["verdict"] == "not_applicable"
    assert doc["scrubber_fingerprint"]["verdict"] == "pass"

    if mc.HAVE_LAME:
        # F3 = the A2 defense: encoder fingerprint survives F1, erased at F3.
        assert _cell(doc, "A1", "F3")["verdict"] == "pass"
        assert _cell(doc, "A2", "F1")["verdict"] == "fail"
        assert _cell(doc, "A2", "F3")["verdict"] == "pass"


@pytest.mark.skipif(not mc.HAVE_LAME, reason="lame not installed")
def test_a2_at_f3_records_its_per_rate_evidence(tmp_path):
    """The A2@F3 pass is a per-sample-rate-group claim, so the cell must SAY which
    groups were verified. A bare 'pass' would read as one universal anonymity class,
    which is exactly the overclaim that went uncaught while the corpus was
    single-rate."""
    from tests.scrub import e_lame
    cell = e_lame.evaluate_cell("F3", e_lame.build_sources(str(tmp_path), repeats=2),
                                str(tmp_path))
    assert cell.verdict.value == "pass"
    for rate in mc.RATES:
        assert f"{rate} Hz" in cell.reason, f"cell does not report the {rate} Hz group"
    assert "WITHIN a sample-rate group" in cell.reason


@pytest.mark.skipif(not mc.HAVE_LAME, reason="lame not installed")
def test_producers_separate_inside_every_rate_group_at_f1(tmp_path):
    """Control for the test above: the per-rate machinery must be able to SEE a
    fingerprint. F1 keeps each producer's encoder signature, so every group must
    fail there — if it did not, the F3 pass would just mean the test is blind."""
    from tests.scrub import e_lame
    per_rate = e_lame.per_rate_report("F1", str(tmp_path), repeats=2)
    for rate, r in per_rate.items():
        assert r["a2_fail"], (
            f"{rate} Hz: F1 should keep the encoder fingerprint; a pass here means "
            "the per-rate check cannot detect what it is looking for")
