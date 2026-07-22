"""The generated JPEG Pareto matrix validates and records honest verdicts."""
from __future__ import annotations

import shutil

from tests.harness.runner import matrix
from tests.scrub import gen_matrix


def _cell(doc, adv, fid):
    return next(c for c in doc["cells"]
               if c["adversary"] == adv and c["fidelity"] == fid)


def test_matrix_builds_and_validates(tmp_path):
    # Empty a2_corpus_dir keeps this hermetic: A2 stays honestly not_tested
    # rather than depending on a machine-specific peer corpus. The measured-A2
    # path (experiment E3) is covered by test_e3_dqt.
    doc = gen_matrix.build_doc(str(tmp_path), a2_corpus_dir=str(tmp_path))
    matrix.validate(doc)   # raises on schema violation

    assert _cell(doc, "A1", "F1")["verdict"] == "pass"
    assert _cell(doc, "A1", "F1")["noise_floor"]["deterministic"] is True
    # A1@F2 is measured (pass) when jpegtran is present, else honest not_tested.
    expected_f2 = "pass" if shutil.which("jpegtran") else "not_tested"
    assert _cell(doc, "A1", "F2")["verdict"] == expected_f2
    # unmeasured points are honestly not_tested, not silently "pass"
    assert _cell(doc, "A2", "F1")["verdict"] == "not_tested"
    assert _cell(doc, "A1", "F3")["verdict"] == "not_tested"
    assert doc["scrubber_fingerprint"]["verdict"] == "pass"
