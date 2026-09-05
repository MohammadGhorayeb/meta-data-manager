"""Tests for the W9/M7 locus census — the list F1 will be written against.

The census is only worth what its rule table is worth, so the tests are aimed at the
two ways a rule table goes wrong:

- a rule that **never fires**, which is a guess nobody checked;
- a locus with **no rule**, which is where a leak hides.

Both are asserted, in that order.
"""
from __future__ import annotations

import re

import pytest

from . import docx_corpus as C
from . import e_docx_loci as L


@pytest.fixture(scope="module")
def torture(tmp_path_factory):
    return C.torture(str(tmp_path_factory.mktemp("loci") / "torture.docx"))


@pytest.fixture(scope="module")
def real(tmp_path_factory):
    return C.producers(str(tmp_path_factory.mktemp("lociprod")))


def _all_rule_ids() -> set[str]:
    return ({loc.id for _, loc in L.PART_RULES}
            | {loc.id for _, loc in L.ATTR_RULES}
            | {loc.id for _, loc in L.ELEM_RULES}
            | {loc.id for *_, loc in L.CONTEXT_ATTR_RULES}
            | {loc.id for *_, loc in L.CONSTRUCT_RULES})


def _found_ids(result: dict) -> set[str]:
    return ({loc.id for _, loc in result["parts"]}
            | {f.locus.id for f in result["findings"]})


def test_every_rule_in_the_table_actually_fires(torture):
    """No rule is a guess nobody checked. The torture package exists to make this
    assertion possible without Microsoft Word: it contains one instance of every
    locus the table names."""
    never = sorted(_all_rule_ids() - _found_ids(L.census(torture)))
    assert not never, f"rules that never fire against the torture package: {never}"


def test_an_unruled_part_is_reported_rather_than_ignored(torture):
    """The hole detector. `word/undeclared.bin` has no rule on purpose; if the census
    ever stops reporting it, an unknown part could reach F1 unexamined."""
    assert L.census(torture)["unclassified"] == ["word/undeclared.bin"]


def test_real_producers_have_no_unclassified_parts(real):
    """Nothing a real producer writes is outside the table."""
    for name, path in real.items():
        assert not L.census(path)["unclassified"], name


def test_bookmark_rule_does_not_match_font_and_style_names(torture):
    """Regression on a bug the census caught in itself. `w:name` is reused across
    `w:font`, `w:style` and `w:compatSetting`, so a context-free rule reported it 386
    times in one document and would have had F1 deleting font names — content, not
    metadata. The torture package carries both kinds; only the bookmark counts.
    """
    hits = [f for f in L.census(torture)["findings"]
            if f.locus.id == "w:bookmarkStart/@w:name"]
    assert len(hits) == 1 and hits[0].count == 1
    assert hits[0].sample == "_GoBack"


def test_sensitive_values_are_actually_present_to_be_removed(torture):
    """The torture package must really contain the strings F1 will have to remove;
    a corpus that never had the secret cannot prove the scrub took it out."""
    findings = L.census(torture)["findings"]
    samples = " ".join(f.sample for f in findings)
    assert C.TORTURE_SENTINEL in samples
    ids = {f.locus.id for f in findings}
    for expected in ("w:rsid*", "w14:paraId/textId", "w15:docId",
                     "w:documentProtection", "w:mailMerge", "author/date attrs"):
        assert expected in ids, expected


# --------------------------------------------------------------------------- #
# The E-SESSION-ID precursor
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not C.HAVE_WORD or not C.HAVE_MAT2,
                    reason="needs Word samples and mat2")
def test_mat2_clears_rsids_but_leaves_the_paragraph_ids(real):
    """The §2.6 probe, re-measured through an independent mechanism.

    This is **not** E-SESSION-ID — one document, one MAT2 version, no controls. It is
    here so the finding cannot quietly rot: the project inherited "RSIDs survive every
    scrubber including MAT2" from the literature, and if that stops being false again
    this test says so.
    """
    word = _found_ids(L.census(real["msword"]))
    mat2 = _found_ids(L.census(real["mat2_out"]))
    assert "w:rsid*" in word and "w:rsids" in word
    assert "w:rsid*" not in mat2, "MAT2 now leaves RSIDs; §2.6 needs revisiting"
    assert "w14:paraId/textId" in mat2, "MAT2 now clears paraId; §2.6 needs revisiting"
    assert "w15:docId" in mat2


# --------------------------------------------------------------------------- #
# The hole detector, generalised from parts to names
# --------------------------------------------------------------------------- #
_ID_SHAPED = re.compile(
    r"^(?:[A-Za-z_][\w.-]*:)?"
    r"(?:.*(?:docId|paraId|textId|rsid|rsidRoot|Guid|GUID|uuid)).*$")

# Names that LOOK like identifiers and are content, with the reason each is content.
# The list is short on purpose: anything not here must be ruled or the test fails.
_NOT_METADATA = {
    "w:rsidTbl": "a table of session ids -- ruled via w:rsids",
}


def _names_in(path: str) -> set[str]:
    from src.scrub.formats.ooxml import opc as _opc
    pkg = _opc.parse(open(path, "rb").read())
    names: set[str] = set()
    for e in pkg.archive.entries:
        if not e.name.endswith(".xml"):
            continue
        body = e.content()
        names |= {m.decode() for m in
                  re.findall(rb"<([A-Za-z_][\w.-]*:[\w.-]+)", body)}
        names |= {m.decode() for m in
                  re.findall(rb'\s([A-Za-z_][\w.-]*:[\w.-]+)\s*=\s*"', body)}
    return names


def test_no_identifier_family_has_an_unruled_sibling(real, torture):
    """Microsoft versions its namespaces — `w14` (Word 2010), `w15` (2012), `w16`
    (2021) — and writes the *same* element in several of them at once.

    This was not hypothetical. `w15:docId` was ruled and `w14:docId` was not, and a
    real Word document carried both, so F1 removed one and left the other. Rather
    than patch the one and move on, this scans every identifier-shaped name in the
    corpus and requires each to be covered by a rule, so the next `w16:` sibling
    fails a test instead of shipping.
    """
    from src.scrub.formats.docx import f1 as docx_f1

    ruled = set(docx_f1.DROP_ELEMENTS)
    attr_rx = re.compile(docx_f1.DROP_ATTRS)

    unruled = set()
    for path in [*real.values(), torture]:
        for name in _names_in(path):
            if not _ID_SHAPED.match(name) or name in _NOT_METADATA:
                continue
            if name in ruled or attr_rx.fullmatch(name):
                continue
            unruled.add(name)
    assert not unruled, (
        f"identifier-shaped names with no rule: {sorted(unruled)} — either add them "
        f"to DROP_ELEMENTS/DROP_ATTRS or record in _NOT_METADATA why they are content")
