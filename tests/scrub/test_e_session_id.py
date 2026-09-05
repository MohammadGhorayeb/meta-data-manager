"""E-SESSION-ID and E-DOCX-THUMB — the benchmark rows, measured on our own corpus.

The claim this project inherited was *"OOXML RSIDs survive every surveyed scrubber
including MAT2"*. It was never measured here, and it is **false against MAT2 0.14.0**.
What is true is narrower and more interesting, so these tests pin down both halves:
the literature's leak is fixed upstream, and a family nobody names is not.

Controls first, always: a tool cannot be shown to have missed something the input
never contained.
"""
from __future__ import annotations

import zipfile

import pytest

from tests.scrub import docx_corpus as C
from tests.scrub import e_session_id as E


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    tmpdir = str(tmp_path_factory.mktemp("session_id"))
    return E.run(E.build_sources(tmpdir), tmpdir), tmpdir


def test_the_control_holds_every_family_is_present_in_the_input(results):
    """Without this, every assertion below could pass on an empty file."""
    res, _ = results
    before = res["synthetic"]["_before"]
    for family, count in before.items():
        assert count > 0, f"{family} absent from the corpus — nothing to measure"


def test_our_f1_clears_every_family(results):
    res, _ = results
    for source, row in res.items():
        got = row["ours_F1"]
        assert got is not None, f"{source}: we refused a package we should handle"
        assert set(got.values()) == {0}, f"{source}: {got}"


@pytest.mark.skipif(not E.HAVE_MAT2, reason="mat2 absent")
def test_mat2_clears_the_rsids_and_leaves_the_paragraph_and_document_ids(results):
    """**The correction to the inherited claim, with controls.**

    RSIDs — the leak the literature names — are gone. What MAT2 leaves is the family
    nobody names: `w14:paraId` and `w14:textId` on every paragraph, and the
    per-document `docId` GUID. The paragraph ids are the sharper of the two, because
    they travel with a paragraph pasted into another document and therefore link
    *files to each other* rather than a file to a producer — an attack the A1/A2/A3
    ladder does not model at all.
    """
    res, _ = results
    for source, row in res.items():
        got = row["mat2"]
        if got is None:
            pytest.skip(f"mat2 declined {source}")
        assert got["rsid_attrs"] == 0 and got["rsids_pool"] == 0
        assert got["rsid_in_style"] == 0
        assert got["paraId"] > 0, "MAT2 now clears paraId — §2.6/§2.13 need revisiting"
        assert got["textId"] > 0
        assert got["docId"] > 0


@pytest.mark.skipif(not E.HAVE_EXIFTOOL, reason="exiftool absent")
def test_exiftool_cannot_write_docx_at_all(results):
    """A capability gap, not a failure — and the distinction matters. A tool that
    declines leaves the file untouched, which is honest; a tool that writes an output
    still carrying the ids has told the user their document is clean when it is not.
    """
    res, _ = results
    for source, row in res.items():
        assert row["exiftool"] is None, f"{source}: exiftool wrote DOCX output now"
        assert "write" in (row["exiftool__why"] or "").lower()


@pytest.mark.skipif(not C.HAVE_WORD, reason="Word samples absent")
def test_the_result_reproduces_on_a_real_word_document(results):
    """The synthetic corpus measures what a tool *does*. Only a real document shows
    that Word writes these ids in the first place — which is the half CI cannot
    check, and is therefore reported as measured-locally rather than dropped."""
    res, _ = results
    real = [k for k in res if k.startswith("msword")]
    assert real, "Word samples were present but produced no rows"
    for source in real:
        before = res[source]["_before"]
        assert before["rsid_attrs"] > 0 and before["rsid_in_style"] > 0
        assert before["paraId"] > 0 and before["docId"] > 0
        assert set(res[source]["ours_F1"].values()) == {0}


# --------------------------------------------------------------------------- #
# E-DOCX-THUMB
# --------------------------------------------------------------------------- #
def test_the_thumbnail_and_its_own_exif_both_go(tmp_path):
    """`docProps/thumbnail.jpeg` is a rendered picture of the document's own first
    page **with its own EXIF** — two leaks in one part, which is why it is dropped
    whole rather than scrubbed in place."""
    from src.scrub.formats.docx import f1
    from src.scrub.formats.ooxml import opc

    from . import corpus as imgc
    img = imgc.build_torture_jpeg()
    src = str(tmp_path / "thumb.docx")
    C.synthetic(src)
    with zipfile.ZipFile(src, "a", zipfile.ZIP_DEFLATED) as z:
        zi = zipfile.ZipInfo("docProps/thumbnail.jpeg", (1980, 1, 1, 0, 0, 0))
        zi.compress_type = zipfile.ZIP_DEFLATED
        z.writestr(zi, img)

    assert b"Exif" in img, "control: the thumbnail must carry EXIF to begin with"
    out = f1.scrub(open(src, "rb").read())
    assert "docProps/thumbnail.jpeg" not in opc.parse(out).parts()
    # And nothing of it survives anywhere else in the package.
    for part in opc.parse(out).parts():
        assert b"Exif" not in opc.parse(out).archive.by_name(part).content()


@pytest.mark.skipif(not E.HAVE_MAT2, reason="mat2 absent")
def test_mat2_cleans_the_thumbnails_exif_but_keeps_the_thumbnail(tmp_path):
    """Measured, and the expectation was wrong before the measurement.

    MAT2 recurses into `docProps/thumbnail.jpeg` and strips its EXIF — good — but
    **keeps the picture**. That is defensible on its face: a thumbnail renders the
    document's own first page, so it appears to show nothing the document does not
    already show.

    Two things make it a leak anyway, which is why we drop the part outright:

    1. **It can be stale.** The thumbnail is written when the document is saved by an
       application that produces one, and a later edit through a tool that does not
       refresh it leaves a picture of a page that no longer exists. That is the DOCX
       relative of PDF's incremental-update history: content the document no longer
       displays, still inside the file.
    2. **It is a rendering**, so it carries the producing application's typesetting in
       pixel space — the residual E-PDF-RASTER measured at 100% producer
       identification for PDF. Keeping it hands that channel back after the XML has
       been normalised.
    """
    from src.scrub.formats.docx import f1
    from src.scrub.formats.ooxml import opc

    from . import corpus as imgc
    src = str(tmp_path / "thumb2.docx")
    C.synthetic(src)
    with zipfile.ZipFile(src, "a", zipfile.ZIP_DEFLATED) as z:
        zi = zipfile.ZipInfo("docProps/thumbnail.jpeg", (1980, 1, 1, 0, 0, 0))
        zi.compress_type = zipfile.ZIP_DEFLATED
        z.writestr(zi, imgc.build_torture_jpeg())

    out = str(tmp_path / "m.docx")
    ok, _why = E._mat2(src, out)
    if not ok:
        pytest.skip("mat2 declined the package")
    data = open(out, "rb").read()
    parts = opc.parse(data).parts()
    assert "docProps/thumbnail.jpeg" in parts, (
        "MAT2 now drops the thumbnail — the benchmark row needs updating")
    assert b"Exif" not in data, "MAT2 no longer scrubs the thumbnail's own EXIF"

    # Ours removes the part itself, which is the difference the row records.
    assert "docProps/thumbnail.jpeg" not in opc.parse(
        f1.scrub(open(src, "rb").read())).parts()
