"""The generated EPUB Pareto matrix records honest verdicts."""
from __future__ import annotations

import re

import pytest

from tests.harness import config
from tests.harness.oracle import fingerprint_guard
from tests.harness.plugins.epub import EpubPlugin, f1, words
from tests.harness.runner import matrix
from tests.scrub import e_epub, gen_matrix_epub

pytestmark = pytest.mark.skipif(not e_epub.producers(),
                                reason="no EPUB producer (soffice, pandoc)")


def _cell(doc, adversary, fidelity):
    return next(c for c in doc["cells"]
                if c["adversary"] == adversary and c["fidelity"] == fidelity)


@pytest.fixture(scope="module")
def doc(tmp_path_factory):
    return gen_matrix_epub.build_doc(str(tmp_path_factory.mktemp("epub_matrix")))


def test_matrix_builds_and_validates(doc):
    matrix.validate(doc)
    assert doc["format"] == "epub"
    assert doc["scrubber_fingerprint"]["verdict"] == "pass"


def test_a1_passes_in_every_producer_shape(doc):
    cell = _cell(doc, "A1", "F1")
    assert cell["verdict"] == "pass" and "5 producer shapes" in cell["reason"]


def test_a2_names_its_channels(doc):
    cell = _cell(doc, "A2", "F1")
    assert cell["verdict"] in ("pass", "fail")
    assert "Peer set" in cell["reason"] or cell["verdict"] == "pass"


def test_every_producer_was_given_the_same_text(tmp_path):
    """The A2 cell compares producers of one document. Each adds structure of its
    own (pandoc a title page and a heading) -- that is the content model being
    measured -- but every book holds the source's paragraphs, in order."""
    sources = e_epub.build_sources(str(tmp_path), repeats=1)
    paragraphs = [p for p in e_epub.TEXT.splitlines() if p.strip()]
    for name, paths in sources.items():
        text = words(f1.Book(open(paths[0], "rb").read(), check_memory=False))
        pos = 0
        for p in paragraphs:
            pos = text.find(p, pos)
            assert pos >= 0, f"{name} lost {p!r}"


def test_the_packager_channel_closes_and_the_producers_differed_raw(tmp_path):
    sources = e_epub.build_sources(str(tmp_path), repeats=2)
    if len(sources) < 2:
        pytest.skip("one producer: nothing to separate")
    raw = e_epub.run_condition("raw", sources, str(tmp_path))
    clean = e_epub.run_condition("F1", sources, str(tmp_path))
    assert raw["by_channel"]["pkg_"], "the packagers were indistinguishable raw"
    assert clean["by_channel"]["pkg_"] == []


def test_the_guard_catches_a_mark_in_the_package_document(tmp_path, monkeypatch):
    """Non-vacuity: with the ZIP fields and F1's two constants declared, a
    generator line written into every package document must fail the guard. (The
    scrub's own residual check refuses it first; switched off here.)"""
    monkeypatch.setattr(f1, "residuals", lambda data: [])
    real = f1.clean_opf

    def marked(data, identifier, path="OPF"):
        out = real(data, identifier, path)
        return re.sub(rb"</metadata>", b'<meta name="generator" content="scrubbed-v1"'
                      b"/></metadata>", out, count=1)
    monkeypatch.setattr(f1, "clean_opf", marked)
    verdict, sigs = fingerprint_guard.evaluate(
        gen_matrix_epub._scrubber(), EpubPlugin(),
        gen_matrix_epub._diverse(str(tmp_path)), "F1", min_len=config.MIN_SIG_LEN)
    assert verdict == "fail"
    assert any(b"scrubbed-v1".hex() in s["bytes_hex"] for s in sigs)
