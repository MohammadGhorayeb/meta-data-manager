"""The generated WebP Pareto matrix records honest verdicts."""
from __future__ import annotations

import io

import pytest
from PIL import Image, ImageChops

from tests.harness.runner import matrix
from tests.scrub import e_webp, gen_matrix_webp


def _cell(doc, adversary, fidelity):
    return next(c for c in doc["cells"]
                if c["adversary"] == adversary and c["fidelity"] == fidelity)


@pytest.fixture(scope="module")
def doc(tmp_path_factory):
    return gen_matrix_webp.build_doc(str(tmp_path_factory.mktemp("webp_matrix")))


def test_matrix_builds_and_validates(doc):
    matrix.validate(doc)
    assert doc["format"] == "webp"
    assert doc["scrubber_fingerprint"]["verdict"] == "pass"


def test_a1_passes_still_and_animated(doc):
    cell = _cell(doc, "A1", "F1")
    assert cell["verdict"] == "pass" and "animated" in cell["reason"]


def test_a2_is_measured_and_names_its_keys(doc):
    cell = _cell(doc, "A2", "F1")
    assert cell["verdict"] in ("fail", "pass") and "Peer set" in cell["reason"]


def test_the_encoders_in_the_peer_set_wrote_identical_pixels(tmp_path):
    sources = e_webp.build_sources(str(tmp_path), repeats=1)
    assert len(sources) >= 2
    images = [Image.open(io.BytesIO(open(p[0], "rb").read())).convert("RGBA")
              for p in sources.values()]
    for im in images[1:]:
        assert ImageChops.difference(images[0], im).getbbox() is None


def test_the_tiers_that_do_not_exist_are_not_tested_not_failed(doc):
    for adversary in ("A1", "A2"):
        for fidelity in ("F2", "F3"):
            assert _cell(doc, adversary, fidelity)["verdict"] == "not_tested"
