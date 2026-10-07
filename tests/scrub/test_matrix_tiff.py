"""The generated plain-TIFF Pareto matrix records honest verdicts.

A1@F1 measured in both byte orders over every locus the survey found; A2@F1 run
across every TIFF writer here, with identical pixels asserted rather than assumed,
and expected to fail with its surviving keys named.
"""
from __future__ import annotations

import pytest
from PIL import Image, ImageChops

from tests.harness.plugins.tiff import TiffPlugin
from tests.harness.runner import matrix
from tests.scrub import e_tiff, gen_matrix_tiff


def _cell(doc, adversary, fidelity):
    return next(c for c in doc["cells"]
                if c["adversary"] == adversary and c["fidelity"] == fidelity)


@pytest.fixture(scope="module")
def doc(tmp_path_factory):
    return gen_matrix_tiff.build_doc(str(tmp_path_factory.mktemp("tiff_matrix")))


def test_matrix_builds_and_validates(doc):
    matrix.validate(doc)
    assert doc["format"] == "tiff"
    assert doc["scrubber_fingerprint"]["verdict"] == "pass"


def test_a1_passes_at_f1_in_both_byte_orders(doc):
    cell = _cell(doc, "A1", "F1")
    assert cell["verdict"] == "pass" and "either byte order" in cell["reason"]


def test_a2_fails_at_f1_and_names_the_writer_keys(doc):
    cell = _cell(doc, "A2", "F1")
    assert cell["verdict"] == "fail"
    assert "compression" in cell["reason"] and "Peer set" in cell["reason"]


def test_the_writers_in_the_peer_set_wrote_identical_pixels(tmp_path):
    """Otherwise A2 would be measuring the picture, not the writer."""
    sources = e_tiff.build_sources(str(tmp_path), repeats=1)
    assert len(sources) >= 2
    images = [Image.open(paths[0]).convert("RGB") for paths in sources.values()]
    for im in images[1:]:
        assert ImageChops.difference(images[0], im).getbbox() is None


def test_the_tiers_that_do_not_exist_are_not_tested_not_failed(doc):
    for adversary in ("A1", "A2"):
        for fidelity in ("F2", "F3"):
            assert _cell(doc, adversary, fidelity)["verdict"] == "not_tested"


def test_the_only_declared_mark_is_the_zero_fill():
    assert TiffPlugin().mandatory_constants() == [b"\0"]
