"""The generated FLAC Pareto matrix records honest verdicts.

The headline: A2 passes at **F2** — losslessly. FLAC joins PNG as a format where the
encoder fingerprint is defeated at zero quality cost, unlike JPEG and MP3 which need
a lossy F3. A regression here would quietly turn the project's strongest claim into
an overclaim, so the cell is asserted rather than eyeballed.
"""
from __future__ import annotations

import pytest

from tests.harness.runner import matrix
from tests.scrub import flac_corpus as fc
from tests.scrub import gen_matrix_flac

pytestmark = pytest.mark.skipif(not fc.HAVE_FFMPEG, reason="ffmpeg not installed")


def _cell(doc, adv, fid):
    return next(c for c in doc["cells"]
                if c["adversary"] == adv and c["fidelity"] == fid)


def test_flac_matrix_builds_and_validates(tmp_path):
    doc = gen_matrix_flac.build_doc(str(tmp_path))
    matrix.validate(doc)                        # raises on schema violation

    assert _cell(doc, "A1", "F1")["verdict"] == "pass"
    assert _cell(doc, "A1", "F2")["verdict"] == "pass"
    assert _cell(doc, "A1", "F3")["verdict"] == "not_applicable"

    # The A2 story: the encoder's structure survives a bit-preserving strip, and the
    # lossless re-encode erases it.
    assert _cell(doc, "A2", "F1")["verdict"] == "fail"
    assert _cell(doc, "A2", "F2")["verdict"] == "pass"
    assert _cell(doc, "A2", "F3")["verdict"] == "not_applicable"

    assert doc["scrubber_fingerprint"]["verdict"] == "pass"


def test_a2_at_f1_names_what_leaks(tmp_path):
    """A 'fail' has to say what failed, or the matrix is just a mood."""
    doc = gen_matrix_flac.build_doc(str(tmp_path))
    cell = _cell(doc, "A2", "F1")
    assert cell["leaks"], "a fail with no named leak is not evidence"
    assert cell["reason"]


def test_a2_at_f2_states_the_lossless_claim(tmp_path):
    """The distinguishing property of this result — bit-identical audio — must be in
    the cell, since that is what separates it from JPEG F3 and MP3 F3."""
    doc = gen_matrix_flac.build_doc(str(tmp_path))
    reason = _cell(doc, "A2", "F2")["reason"].lower()
    assert "bit-identical" in reason
    assert "residual" in reason, "residuals are documented, never silently omitted"
