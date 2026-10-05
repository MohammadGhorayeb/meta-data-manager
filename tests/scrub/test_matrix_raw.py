"""The generated camera-RAW Pareto matrix records honest verdicts.

A1@F1 is measured across all five maker-note layouts, because each maker hides its
serials somewhere else; content identity is decoded (LibRaw), not parsed. A2 is
stated, not run: the model survives by design, and the camera-BODY question needs a
corpus this project does not have. The fingerprint guard declares exactly the marks
size-preserving cleaning must leave, and nothing that could hide a locus.
"""
from __future__ import annotations

import pytest

from tests.harness.plugins.raw import RawPlugin
from tests.harness.runner import matrix
from tests.scrub import gen_matrix_raw

pytest.importorskip("rawpy")


def _cell(doc, adversary, fidelity):
    return next(c for c in doc["cells"]
                if c["adversary"] == adversary and c["fidelity"] == fidelity)


@pytest.fixture(scope="module")
def doc(tmp_path_factory):
    return gen_matrix_raw.build_doc(str(tmp_path_factory.mktemp("raw_matrix")))


def test_matrix_builds_and_validates(doc):
    matrix.validate(doc)
    assert doc["format"] == "raw"
    assert doc["scrubber_fingerprint"]["verdict"] == "pass"


def test_a1_passes_at_f1_in_every_maker_note_layout(doc):
    cell = _cell(doc, "A1", "F1")
    assert cell["verdict"] == "pass"
    for layout in gen_matrix_raw.LAYOUTS:
        assert layout in cell["reason"]


def test_a2_is_stated_not_guessed(doc):
    cell = _cell(doc, "A2", "F1")
    assert cell["verdict"] == "not_tested"
    assert "BODY" in cell["reason"] and "#47" in cell["reason"]


def test_the_tiers_that_do_not_exist_are_not_tested_not_failed(doc):
    for adversary in ("A1", "A2"):
        for fidelity in ("F2", "F3"):
            assert _cell(doc, adversary, fidelity)["verdict"] == "not_tested"


def test_the_declared_marks_carry_no_locus():
    """A declaration broad enough to hide a leak would be an exemption, not a
    declaration: each is zeros, an end-of-image marker, or a start-of-image marker
    with a colour-profile segment -- and nothing else."""
    for constant in RawPlugin().mandatory_constants():
        assert set(constant) <= {0x00, 0xFF, 0xD8, 0xD9, 0xE2}, constant[:16]


def test_the_declarations_are_generated_from_the_code_that_writes_them():
    from src.scrub.formats.raw import f1
    assert RawPlugin().mandatory_constants() == [
        f1.PREVIEW_PAD_MARK, f1.PREVIEW_ICC_FIRST, f1.BLANKED_RUN]
