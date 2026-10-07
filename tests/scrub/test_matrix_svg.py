"""The generated SVG Pareto matrix records honest verdicts."""
from __future__ import annotations

import pytest

from tests.harness.runner import matrix
from tests.scrub import gen_matrix_svg


def _cell(doc, adversary, fidelity):
    return next(c for c in doc["cells"]
                if c["adversary"] == adversary and c["fidelity"] == fidelity)


@pytest.fixture(scope="module")
def doc(tmp_path_factory):
    return gen_matrix_svg.build_doc(str(tmp_path_factory.mktemp("svg_matrix")))


def test_matrix_builds_and_validates(doc):
    matrix.validate(doc)
    assert doc["format"] == "svg"
    assert doc["scrubber_fingerprint"]["verdict"] == "pass"


def test_a1_passes_in_every_producer_shape(doc):
    cell = _cell(doc, "A1", "F1")
    assert cell["verdict"] == "pass" and "Illustrator" in cell["reason"]


def test_a2_says_which_producers_are_real_and_which_are_models(doc):
    cell = _cell(doc, "A2", "F1")
    assert cell["verdict"] in ("fail", "pass") and "corpus models" in cell["reason"]


def test_the_tiers_that_do_not_exist_are_not_tested_not_failed(doc):
    for adversary in ("A1", "A2"):
        for fidelity in ("F2", "F3"):
            assert _cell(doc, adversary, fidelity)["verdict"] == "not_tested"
