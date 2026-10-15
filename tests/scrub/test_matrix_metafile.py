"""The generated EMF/WMF Pareto matrix records honest verdicts."""
from __future__ import annotations

import shutil

import pytest

from tests.harness.contract import V
from tests.harness.oracle import leak
from tests.harness.plugins.metafile import MetafilePlugin
from tests.harness.runner import matrix
from tests.scrub import gen_matrix_metafile
from tests.scrub.test_harness_a1 import ScrubResult


def _cell(doc, adversary, fidelity):
    return next(c for c in doc["cells"]
                if c["adversary"] == adversary and c["fidelity"] == fidelity)


@pytest.fixture(scope="module")
def doc(tmp_path_factory):
    return gen_matrix_metafile.build_doc(str(tmp_path_factory.mktemp("metafile_matrix")))


def test_matrix_builds_and_validates(doc):
    matrix.validate(doc)
    assert doc["format"] == "metafile"
    assert doc["scrubber_fingerprint"]["verdict"] == "pass"


def test_a1_passes_for_emf_and_wmf(doc):
    cell = _cell(doc, "A1", "F1")
    assert cell["verdict"] == "pass" and "WMF" in cell["reason"]


class _DoesNothing:
    name, version = "identity", "0"

    def run(self, in_path, out_path, fidelity):
        shutil.copy(in_path, out_path)
        return ScrubResult(ok=True, out_path=out_path, fidelity=fidelity)


@pytest.mark.parametrize("kind", ["emf", "wmf"])
def test_the_oracle_sees_the_loci_when_nothing_removes_them(kind, tmp_path):
    """Control: the pass above means something only if a scrubber that changes
    nothing fails the same check."""
    cell = leak.evaluate_a1(_DoesNothing(), MetafilePlugin(),
                            gen_matrix_metafile._variants(str(tmp_path), kind), "F1",
                            n=3, sentinel_field="metadata_variant", modality="bytes")
    assert cell.verdict == V.FAIL and cell.leaks


def test_a2_is_not_claimed_and_names_what_f1_keeps(doc):
    cell = _cell(doc, "A2", "F1")
    assert cell["verdict"] == "not_tested" and "reference device" in cell["reason"]


def test_the_tiers_that_do_not_exist_are_not_tested_not_failed(doc):
    for adversary in ("A1", "A2"):
        for fidelity in ("F2", "F3"):
            assert _cell(doc, adversary, fidelity)["verdict"] == "not_tested"
