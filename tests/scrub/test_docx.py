"""M10 — DOCX F1: surgical deletion, recursive, fail-closed.

The tests are organised around what F1 promises, in the order the promises matter:

1. the document still opens and its text is byte-identical (content preservation);
2. the loci the M7 census named are gone, checked with the census rather than by
   grepping for the fields we happened to remember;
3. the package is still internally consistent — no relationship or content-type
   override pointing at a part we removed;
4. what F1 cannot handle is refused, with a reason, and writes nothing.
"""
from __future__ import annotations

import shutil
import subprocess
import zipfile

import pytest

from src.scrub import cli
from src.scrub.errors import ContentError, ParseError
from src.scrub.formats.docx import f1
from src.scrub.formats.ooxml import opc

from . import docx_corpus as C
from . import e_docx_loci as L


@pytest.fixture(scope="module")
def real(tmp_path_factory):
    return C.producers(str(tmp_path_factory.mktemp("docxf1")))


def _text(data: bytes) -> str:
    """Parse, never match. See `src/scrub/formats/docx/textextract.py` for why the
    obvious regex reports a text change that did not happen."""
    from src.scrub.formats.docx import textextract
    return textextract.document_text(data)


# --------------------------------------------------------------------------- #
# 1. Content preservation
# --------------------------------------------------------------------------- #
def test_document_text_is_byte_identical(real):
    for name, path in real.items():
        data = open(path, "rb").read()
        assert _text(f1.scrub(data)) == _text(data), name


@pytest.mark.skipif(shutil.which("soffice") is None, reason="soffice absent")
def test_the_output_still_opens(real, tmp_path):
    """The acceptance test is not "did we write a file" but "does it still open".
    Dropping a part without dropping its relationship gives Word the "unreadable
    content" dialog, and a scrub that destroys the document while reporting success
    is the worst failure this tier has."""
    for name, path in real.items():
        out = tmp_path / f"{name}.docx"
        out.write_bytes(f1.scrub(open(path, "rb").read()))
        sub = tmp_path / f"pdf_{name}"
        sub.mkdir()
        subprocess.run(["soffice", "--headless", "--convert-to", "pdf",
                        "--outdir", str(sub), str(out)],
                       capture_output=True, timeout=180, check=False)
        rendered = sub / f"{name}.pdf"
        assert rendered.exists() and rendered.stat().st_size > 0, name


def test_surviving_parts_keep_their_bytes_except_what_was_deleted(real):
    """F1 means we only delete. A part we did not target must come out either
    identical or strictly shorter — never rewritten, which is what parsing and
    re-serialising would do and which belongs to F2."""
    for name, path in real.items():
        before = opc.parse(open(path, "rb").read())
        after = opc.parse(f1.scrub(open(path, "rb").read()))
        for part in after.parts():
            b = before.archive.by_name(part).content()
            a = after.archive.by_name(part).content()
            assert len(a) <= len(b), f"{name}/{part} grew: F1 rewrote something"


# --------------------------------------------------------------------------- #
# 2. The loci are gone — checked with the census, not from memory
# --------------------------------------------------------------------------- #
def test_no_metadata_locus_survives_on_any_producer(real, tmp_path):
    for name, path in real.items():
        out = tmp_path / f"c_{name}.docx"
        out.write_bytes(f1.scrub(open(path, "rb").read()))
        found = {f.locus.id for f in L.census(str(out))["findings"]}
        assert not found, f"{name}: {sorted(found)}"


@pytest.mark.skipif(not C.HAVE_WORD, reason="Word samples absent")
def test_the_session_id_family_mat2_leaves_is_gone(real):
    """The phase's actual target. MAT2 clears `w:rsid*` and leaves `w14:paraId`,
    `w14:textId` and `w15:docId` (§2.6, §2.8); F1 must clear all of them."""
    out = f1.scrub(open(real["msword"], "rb").read())
    pkg = opc.parse(out)
    for part in pkg.parts():
        body = pkg.archive.by_name(part).content()
        for token in (b"w:rsid", b"paraId", b"textId", b"docId"):
            assert token not in body, f"{part}: {token!r}"
    # And a raw sweep over the whole archive, which is weaker (deflate hides strings
    # on its own) but is the check a forensic reader would actually run.
    for token in (b"w:rsid", b"paraId", b"docId"):
        assert token not in out, token


def test_the_torture_package_comes_out_clean(tmp_path):
    """Every locus the rule table knows about, in one file, removed in one pass —
    minus the parts F1 refuses, which are removed here so the rest can be tested."""
    src = C.torture(str(tmp_path / "t.docx"))
    trimmed = str(tmp_path / "trimmed.docx")
    _drop(src, trimmed, lambda n: (n.startswith("word/comments")
                                   or n.startswith("word/embeddings/")
                                   or n.endswith("vbaProject.bin")
                                   or n == "word/header1.xml"      # carries a DOCTYPE
                                   or n == "word/undeclared.bin"))
    data = open(trimmed, "rb").read()
    # The tracked changes live inside document.xml, so they are refused until removed.
    data = _strip_tracked(data)
    out = f1.scrub(data)

    assert C.TORTURE_SENTINEL.encode() not in out
    cleaned = opc.parse(out)
    for part in cleaned.parts():
        assert C.TORTURE_SENTINEL.encode() not in cleaned.archive.by_name(
            part).content(), part
    outp = str(tmp_path / "clean.docx")
    open(outp, "wb").write(out)
    assert not {f.locus.id for f in L.census(outp)["findings"]}


def _drop(src: str, dst: str, predicate) -> None:
    zin = zipfile.ZipFile(src)
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            if predicate(item.filename):
                continue
            zi = zipfile.ZipInfo(item.filename, (1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zout.writestr(zi, zin.read(item.filename))


def _strip_tracked(data: bytes) -> bytes:
    from src.scrub.formats.ooxml import xmlsurgery as xs
    from src.scrub.formats.ooxml import zipwrite
    pkg = opc.parse(data)
    parts = {}
    for e in pkg.archive.entries:
        body = e.content()
        if e.name == "word/document.xml":
            for tag in ("w:ins", "w:del", "w:commentRangeStart", "w:commentRangeEnd",
                        "w:commentReference"):
                body, _ = xs.remove_elements(body, tag)
        parts[e.name] = body
    return zipwrite.write(parts)


# --------------------------------------------------------------------------- #
# 3. The package stays consistent
# --------------------------------------------------------------------------- #
def test_no_relationship_or_override_points_at_a_removed_part(real):
    for name, path in real.items():
        pkg = opc.parse(f1.scrub(open(path, "rb").read()))
        assert not pkg.orphans(set(pkg.parts())), name
        for override in pkg.overrides:
            assert override.lstrip("/") in pkg.parts(), f"{name}: {override}"


def test_an_empty_rels_part_is_removed_rather_than_left_behind(tmp_path):
    """A `.rels` part that now declares nothing is itself a tell that something was
    taken out of it."""
    from src.scrub.formats.ooxml import zipwrite
    doc_rels = C._ROOT_RELS.replace(
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships'
        '/officeDocument" Target="word/document.xml"',
        'Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata'
        '/core-properties" Target="../docProps/core.xml"')
    parts = {
        "[Content_Types].xml": C._CONTENT_TYPES.encode(),
        "_rels/.rels": C._ROOT_RELS.encode(),
        "word/document.xml": C._document_xml(C.SOURCE_TEXT).encode(),
        "word/_rels/document.xml.rels": doc_rels.encode(),
        "docProps/core.xml": C._TORTURE_CORE.encode(),
    }
    out = f1.scrub(zipwrite.write(parts))
    kept = opc.parse(out).parts()
    assert "docProps/core.xml" not in kept
    assert "word/_rels/document.xml.rels" not in kept, "an emptied .rels survived"


def test_an_input_that_is_already_inconsistent_is_refused_not_blamed_on_us(tmp_path):
    """A relationship pointing at a part the package does not contain is damage in
    the INPUT. F1 must say so, rather than reporting its own output as broken for
    something it inherited -- which is what it did before this test existed."""
    from src.scrub.formats.ooxml import zipwrite
    parts = {
        "[Content_Types].xml": C._CONTENT_TYPES.encode(),
        "_rels/.rels": C._ROOT_RELS.encode(),
        "word/document.xml": C._document_xml(C.SOURCE_TEXT).encode(),
        "word/_rels/document.xml.rels": C._ROOT_RELS.replace(
            'Target="word/document.xml"', 'Target="../docProps/absent.xml"').encode(),
    }
    with pytest.raises(ContentError, match="input already has relationships"):
        f1.scrub(zipwrite.write(parts))


# --------------------------------------------------------------------------- #
# 4. Fail closed, with a reason
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("parts,expect", [
    ({"word/vbaProject.bin": b"\xd0\xcf\x11\xe0"}, "macro"),
    ({"word/embeddings/o.bin": b"\xd0\xcf\x11\xe0"}, "OLE"),
    ({"_xmlsignatures/sig1.xml": b"<sig/>"}, "signed"),
    ({"word/comments.xml": b"<w:comments/>"}, "comments are content"),
])
def test_refusals_name_their_reason(tmp_path, parts, expect):
    src = str(tmp_path / "r.docx")
    C.synthetic(src)
    with zipfile.ZipFile(src, "a", zipfile.ZIP_DEFLATED) as z:
        for name, body in parts.items():
            zi = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, body)
    with pytest.raises((ContentError, ParseError), match=expect):
        f1.scrub(open(src, "rb").read())


def test_tracked_changes_are_refused_because_they_are_content(tmp_path):
    """Accepting all revisions would change what a reader sees with markup on — a
    content-preservation failure wearing a scrub's clothes. Refused at F1; F2 is where
    the tier licenses a rewrite."""
    src = C.torture(str(tmp_path / "t.docx"))
    with pytest.raises((ContentError, ParseError), match="content displayed"):
        f1.scrub(open(src, "rb").read())


def test_a_doctype_is_refused(tmp_path):
    """A DOCTYPE can define entities — both a data-hiding channel and the entry point
    for the classic expansion attacks. We do not model it, so we decline rather than
    edit the part blind."""
    from src.scrub.formats.ooxml import zipwrite
    data = zipwrite.write({
        "[Content_Types].xml": C._CONTENT_TYPES.encode(),
        "_rels/.rels": C._ROOT_RELS.encode(),
        "word/document.xml":
            b'<?xml version="1.0"?>\n<!DOCTYPE w:document [<!ENTITY x "leak">]>\n'
            + C._document_xml(C.SOURCE_TEXT).split("?>\n", 1)[1].encode()})
    with pytest.raises((ContentError, ParseError), match="DOCTYPE"):
        f1.scrub(data)


def test_an_xml_comment_is_removed_and_cdata_is_not(tmp_path):
    """A comment is never rendered, so removing it cannot change what a reader sees —
    which makes it a deletion, and therefore F1's job. It is also reachable by none of
    the census's name-based rules, since it has neither a part name nor an element
    name, and no producer in the corpus writes one. It was found by looking, not by
    the corpus.

    CDATA is the trap: `<![CDATA[ ... <!-- ... ]]>` is *text*, and a scanner cutting
    at the first `<!--` would corrupt the document.
    """
    from src.scrub.formats.ooxml import opc as _opc
    from src.scrub.formats.ooxml import zipwrite
    doc = C._document_xml(C.SOURCE_TEXT).replace(
        "<w:body>", "<w:body><!-- SECRET-AUTHOR-NOTE -->"
                    "<![CDATA[keep <!-- this ]]>")
    data = zipwrite.write({"[Content_Types].xml": C._CONTENT_TYPES.encode(),
                           "_rels/.rels": C._ROOT_RELS.encode(),
                           "word/document.xml": doc.encode()})
    assert any("XML comment" in r for r in f1.residuals(data)), "control"

    body = _opc.parse(f1.scrub(data)).archive.by_name(
        "word/document.xml").content()
    assert b"SECRET-AUTHOR-NOTE" not in body
    assert b"keep <!-- this" in body, "a CDATA section is text, not a comment"


def test_unhandled_media_is_refused_rather_than_passed_through(tmp_path):
    """EMF/WMF can carry text and even a printer name, and this project has no
    handler for it — so it is a refusal, not a silent pass-through."""
    src = str(tmp_path / "emf.docx")
    C.synthetic(src)
    with zipfile.ZipFile(src, "a", zipfile.ZIP_DEFLATED) as z:
        zi = zipfile.ZipInfo("word/media/image1.emf", (1980, 1, 1, 0, 0, 0))
        zi.compress_type = zipfile.ZIP_DEFLATED
        z.writestr(zi, b"\x01\x00\x00\x00EMF")
    with pytest.raises(ParseError, match="no handler"):
        f1.scrub(open(src, "rb").read())


def test_the_cli_writes_nothing_when_it_refuses(tmp_path):
    src = C.torture(str(tmp_path / "t.docx"))
    out = tmp_path / "out.docx"
    with pytest.raises((ContentError, ParseError)):
        cli.scrub_file(src, str(out), "F1")
    assert not out.exists()


# --------------------------------------------------------------------------- #
# Recursion into embedded media
# --------------------------------------------------------------------------- #
def test_an_embedded_jpeg_is_scrubbed_at_its_own_formats_f1(tmp_path):
    """The invariant PDF F1 established: an embedded file is scrubbed at *its own*
    format's F1, not passed through. The scan comes back byte-identical while the
    EXIF and the embedded thumbnail are gone."""
    from src.scrub.formats.jpeg import f1 as jf1

    from . import corpus as imgc
    img = imgc.build_torture_jpeg()
    src = str(tmp_path / "media.docx")
    C.synthetic(src)
    with zipfile.ZipFile(src, "a", zipfile.ZIP_DEFLATED) as z:
        zi = zipfile.ZipInfo("word/media/image1.jpeg", (1980, 1, 1, 0, 0, 0))
        zi.compress_type = zipfile.ZIP_DEFLATED
        z.writestr(zi, img)

    out = f1.scrub(open(src, "rb").read())
    scrubbed = opc.parse(out).archive.by_name("word/media/image1.jpeg").content()
    assert scrubbed == jf1.scrub(img)
    assert len(scrubbed) < len(img)
    assert b"Exif" not in scrubbed


def test_docprops_thumbnail_goes_entirely(tmp_path):
    """A rendered picture of the document's own first page, with its own EXIF."""
    from . import corpus as imgc
    src = str(tmp_path / "thumb.docx")
    C.synthetic(src)
    with zipfile.ZipFile(src, "a", zipfile.ZIP_DEFLATED) as z:
        zi = zipfile.ZipInfo("docProps/thumbnail.jpeg", (1980, 1, 1, 0, 0, 0))
        zi.compress_type = zipfile.ZIP_DEFLATED
        z.writestr(zi, imgc.build_torture_jpeg())
    out = f1.scrub(open(src, "rb").read())
    assert "docProps/thumbnail.jpeg" not in opc.parse(out).parts()


# --------------------------------------------------------------------------- #
# Residuals, named rather than silently ignored
# --------------------------------------------------------------------------- #
def test_verify_reports_no_failure_on_any_producer(real):
    """`residuals()` feeds `verify()`, and the CLI refuses on a non-empty list — so it
    must contain only genuine failures, never things the tier deliberately preserves.

    That distinction was got wrong first and only a real file caught it: with the
    documented residuals wired into `verify()`, every LibreOffice and Word document
    was refused for carrying a font table, which F1 never promised to remove. The
    synthetic corpus has no font table, so the synthetic tests all passed. W7 had
    already recorded the same rule for the PDF redaction detector.
    """
    for name, path in real.items():
        assert f1.residuals(f1.scrub(open(path, "rb").read())) == [], name


def test_residuals_would_catch_a_locus_that_survived(tmp_path):
    """The self-check has to be able to fail, or `verify()` is decoration."""
    from src.scrub.formats.ooxml import zipwrite
    doc = C._document_xml(C.SOURCE_TEXT).replace(
        "<w:body>", '<w:body><w:p w:rsidR="00A11CE0"/>')
    leaky = zipwrite.write({
        "[Content_Types].xml": C._CONTENT_TYPES.encode(),
        "_rels/.rels": C._ROOT_RELS.encode(),
        "word/document.xml": doc.encode()})
    assert any("w:rsidR" in r for r in f1.residuals(leaky))


def test_advisories_name_what_f1_knowingly_leaves(real):
    """Not failures: properties of the input the tier preserves on purpose. They are
    limits #20 and #21, surfaced rather than hidden.

    The check is that each advisory *explains itself*, not that it has a particular
    punctuation mark. An earlier version asserted a colon, as a proxy for "names the
    part it is about" — which broke correctly when the namespace advisory was
    aggregated across parts and stopped naming any single one.
    """
    for name, path in real.items():
        out = f1.scrub(open(path, "rb").read())
        for a in f1.advisories(out):
            assert len(a) > 30, f"{name}: advisory too terse to act on: {a}"
            assert any(mark in a for mark in (":", "—")), \
                f"{name}: advisory states no reason: {a}"


@pytest.mark.skipif(not C.HAVE_WORD, reason="Word samples absent")
def test_word_version_namespaces_are_reported(real):
    """A real Word document declares nine versioned namespaces (`w10` through
    `w16se`). The set dates the producing application, and F1 cannot remove a
    declaration without rewriting the part — so it is named here and belongs to F2."""
    out = f1.scrub(open(real["msword"], "rb").read())
    ns_lines = [a for a in f1.advisories(out) if "versioned namespaces" in a]
    assert ns_lines
    assert any("w16" in line for line in ns_lines)


def test_a_user_bookmark_survives_and_is_reported_but_goback_does_not(tmp_path):
    """`_GoBack` is where the cursor sat at the last save. Other bookmark names are
    link targets that hyperlinks and TOC fields reference, so removing them would
    break the document — they survive, and are reported rather than hidden."""
    src = str(tmp_path / "bm.docx")
    doc = C._document_xml(C.SOURCE_TEXT).replace(
        "<w:body>",
        '<w:body><w:bookmarkStart w:id="0" w:name="_GoBack"/>'
        '<w:bookmarkStart w:id="1" w:name="Chapter1"/>')
    parts = {"[Content_Types].xml": C._CONTENT_TYPES.encode(),
             "_rels/.rels": C._ROOT_RELS.encode(),
             "word/document.xml": doc.encode()}
    from src.scrub.formats.ooxml import zipwrite
    open(src, "wb").write(zipwrite.write(parts))

    out = f1.scrub(open(src, "rb").read())
    # Read the PART, not the archive bytes: a string is absent from deflated bytes
    # almost by construction, so a raw `not in` check passes vacuously and proves
    # nothing about whether the element was removed.
    doc = opc.parse(out).archive.by_name("word/document.xml").content()
    assert b"_GoBack" not in doc
    assert b"Chapter1" in doc
    assert any("Chapter1" in a for a in f1.advisories(out))
