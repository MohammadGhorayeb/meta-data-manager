"""The generated BMP Pareto matrix records honest verdicts."""
from __future__ import annotations

import io

import pytest
from PIL import Image, ImageChops

from tests.harness.contract import V
from tests.harness.oracle import leak
from tests.harness.plugins.bmp import BmpPlugin
from tests.harness.runner import matrix
from tests.scrub import e_bmp, gen_matrix_bmp


def _cell(doc, adversary, fidelity):
    return next(c for c in doc["cells"]
                if c["adversary"] == adversary and c["fidelity"] == fidelity)


@pytest.fixture(scope="module")
def doc(tmp_path_factory):
    return gen_matrix_bmp.build_doc(str(tmp_path_factory.mktemp("bmp_matrix")))


def test_matrix_builds_and_validates(doc):
    matrix.validate(doc)
    assert doc["format"] == "bmp"
    assert doc["scrubber_fingerprint"]["verdict"] == "pass"


def test_a1_passes_in_every_layout(doc):
    cell = _cell(doc, "A1", "F1")
    assert cell["verdict"] == "pass" and "control" in cell["reason"]
    for label in gen_matrix_bmp.LAYOUTS:
        assert label in cell["reason"]


@pytest.mark.parametrize("label", sorted(gen_matrix_bmp.LAYOUTS))
def test_the_do_nothing_control_fails_a1(label, tmp_path):
    """Non-vacuity, per layout: a scrubber that copies its input is caught."""
    groups = gen_matrix_bmp._variants(str(tmp_path), label,
                                      gen_matrix_bmp.LAYOUTS[label])
    cell = leak.evaluate_a1(gen_matrix_bmp.DoNothingScrubber(), BmpPlugin(), groups,
                            "F1", n=3, sentinel_field="metadata_variant",
                            modality="bytes")
    assert cell.verdict == V.FAIL
    assert any(lk.kind == "variant_correlated" for lk in cell.leaks)


def test_a2_is_measured_and_names_its_keys(doc):
    cell = _cell(doc, "A2", "F1")
    assert cell["verdict"] in ("fail", "pass") and "Peer set" in cell["reason"]


def test_the_writers_in_the_peer_set_wrote_identical_colours(tmp_path):
    sources = e_bmp.build_sources(str(tmp_path), repeats=1)
    assert len(sources) >= 2
    images = [Image.open(io.BytesIO(open(p[0], "rb").read())).convert("RGBA")
              for p in sources.values()]
    for im in images[1:]:
        assert ImageChops.difference(images[0], im).getbbox() is None


def test_the_tiers_that_do_not_exist_are_not_tested_not_failed(doc):
    for adversary in ("A1", "A2"):
        for fidelity in ("F2", "F3"):
            assert _cell(doc, adversary, fidelity)["verdict"] == "not_tested"
