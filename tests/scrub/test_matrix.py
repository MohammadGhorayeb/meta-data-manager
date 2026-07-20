"""The generated JPEG Pareto matrix validates and records honest verdicts."""
from __future__ import annotations

from tests.harness.runner import matrix
from tests.scrub import gen_matrix


def _cell(doc, adv, fid):
    return next(c for c in doc["cells"]
               if c["adversary"] == adv and c["fidelity"] == fid)


def test_matrix_builds_and_validates(tmp_path):
    doc = gen_matrix.build_doc(str(tmp_path))
    matrix.validate(doc)   # raises on schema violation

    assert _cell(doc, "A1", "F1")["verdict"] == "pass"
    assert _cell(doc, "A1", "F1")["noise_floor"]["deterministic"] is True
    # unmeasured points are honestly not_tested, not silently "pass"
    assert _cell(doc, "A2", "F1")["verdict"] == "not_tested"
    assert _cell(doc, "A1", "F2")["verdict"] == "not_tested"
    assert doc["scrubber_fingerprint"]["verdict"] == "pass"
