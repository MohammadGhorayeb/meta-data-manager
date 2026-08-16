"""E-PDF: which producer channel identifies the application that made a PDF?

Guards the experiment before the result, as Phase 2 established. An attack that cannot
tell five producers apart on an **untouched** file proves nothing when it also fails
on a scrubbed one — and here that check has to be made **per channel**, because a peer
set can easily separate producers one way and not another.
"""
from __future__ import annotations

import pytest

from tests.scrub import e_pdf
from tests.scrub import pdf_corpus as pc

pytestmark = pytest.mark.skipif(not pc.HAVE_PDFTOTEXT,
                                reason="needs pdftotext (poppler)")


@pytest.fixture(scope="module")
def sources(tmp_path_factory):
    return e_pdf.build_sources(str(tmp_path_factory.mktemp("e_pdf")), repeats=3)


@pytest.fixture(scope="module")
def results(sources, tmp_path_factory):
    tmp = str(tmp_path_factory.mktemp("e_pdf_run"))
    return {fid: e_pdf.run_condition(fid, sources, tmp) for fid in ("raw", "F1")}


def test_the_peer_set_always_holds_the_two_synthetics(sources):
    """Chrome and LibreOffice are unlikely on a CI runner and `cupsfilter` is
    macOS-only. Without a producer pair that exists everywhere, every PDF A2 cell
    would report `not_tested` on Linux and the published verdicts would go
    permanently unchallenged — limit #12, which exists because this project has been
    bitten by exactly that."""
    assert {"synth_td_int", "synth_tm_real"} <= set(sources)


def test_the_peer_set_holds_content_constant(sources):
    """Content constant, producer varying — the shape every A2 peer set here takes.
    If the documents differed, any separation found would be about the text."""
    texts = {pc.pdftotext(paths[0]) for paths in sources.values()}
    assert len(texts) == 1, "producers rendered different documents"
    assert len(texts.pop()) > 400, "too little text to carry a layout fingerprint"


def test_control_untouched_files_give_the_producer_away(results):
    """CONTROL. Asserted per channel: a channel that cannot identify the producer on
    an untouched file cannot prove anything about a scrubbed one, and reporting it as
    clean would be reporting an unmeasured cell as a pass."""
    valid = e_pdf.controls_valid(results)
    assert valid["serializer"], "no serializer feature separates untouched producers"
    assert valid["layout"], "no layout feature separates untouched producers"
    for channel in ("serializer", "layout"):
        assert results["raw"]["by_channel"][channel], channel


def test_f1_closes_the_serializer_channel(results):
    """The M3 result. Our writer decides the header, `/ID`, xref style, object streams
    and `/Length`, so after F1 no serializer feature should separate the producers."""
    leaked = results["F1"]["by_channel"]["serializer"]
    assert leaked == [], f"serializer still identifies the producer: {leaked}"


def test_f1_leaves_the_layout_channel_untouched(results):
    """The other half, and the reason A2@F1 fails. F1 never rewrites a content stream,
    so the operators, number precision, font subsets and glyph geometry are exactly as
    the original typesetter left them. This is what M4 has to work on — and per W5
    only part of it is normalisable without re-typesetting the page."""
    leaked = results["F1"]["by_channel"]["layout"]
    assert leaked, "layout leaked nothing after F1 — check the controls, not the tier"
    assert "struct:glyph_digest" in leaked, (
        "glyph geometry is the residual W5 predicts F2 cannot remove; if it is not "
        "leaking here, the experiment is not measuring what M4 will be judged on")


def test_every_leaking_key_belongs_to_a_named_channel(results):
    """A key the plugin emits that no channel claims would be counted in the verdict
    but reported to nobody — the one way this experiment could quietly under-report."""
    for fidelity, result in results.items():
        assert result["by_channel"]["unclassified"] == [], (
            f"{fidelity}: unclassified leaking keys "
            f"{result['by_channel']['unclassified']}")


def test_size_is_reported_as_its_own_channel(results):
    """Size stays in — the corpus holds the source document constant, so it is a real
    producer signal and excluding it would be hiding evidence. But it is a side
    effect of the other two channels rather than a statement about either, so it is
    named separately instead of being folded in."""
    from tests.harness.plugins.pdf import LAYOUT_KEYS, SERIALIZER_KEYS
    assert "struct:size" not in set(SERIALIZER_KEYS) | set(LAYOUT_KEYS)
    assert results["raw"]["by_channel"]["size"] == ["struct:size"]
