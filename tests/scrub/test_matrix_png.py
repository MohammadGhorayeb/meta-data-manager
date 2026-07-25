"""The generated PNG Pareto matrix validates and records honest verdicts:
A1 pass at F1/F2, A2 fail at F1 but pass (losslessly) at F2, F3 not_applicable.
"""
from __future__ import annotations

from tests.harness.runner import matrix
from tests.scrub import gen_matrix_png


def _cell(doc, adv, fid):
    return next(c for c in doc["cells"]
               if c["adversary"] == adv and c["fidelity"] == fid)


def test_png_matrix_builds_and_validates(tmp_path):
    doc = gen_matrix_png.build_doc(str(tmp_path))
    matrix.validate(doc)   # raises on schema violation

    assert _cell(doc, "A1", "F1")["verdict"] == "pass"
    assert _cell(doc, "A1", "F2")["verdict"] == "pass"
    # A2: the deflate fingerprint survives F1 but is normalized losslessly at F2.
    assert _cell(doc, "A2", "F1")["verdict"] == "fail"
    assert _cell(doc, "A2", "F2")["verdict"] == "pass"
    # PNG is lossless: F3 adds nothing.
    assert _cell(doc, "A1", "F3")["verdict"] == "not_applicable"
    assert _cell(doc, "A2", "F3")["verdict"] == "not_applicable"
    assert doc["scrubber_fingerprint"]["verdict"] == "pass"
