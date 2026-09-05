"""M13 — DOCX F2: canonical re-serialisation, and the residual it does not close.

F2's premise is that a producer's XML *spelling* is not content, so rewriting every
part through one writer should make two spellings of the same document identical.
These tests check the premise at its smallest, then check the three traps that
silently break a document, then check the cost the tier actually charges.

Content preservation is verified twice: against the document's own text, and — the
gate that does not rely on our own idea of what a document is — **in pixel space**,
by rendering before and after through LibreOffice and comparing the images.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess

import pytest

from src.scrub.errors import ContentError, ParseError
from src.scrub.formats.docx import f2, textextract
from src.scrub.formats.ooxml import opc, xmlcanon, zipwrite

from . import docx_corpus as C

HAVE_SOFFICE = shutil.which("soffice") is not None
HAVE_POPPLER = shutil.which("pdftoppm") is not None

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
W14_NS = "http://schemas.microsoft.com/office/word/2010/wordml"


@pytest.fixture(scope="module")
def real(tmp_path_factory):
    return C.producers(str(tmp_path_factory.mktemp("docxf2")))


def _pkg(parts: dict[str, bytes]) -> bytes:
    base = {"[Content_Types].xml": C._CONTENT_TYPES.encode(),
            "_rels/.rels": C._ROOT_RELS.encode()}
    base.update(parts)
    return zipwrite.write(base)


# --------------------------------------------------------------------------- #
# The premise
# --------------------------------------------------------------------------- #
def test_two_spellings_of_the_same_document_collapse_to_one():
    """The whole tier, at its smallest. Different prefixes, different quote style,
    spaced versus tight self-closing, an extra unused declaration — one document."""
    a = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
         f'<w:document xmlns:w="{W_NS}" xmlns:mc="{MC_NS}" xmlns:w14="{W14_NS}" '
         f'xmlns:unused="http://example.invalid/none" mc:Ignorable="w14 unused">'
         f'<w:body><w:p><w:r><w:t xml:space="preserve">a &gt; b </w:t></w:r>'
         f'</w:p></w:body></w:document>').encode()
    b = (f"<?xml version='1.0' encoding='utf-8'?>\n"
         f'<ww:document xmlns:ww="{W_NS}" xmlns:m2="{MC_NS}" xmlns:x14="{W14_NS}" '
         f'm2:Ignorable="x14" ><ww:body ><ww:p ><ww:r >'
         f'<ww:t xml:space="preserve" >a > b </ww:t></ww:r></ww:p></ww:body>'
         f'</ww:document>').encode()
    assert xmlcanon.canonicalize(a) == xmlcanon.canonicalize(b)


def test_canonicalisation_is_idempotent(real):
    """A canonical form that still changes on a second pass is not canonical — and
    the first version of the canonicaliser was not: a document declaring `mc` solely
    to carry `mc:Ignorable` had both pruned on pass one, leaving `mc` declared and
    unused, and pass two dropped it."""
    for name, path in real.items():
        pkg = opc.parse(open(path, "rb").read())
        for entry in pkg.archive.entries:
            if not entry.name.endswith((".xml", ".rels")):
                continue
            once = xmlcanon.canonicalize(entry.content())
            assert xmlcanon.canonicalize(once) == once, f"{name}/{entry.name}"


# --------------------------------------------------------------------------- #
# The traps
# --------------------------------------------------------------------------- #
def test_mc_ignorable_is_rewritten_through_the_uri_not_the_prefix():
    """`mc:Ignorable` names *prefixes*. Renaming a prefix without rewriting it leaves
    Word ignoring a prefix that no longer exists."""
    src = (f'<?xml version="1.0"?>\n<zz:document xmlns:zz="{W_NS}" '
           f'xmlns:q="{MC_NS}" xmlns:v14="{W14_NS}" q:Ignorable="v14">'
           f'<zz:body><zz:p v14:paraId="AA"/></zz:body></zz:document>').encode()
    out = xmlcanon.canonicalize(src)
    assert b'mc:Ignorable="w14"' in out
    assert b'xmlns:w14=' in out


def test_an_unused_namespace_declaration_is_dropped_and_delisted():
    """Which closes limit #21: the versioned-namespace set that dates the producing
    Word survives F1 and does not survive F2. Once F1 has removed the last `w14`
    user, both the declaration and its `mc:Ignorable` entry go."""
    src = (f'<?xml version="1.0"?>\n<w:document xmlns:w="{W_NS}" xmlns:mc="{MC_NS}" '
           f'xmlns:w14="{W14_NS}" mc:Ignorable="w14">'
           f'<w:body><w:p/></w:body></w:document>').encode()
    out = xmlcanon.canonicalize(src)
    assert b"w14" not in out
    assert b"mc:Ignorable" not in out


def test_text_is_never_reindented():
    """Whitespace inside `w:t` is what the reader sees, especially under
    `xml:space="preserve"`. A pretty-printer here would change the document."""
    src = (f'<?xml version="1.0"?>\n<w:document xmlns:w="{W_NS}"><w:body><w:p>'
           f'<w:r><w:t xml:space="preserve">  two  spaces  </w:t></w:r>'
           f'</w:p></w:body></w:document>').encode()
    out = xmlcanon.canonicalize(src)
    assert b"<w:t xml:space=\"preserve\">  two  spaces  </w:t>" in out


def test_cdata_is_not_mistaken_for_a_comment():
    src = (f'<?xml version="1.0"?>\n<w:document xmlns:w="{W_NS}"><w:body><w:p>'
           f'<w:r><w:t><![CDATA[keep <!-- this ]]></w:t></w:r>'
           f'</w:p></w:body></w:document>').encode()
    out = xmlcanon.canonicalize(src)
    assert b"keep &lt;!-- this" in out


# --------------------------------------------------------------------------- #
# Content preservation
# --------------------------------------------------------------------------- #
def test_text_survives_every_producer(real):
    for name, path in real.items():
        data = open(path, "rb").read()
        assert textextract.document_text(f2.scrub(data)) == \
            textextract.document_text(data), name


@pytest.mark.skipif(not (HAVE_SOFFICE and HAVE_POPPLER),
                    reason="needs soffice + pdftoppm for the pixel gate")
def test_the_rendered_page_is_unchanged(real, tmp_path):
    """The gate that does not rely on our own idea of what a document is.

    Checking text equality proves our extractor agrees with itself. Rendering the
    document before and after and comparing **pixels** is what shows a reader sees
    the same page — the same argument PDF F2 makes when it renders all five producers
    at 150 DPI rather than trusting its own invariant.
    """
    def render(path: str, tag: str) -> str:
        out = tmp_path / tag
        out.mkdir()
        subprocess.run(["soffice", "--headless", "--convert-to", "pdf",
                        "--outdir", str(out), path], capture_output=True, timeout=180)
        pdfs = list(out.glob("*.pdf"))
        if not pdfs:
            pytest.skip("LibreOffice produced no PDF here")
        subprocess.run(["pdftoppm", "-r", "100", "-png", str(pdfs[0]),
                        str(out / "page")], capture_output=True, timeout=180)
        pngs = sorted(out.glob("page*.png"))
        assert pngs, "no pages rendered"
        h = hashlib.sha256()
        for png in pngs:
            h.update(png.read_bytes())
        return h.hexdigest()

    checked = 0
    for name, path in real.items():
        if name == "synth_zipfile":
            continue                      # no styles: LibreOffice invents its own
        scrubbed = tmp_path / f"{name}_f2.docx"
        scrubbed.write_bytes(f2.scrub(open(path, "rb").read()))
        assert render(path, f"{name}_before") == render(str(scrubbed), f"{name}_after"), \
            f"{name}: the rendered page changed"
        checked += 1
    assert checked, "the pixel gate ran on nothing"


# --------------------------------------------------------------------------- #
# What F1 refused and F2 accepts — with the cost stated
# --------------------------------------------------------------------------- #
def test_a_tracked_insertion_is_accepted_keeping_its_text():
    """Unwrapped, not deleted: the reader's text stays exactly where it was and only
    the wrapper — which carries the author's name and the date — goes."""
    doc = (f'<?xml version="1.0"?>\n<w:document xmlns:w="{W_NS}"><w:body><w:p>'
           f'<w:ins w:id="7" w:author="A Reviewer" w:date="2019-03-04T15:00:00Z">'
           f'<w:r><w:t>inserted words</w:t></w:r></w:ins></w:p></w:body>'
           f'</w:document>').encode()
    out = f2.scrub(_pkg({"word/document.xml": doc}))
    assert "inserted words" in textextract.document_text(out)
    body = opc.parse(out).archive.by_name("word/document.xml").content()
    assert b"w:ins" not in body and b"A Reviewer" not in body


def test_a_tracked_deletion_is_accepted_by_removing_it():
    doc = (f'<?xml version="1.0"?>\n<w:document xmlns:w="{W_NS}"><w:body><w:p>'
           f'<w:r><w:t>kept</w:t></w:r>'
           f'<w:del w:id="8" w:author="A Reviewer" w:date="2019-03-04T15:01:00Z">'
           f'<w:r><w:delText>deleted but still in the file</w:delText></w:r>'
           f'</w:del></w:p></w:body></w:document>').encode()
    out = f2.scrub(_pkg({"word/document.xml": doc}))
    assert "kept" in textextract.document_text(out)
    assert b"deleted but still in the file" not in \
        opc.parse(out).archive.by_name("word/document.xml").content()
    assert b"A Reviewer" not in out


def test_comments_and_their_anchors_both_go():
    """Dropping the comments part while leaving `w:commentReference` behind would
    point an anchor at nothing."""
    doc = (f'<?xml version="1.0"?>\n<w:document xmlns:w="{W_NS}"><w:body><w:p>'
           f'<w:commentRangeStart w:id="1"/><w:r><w:t>text</w:t></w:r>'
           f'<w:commentRangeEnd w:id="1"/><w:r><w:commentReference w:id="1"/></w:r>'
           f'</w:p></w:body></w:document>').encode()
    comments = (f'<?xml version="1.0"?>\n<w:comments xmlns:w="{W_NS}">'
                f'<w:comment w:id="1" w:author="A Reviewer">'
                f'<w:p><w:r><w:t>check this</w:t></w:r></w:p></w:comment>'
                f'</w:comments>').encode()
    out = f2.scrub(_pkg({"word/document.xml": doc,
                         "word/comments.xml": comments}))
    parts = opc.parse(out).parts()
    assert "word/comments.xml" not in parts
    body = opc.parse(out).archive.by_name("word/document.xml").content()
    assert b"commentReference" not in body and b"commentRangeStart" not in body
    assert "text" in textextract.document_text(out)


def test_f1_still_refuses_what_f2_accepts(tmp_path):
    """The tiers differ deliberately: at F1 accepting revisions would change what a
    reader sees with markup on, so it refuses; F2 licenses the rewrite and states the
    cost."""
    from src.scrub.formats.docx import f1
    doc = (f'<?xml version="1.0"?>\n<w:document xmlns:w="{W_NS}"><w:body><w:p>'
           f'<w:ins w:id="7" w:author="A Reviewer"><w:r><w:t>x</w:t></w:r>'
           f'</w:ins></w:p></w:body></w:document>').encode()
    data = _pkg({"word/document.xml": doc})
    with pytest.raises((ContentError, ParseError), match="content displayed"):
        f1.scrub(data)
    f2.scrub(data)          # must not raise


def test_structural_refusals_still_apply_at_f2(tmp_path):
    """F2 accepts revisions; it does not accept a macro."""
    import zipfile
    src = str(tmp_path / "m.docx")
    C.synthetic(src)
    with zipfile.ZipFile(src, "a", zipfile.ZIP_DEFLATED) as z:
        zi = zipfile.ZipInfo("word/vbaProject.bin", (1980, 1, 1, 0, 0, 0))
        zi.compress_type = zipfile.ZIP_DEFLATED
        z.writestr(zi, b"\xd0\xcf\x11\xe0")
    with pytest.raises((ContentError, ParseError), match="macro"):
        f2.scrub(open(src, "rb").read())


# --------------------------------------------------------------------------- #
# Self-check
# --------------------------------------------------------------------------- #
def test_f2_output_is_clean_by_its_own_residual_check(real):
    for name, path in real.items():
        assert f2.residuals(f2.scrub(open(path, "rb").read())) == [], name


def test_residuals_would_catch_a_part_left_uncanonical():
    """The self-check has to be able to fail."""
    doc = (f"<?xml version='1.0'?>\n<w:document xmlns:w=\"{W_NS}\" >"
           f"<w:body /></w:document>").encode()
    assert any("canonical" in r for r in
               f2.residuals(_pkg({"word/document.xml": doc})))


@pytest.mark.skipif(not HAVE_SOFFICE, reason="soffice absent")
def test_the_output_still_opens(real, tmp_path):
    for name, path in real.items():
        out = tmp_path / f"{name}.docx"
        out.write_bytes(f2.scrub(open(path, "rb").read()))
        sub = tmp_path / f"o_{name}"
        sub.mkdir()
        subprocess.run(["soffice", "--headless", "--convert-to", "pdf",
                        "--outdir", str(sub), str(out)],
                       capture_output=True, timeout=180, check=False)
        assert list(sub.glob("*.pdf")), name
