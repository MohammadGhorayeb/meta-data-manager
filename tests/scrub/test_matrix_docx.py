"""The DOCX Pareto matrix, and the claims it is allowed to make.

The A2 cell must not say "A2 fails" and stop there. A DOCX has two producers in one
file — the packager that laid out the ZIP and the document model that wrote the
WordprocessingML — and a cell that averaged them would report the same verdict
whether F1 had closed one channel or neither. Naming the channel is what makes the
result useful: it is what scopes F2.
"""
from __future__ import annotations

import pytest

from tests.harness.plugins.docx import MODEL_KEYS, PACKAGER_KEYS
from tests.scrub import docx_corpus as C
from tests.scrub import gen_matrix_docx


@pytest.fixture(scope="module")
def doc(tmp_path_factory):
    return gen_matrix_docx.build_doc(str(tmp_path_factory.mktemp("docx_matrix")))


def _cell(doc, adversary, fidelity):
    return next(c for c in doc["cells"]
                if c["adversary"] == adversary and c["fidelity"] == fidelity)


def test_matrix_builds_and_validates(doc):
    from tests.harness.runner import matrix
    matrix.validate(doc)
    assert doc["format"] == "docx"


def test_a1_at_f1_passes(doc):
    """Three packages differing only by a sentinel — placed in `core.xml`, `app.xml`,
    `custom.xml` and `people.xml` at once — must collapse to identical bytes."""
    assert _cell(doc, "A1", "F1")["verdict"] == "pass"


def test_a2_at_f1_fails_on_the_model_channel_with_the_packager_closed(doc):
    """The measured result, and the one that scopes F2.

    F1 rewrites the container through our own writer but edits the XML only by
    deletion, so the prediction was that the packager channel closes and the model
    channel does not. It holds: `create_system`, `external_attrs`, `flags`,
    `order_policy` and `timestamp_scheme` all separate producers on untouched files
    and none of them survives F1, while every model key does.
    """
    cell = _cell(doc, "A2", "F1")
    assert cell["verdict"] == "fail"
    reason = cell["reason"]
    assert "model:" in reason, reason
    leaking = {leak["locus"]["feature_id"] for leak in cell["leaks"]}
    assert leaking, "a failing A2 cell must name what leaked"
    assert not (leaking & set(PACKAGER_KEYS)), (
        f"the packager channel was supposed to close at F1: {leaking & set(PACKAGER_KEYS)}")
    assert leaking & set(MODEL_KEYS), "the model channel should still be open"


# The model channel splits in two, and F2 is the line between them.
SPELLING_KEYS = {"struct:xml_decl", "struct:selfclose_style", "struct:namespaces"}
SUBSTANCE_KEYS = {"struct:entry_set", "struct:style_ids", "struct:settings_keys",
                  "struct:paragraph_idiom", "struct:sectpr", "struct:part_types",
                  "struct:has_theme"}


def test_a1_at_f2_passes(doc):
    assert _cell(doc, "A1", "F2")["verdict"] == "pass"


def test_a2_at_f2_closes_the_spelling_and_names_the_substance(doc):
    """The honest A2-at-F2 answer, and the reason the keys are reported individually.

    A channel verdict alone would read the same whether F2 had collapsed most of the
    model channel or none of it. It collapsed the half that is *spelling* — the
    namespace set, the self-closing style, the XML declaration — and left the half
    that is *substance*: which parts a producer emits, which styles it defines, what
    it writes into `settings.xml`, how it represents a paragraph, its section
    properties. None of that can change without changing what the reader sees, which
    is the same argument that makes glyph geometry PDF's floor.
    """
    cell = _cell(doc, "A2", "F2")
    assert cell["verdict"] == "fail"
    leaking = {leak["locus"]["feature_id"] for leak in cell["leaks"]}
    assert not (leaking & SPELLING_KEYS), (
        f"F2 was supposed to close the spelling keys: {leaking & SPELLING_KEYS}")
    assert leaking & SUBSTANCE_KEYS, (
        "if the substance keys ever stop leaking, F2 has started changing the "
        "document — check content preservation before celebrating")


def test_f2_closed_keys_that_f1_left_open(doc):
    """The tier has to buy something. Measured rather than assumed: the keys leaking
    at F1 must be a strict superset of those leaking at F2."""
    f1_keys = {leak["locus"]["feature_id"] for leak in _cell(doc, "A2", "F1")["leaks"]}
    f2_keys = {leak["locus"]["feature_id"] for leak in _cell(doc, "A2", "F2")["leaks"]}
    assert f2_keys < f1_keys, (f1_keys, f2_keys)
    assert SPELLING_KEYS <= (f1_keys - f2_keys)


def test_the_cell_says_word_is_not_in_the_peer_set(doc):
    """Word has no CLI on any platform, so it cannot render *our* document. Putting
    an unrelated Word file in the set would have the classifier separating documents
    while appearing to separate producers — so it is named as absent in the cell's
    own reason, the limit-#12 precedent."""
    reason = _cell(doc, "A2", "F1")["reason"]
    assert "msword" in reason and "cannot render our document" in reason


def test_every_a2_cell_names_its_peer_set(doc):
    for cell in doc["cells"]:
        if cell["adversary"] != "A2" or cell["verdict"] == "not_tested":
            continue
        assert "peer set" in cell["reason"], cell


def test_f3_is_measured_where_the_engine_exists_and_untested_where_it_does_not(doc):
    """F3 re-typesets through LibreOffice. Where that is installed the tier runs and
    the cells are real; where it is not, they must say *not measured* — never clean,
    and never silently absent, since an empty cell reads like a measurement nobody
    took (limit #12)."""
    from src.scrub.formats.docx import f3
    for adv in ("A1", "A2"):
        cell = _cell(doc, adv, "F3")
        if f3.available():
            assert cell["verdict"] in ("pass", "fail"), cell
        else:
            assert cell["verdict"] == "not_tested"
            assert "not installed" in cell["reason"]


@pytest.mark.skipif(not __import__("src.scrub.formats.docx.f3",
                                   fromlist=["f3"]).available(),
                    reason="LibreOffice absent")
def test_a2_at_f3_narrows_the_model_channel_to_a_primary_production_trace(doc):
    """The M14 result. A re-typeset regenerates the model rather than relocating it,
    so the channel collapses to a single key — and that key describes the *source*
    document's styles, which the engine preserves rather than rebuilds, not the
    producer that handed us the file."""
    f2_keys = {leak["locus"]["feature_id"] for leak in _cell(doc, "A2", "F2")["leaks"]}
    f3_keys = {leak["locus"]["feature_id"] for leak in _cell(doc, "A2", "F3")["leaks"]}
    assert f3_keys < f2_keys, (f2_keys, f3_keys)
    assert not (f3_keys & set(PACKAGER_KEYS))
    assert len(f3_keys & set(MODEL_KEYS)) <= 1, (
        f"F3 was measured leaving more than one model key: {f3_keys & set(MODEL_KEYS)}")


def test_fingerprint_guard_passes(doc):
    assert doc["scrubber_fingerprint"]["verdict"] == "pass"
    assert doc["scrubber_fingerprint"]["checked"] is True


def test_the_guard_would_fail_on_an_undiverse_corpus(tmp_path):
    """Proof the guard is live rather than vacuously green.

    It **did** fail, on the first run, because the guard's own input corpus varied
    only its paragraph text — leaving `[Content_Types].xml` and `_rels/.rels`
    byte-identical across every input, so their whole central-directory records read
    as our signature. The guard was right and the corpus was wrong. This reproduces
    that condition so the pass above cannot quietly become meaningless if
    `diverse_inputs` is ever simplified.
    """
    from tests.harness import config
    from tests.harness.oracle import fingerprint_guard
    from tests.harness.plugins.docx import DocxPlugin

    same_shape = [C.synthetic(str(tmp_path / f"s{i}.docx"), f"body {i}\n")
                  for i in range(4)]
    verdict, _sig = fingerprint_guard.evaluate(
        gen_matrix_docx._scrubber(), DocxPlugin(), same_shape, "F1",
        min_len=config.MIN_SIG_LEN)
    assert verdict == "fail", (
        "a corpus whose packages share a part set must trip the guard — if it does "
        "not, the guard is no longer measuring anything")


def test_the_declared_skeleton_is_our_own_empty_output():
    """The broad declaration is generated, never transcribed: it is exactly what our
    writer emits for a document with no content, so any run common to every output
    that is a substring of it is structure by construction.

    It was added because the guard **failed the moment F2 landed**, reporting the
    complete entry for `_rels/.rels` — header, name, compressed bytes and CRC — as
    our signature. That one is real convergence rather than a corpus artefact: every
    package's root relationships part ends up declaring one relationship once
    `docProps/*` is dropped, and canonicalisation then gives them all the same bytes.
    """
    import tempfile

    from tests.harness.plugins.docx import empty_package_skeleton
    from tests.scrub.e_docx_loci import census
    for fid in ("F1", "F2"):
        skeleton = empty_package_skeleton(fid)
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as fh:
            fh.write(skeleton)
            path = fh.name
        # A document with no content must contain no loci at all -- if the skeleton
        # ever carries one, declaring it would hide a real leak.
        assert not census(path)["findings"], fid


def test_declared_constants_hide_nothing_that_matters():
    """The exclusion list is checked rather than trusted: every declared constant must
    be pure structure — no producer string, no timestamp, no padding run."""
    import re as _re

    from tests.harness.plugins.docx import DocxPlugin

    # A date, not a year. The OPC schema URLs contain `2006` because that is part of
    # the standard's own name -- an earlier version of this test forbade the substring
    # "20" and failed on exactly that, which would have been a false alarm about a
    # constant every conformant package on earth contains.
    date_like = _re.compile(rb"\d{4}-\d{2}-\d{2}|\d{2}:\d{2}:\d{2}")

    for const in DocxPlugin().mandatory_constants():
        text = const.decode("latin-1", "replace").lower()
        if len(const) > 400:
            # The generated skeletons are whole packages, so their deflated bytes can
            # contain any byte sequence by chance. They are checked by the test above,
            # which asserts the document they came from carries no loci at all.
            continue
        for producer in ("microsoft", "libreoffice", "cocoa", "python", "mat2",
                         "aptos", "normal.dotm"):
            assert producer not in text, f"{const!r} names a producer: {producer!r}"
        assert not date_like.search(const), f"{const!r} contains a timestamp"
        assert b"\x00" * 8 not in const, "a padding run is not structure"
