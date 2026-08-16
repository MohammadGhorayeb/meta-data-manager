"""The PDF Pareto matrix, and the claims it is allowed to make.

The A2@F1 cell is the one that matters at this milestone: it must not say "A2 fails"
and stop there. It has to name *which* producer channel leaked, because that is what
M4 is built from — and a cell that averaged the serializer and layout channels
together would report the same verdict whether F1 had closed one of them or neither.
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


def test_unbuilt_tiers_are_untested_not_passed(doc):
    """F2 and F3 do not exist. `not_tested`, never `not_applicable`: they are planned,
    and a reader must be able to tell "we have not measured this" from "this cell can
    never apply"."""
    for adversary in ("A1", "A2"):
        for fidelity in ("F2", "F3"):
            cell = _cell(doc, adversary, fidelity)
            assert cell["verdict"] == "not_tested"
            assert "M4" in cell["reason"] or "M5" in cell["reason"]


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
