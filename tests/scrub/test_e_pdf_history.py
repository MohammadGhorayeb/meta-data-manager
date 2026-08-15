"""E-PDF-HISTORY: incremental-update history, and what actually removes it.

Guards the experiment before the result, as Phase 2 established: an attack that
cannot recover history from an untouched file proves nothing when it also fails on
a cleaned one. So the controls come first — the corpus really does hide three
revisions, and all three attacks really do find them.

The benchmark rows (MAT2, ExifTool) are asserted here rather than only reported,
because they are the claims `docs/benchmark.md` publishes. If a future version of
either tool changes behaviour, this test going red is the correct outcome: the
published claim has stopped being true and must be re-measured.
"""
from __future__ import annotations

import pytest

from tests.scrub import e_pdf_history as e
from tests.scrub import pdf_corpus as pc

pytestmark = pytest.mark.skipif(not pc.HAVE_PDFTOTEXT,
                                reason="needs pdftotext (poppler) to read revisions")


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    return e.run(str(tmp_path_factory.mktemp("e_pdf_history")))


# --------------------------------------------------------------------------- #
# Controls
# --------------------------------------------------------------------------- #
def test_corpus_hides_its_history(tmp_path):
    """CONTROL: the visible document shows only the final text, as a user expects."""
    p = pc.incremental_pdf(str(tmp_path / "h.pdf"), n_revisions=3)
    visible = pc.pdftotext(p)
    assert "Public release text" in visible
    assert "CONFIDENTIAL" not in visible, "the corpus leaks in plain sight, not by history"


def test_control_all_three_attacks_recover_the_history(results):
    """CONTROL: rollback, carving and the object ledger each find the earlier drafts."""
    raw = results["raw"]
    assert raw["revisions"] == 3, "corpus should carry three revisions"
    assert raw["prev_chain"] == 3, "/Prev should link every revision"
    assert raw["ledger"]["stale_definitions"] == 4, (
        "two objects replaced twice each = four superseded definitions")
    assert set(raw["carved"]) == {s.decode() for s in e.SECRETS}
    rolled = " ".join(r["text"] for r in raw["rolled_back"])
    assert "CONFIDENTIAL-REV1-SECRET" in rolled, (
        "revision 1's text must be recoverable by truncation, or the attack is vacuous")
    assert any("Author-REV1" in v for v in raw["prior_metadata"])


def test_revision_counter_ignores_eof_inside_streams():
    """`%%EOF` occurs inside compressed streams by chance; `startxref … %%EOF` does not.

    Counting the former would report history in files that have none — measured on
    MAT2 output, which is why the counter is written this way.
    """
    fake = b"%PDF-1.4\nstream\n%%EOF junk %%EOF\nendstream\nstartxref\n9\n%%EOF\n"
    assert len(e.revision_ends(fake)) == 1


def test_ledger_separates_superseded_from_orphaned(tmp_path):
    """An orphan-only check would call the raw corpus clean: every object number in it
    is still reachable. What is stale there is the *earlier definition* of a live
    number, which is what an incremental update leaves behind."""
    p = pc.incremental_pdf(str(tmp_path / "h.pdf"), n_revisions=3)
    ledger = e.object_ledger(p)
    assert ledger["superseded"] == {5: 2, 6: 2}
    assert ledger["orphaned"] == []


# --------------------------------------------------------------------------- #
# The mechanism that works
# --------------------------------------------------------------------------- #
def test_full_rewrite_is_what_removes_history(results):
    """A whole-document rewrite collapses the file to one revision and nothing is
    recoverable — no metadata was stripped to achieve that.

    This is the mechanism W3 requires of our F1, measured on its own before any
    scrubbing is layered on top, so a later F1 pass cannot be credited to the wrong
    half of the work.
    """
    r = results["pikepdf_rewrite"]
    assert r["revisions"] == 1
    assert r["ledger"]["stale_definitions"] == 0
    assert r["recovered"] == []
    assert r["text_preserved"], "the rewrite must not change the visible document"


def test_appending_a_cleanup_would_not_work(tmp_path):
    """The negative control for the above: 'scrub' by appending a clean /Info, exactly
    as a naive implementation would, and watch every secret stay recoverable."""
    p = pc.incremental_pdf(str(tmp_path / "h.pdf"), n_revisions=3)
    data = open(p, "rb").read()
    prev = int(data.rsplit(b"startxref\n", 1)[1].split(b"\n", 1)[0])
    appended = pc._revision(
        data, [(6, pc._obj(6, b"<< >>"))], size=7, root=1, info=6, prev=prev)
    out = tmp_path / "appended.pdf"
    out.write_bytes(appended)
    r = e.attack(str(out), e.SECRETS)
    assert r["revisions"] == 4
    assert set(r["carved"]) == {s.decode() for s in e.SECRETS}, (
        "appending a cleanup cannot remove anything — that is the whole point of W3")


# --------------------------------------------------------------------------- #
# What this experiment does NOT close
# --------------------------------------------------------------------------- #
def test_redaction_is_a_different_leak(tmp_path):
    """A single-revision file with text under an opaque box has no history at all, so
    collapsing revisions changes nothing about it. Recorded so the phase never reports
    'history removed' as if it meant 'the redaction holds'."""
    p = pc.redacted_pdf(str(tmp_path / "r.pdf"))
    r = e.attack(p, [b"CONFIDENTIAL-SECRET"])
    assert r["revisions"] == 1 and r["ledger"]["stale_definitions"] == 0
    assert r["rolled_back"] == [], "nothing to roll back — there is no history here"
    assert r["carved"] == ["CONFIDENTIAL-SECRET"], "yet the text is still in the file"
    assert "CONFIDENTIAL" in r["visible_text"], "and pdftotext reads it straight out"


# --------------------------------------------------------------------------- #
# Benchmark rows
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not e.HAVE_EXIFTOOL, reason="exiftool not installed")
def test_exiftool_edits_by_appending_and_removes_nothing(results):
    """ExifTool writes PDF metadata as an incremental update, so `-all=` *adds* a
    revision and every original value stays readable. ExifTool says so itself
    ("PDF edits are reversible. Deleted tags may be recovered!"), and this measures it
    on our corpus rather than repeating the warning."""
    r = results["exiftool_all"]
    assert r["revisions"] == 4, "expected one more revision than it started with"
    assert set(r["carved"]) == {s.decode() for s in e.SECRETS}
    assert any("Author-FINAL" in v for v in r["prior_metadata"]), (
        "the /Info it cleared is still recoverable one revision down")


@pytest.mark.skipif(not e.HAVE_MAT2, reason="mat2 not installed")
@pytest.mark.parametrize("variant", ["mat2", "mat2_lightweight"])
def test_mat2_destroys_the_document_history_but_leaks_its_own(results, variant):
    """MAT2 re-renders, so the document's own history genuinely goes — a real result,
    and the reason it beats ExifTool here.

    But it then clears `/Info` by **appending an incremental update**, leaving its own
    producer string and a wall-clock creation date one revision down. Rolling back one
    revision names the tool that made the file and the second it was made, complete
    with the operator's UTC offset. Under this project's own definition of done
    (no producer string, no mtime stamping) that output fails the fingerprint guard.
    """
    r = results[variant]
    assert r["recovered"] == [], "the document's planted secrets are gone — credit due"
    assert r["revisions"] == 2, "but the output is itself a two-revision file"
    joined = " ".join(r["prior_metadata"])
    assert "cairo" in joined, "MAT2's own producer string survives one revision down"
    assert "/CreationDate=" in joined, "as does a wall-clock timestamp of the scrub"


@pytest.mark.skipif(not e.HAVE_MAT2, reason="mat2 not installed")
def test_mat2_default_path_destroys_the_text(results):
    """The cost of MAT2's default path, measured: the text layer does not survive.
    `--lightweight` keeps it, which is why `docs/benchmark.md` must name the path."""
    assert not results["mat2"]["text_preserved"]
    assert results["mat2_lightweight"]["text_preserved"]
