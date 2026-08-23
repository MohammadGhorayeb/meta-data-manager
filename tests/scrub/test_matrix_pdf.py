"""The PDF Pareto matrix, and the claims it is allowed to make.

The A2 cells must not say "A2 fails" and stop there. They have to name *which*
producer channel leaked — a cell that averaged the serializer and layout channels
together would report the same verdict whether F1 had closed one of them or neither,
and the same argument applies one level down at F2, where the text machine's operator
vocabulary collapses and the graphics vocabulary does not.
"""
from __future__ import annotations

import re

import pytest

from tests.harness.plugins.pdf import LAYOUT_KEYS, SERIALIZER_KEYS, empty_document_skeleton
from tests.scrub import gen_matrix_pdf


@pytest.fixture(scope="module")
def doc(tmp_path_factory):
    return gen_matrix_pdf.build_doc(str(tmp_path_factory.mktemp("pdf_matrix")))


def _cell(doc, adversary, fidelity):
    return next(c for c in doc["cells"]
                if c["adversary"] == adversary and c["fidelity"] == fidelity)


def test_matrix_builds_and_validates(doc):
    from tests.harness.runner import matrix
    matrix.validate(doc)
    assert doc["format"] == "pdf"


def test_a1_at_f1_passes(doc):
    """Same page, metadata differing only by a sentinel, in both `/Info` and XMP."""
    assert _cell(doc, "A1", "F1")["verdict"] == "pass"


def test_a2_at_f1_names_the_channel_that_leaked(doc):
    """The M3 result. F1 writes every byte of the file itself, so the serializer
    channel should be closed outright; it never rewrites a content stream, so the
    layout channel should be untouched. The cell has to say exactly that."""
    cell = _cell(doc, "A2", "F1")
    assert cell["verdict"] == "fail", "F1 does not touch the page, so A2 must fail"
    reason = cell["reason"]
    assert "layout:" in reason, "the cell must name which channel leaked"
    assert "serializer:" not in reason, (
        "F1 decides the header, /ID, xref style, object streams and /Length itself — "
        "if the serializer channel still separates producers, something regressed")
    leaked = {leak["locus"]["feature_id"] for leak in cell["leaks"]}
    assert leaked & set(LAYOUT_KEYS), "expected the layout channel in the leak list"
    assert not (leaked & set(SERIALIZER_KEYS)), f"serializer keys leaked: {leaked}"


def test_a1_at_f2_passes(doc):
    assert _cell(doc, "A1", "F2")["verdict"] == "pass"


def test_a2_at_f2_still_fails_and_says_what_survived(doc):
    """The honest M4 answer. F2 rewrites every content stream through one writer, so
    the text machine's *spelling* is gone — but glyph geometry is not spelling, and a
    cell that claimed a pass here would be reporting a broken measurement rather than
    a closed leak. The leak list must still carry the glyph digest."""
    cell = _cell(doc, "A2", "F2")
    assert cell["verdict"] == "fail"
    leaked = {leak["locus"]["feature_id"] for leak in cell["leaks"]}
    assert "struct:glyph_digest" in leaked, (
        "glyph geometry is the predicted F2 floor — if it stopped leaking, either the "
        "tier is re-typesetting the page or the feature stopped measuring anything")
    assert not (leaked & set(SERIALIZER_KEYS)), (
        f"F2 inherits F1's serializer, so these must stay closed: {leaked}")


def test_peer_set_is_reported_in_the_cell(doc):
    """Which producers were actually available is part of the claim. A cell measured
    against two synthetics is a weaker statement than one measured against five, and
    the reader has to be able to see which they are looking at."""
    reason = _cell(doc, "A2", "F1")["reason"]
    assert "synth_td_int" in reason and "synth_tm_real" in reason, (
        "the two synthetics exist so this cell is measurable without Chrome, "
        "LibreOffice or macOS — they must always be in the peer set")


# --------------------------------------------------------------------------- #
# The guard on the guard
# --------------------------------------------------------------------------- #
def test_fingerprint_guard_passes(doc):
    fp = doc["scrubber_fingerprint"]
    assert fp["verdict"] == "pass", f"tool signature found: {fp['signatures']}"


def test_the_declared_skeleton_hides_nothing_that_matters():
    """`mandatory_constants()` declares the whole empty document our writer emits,
    which is a broad declaration — broad enough that it could in principle hide a real
    signature. So it is checked rather than trusted: the skeleton must be pure
    structure. Add a `/Producer` string or a timestamp to the serializer tomorrow and
    this fails, even though the guard itself would still pass.
    """
    skeleton = empty_document_skeleton()
    lowered = skeleton.lower()
    for banned in (b"producer", b"creator", b"pikepdf", b"qpdf", b"scrub",
                   b"moddate", b"creationdate", b"/id", b"/info"):
        assert banned not in lowered, f"{banned!r} is in the declared skeleton"
    assert not re.search(rb"D:\d{8}", skeleton), "a date is in the declared skeleton"
    assert not re.search(rb"(.)\1{31}", skeleton), (
        "a 32-byte constant run is in the declared skeleton — that is padding, and "
        "padding is exactly what the guard exists to catch")
    assert len(skeleton) < 1024, (
        f"the declared skeleton is {len(skeleton)} bytes; a skeleton that grows past "
        "an empty document's structure is no longer just structure")


def test_a1_at_f3_passes(doc):
    assert _cell(doc, "A1", "F3")["verdict"] == "pass"


def test_a2_at_f3_is_judged_on_pixels_not_structure(doc):
    """The cell that would have been easiest to get wrong.

    Structurally F3 passes almost trivially — the file is entirely our own output, so
    a structural comparison has nearly nothing left to separate producers with. If
    this cell ever reads `pass`, the most likely cause is not that F3 got better but
    that it stopped being judged on the rendered page, so the locus is asserted too.
    """
    cell = _cell(doc, "A2", "F3")
    assert cell["verdict"] == "fail", (
        "rasterising relocates the typesetter's geometry into pixel space; it does "
        "not remove it, and E-PDF-RASTER measures exactly that")
    spaces = {leak["locus"]["space"] for leak in cell["leaks"]}
    assert spaces == {"pixel"}, (
        f"the F3 residual is pixel-domain, not structural — got {spaces}")
    assert "chance" in cell["reason"] and "n=" in cell["reason"], (
        "the cell must carry the statistic it was decided on, not just a verdict")


def test_every_a2_cell_names_its_peer_set(doc):
    """Which producers were actually available is part of the claim — a cell measured
    against two synthetics is a weaker statement than one measured against five."""
    for fidelity in ("F1", "F2", "F3"):
        reason = _cell(doc, "A2", fidelity)["reason"]
        assert "synth_td_int" in reason and "synth_tm_real" in reason, (
            f"A2@{fidelity} does not say what it was measured against")
