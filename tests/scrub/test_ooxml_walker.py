"""M8 — the OPC walker, and the hostile inputs it must refuse rather than crash on.

Two halves. First, that the walker reads what real producers write. Second — and this
is the half that matters — that every structure it cannot reason about is **refused
with a reason**, because `PK\\x03\\x04` is the magic of every ZIP ever made and a
scrubber that guesses at a malformed package is a scrubber that writes a file it
cannot vouch for.

The hostile archives are built here rather than waited for. None of them is
hypothetical: duplicate names, traversal paths, ZIP64 and prepended stubs are all
things that exist in the wild, and the AppleDouble case is the one that crashed a tool
during this project's own Step 2 research.
"""
from __future__ import annotations

import struct
import zipfile

import pytest

from src.scrub.errors import ParseError
from src.scrub.formats.ooxml import opc, zipread

from . import docx_corpus as C


@pytest.fixture(scope="module")
def real(tmp_path_factory):
    return C.producers(str(tmp_path_factory.mktemp("walk")))


def _minimal(path: str, extra_parts=()) -> bytes:
    C.synthetic(path)
    if extra_parts:
        with zipfile.ZipFile(path, "a", zipfile.ZIP_DEFLATED) as z:
            for name, body in extra_parts:
                zi = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                zi.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(zi, body)
    return open(path, "rb").read()


# --------------------------------------------------------------------------- #
# It reads what real producers write
# --------------------------------------------------------------------------- #
def test_every_producer_parses(real):
    for name, path in real.items():
        pkg = opc.parse(open(path, "rb").read())
        assert pkg.flavour == "docx", name
        assert pkg.parts(), name


def test_relationship_targets_resolve_relative_to_their_source_part(real):
    """`word/document.xml` + `settings.xml` is `word/settings.xml`, not
    `settings.xml`. Getting this wrong makes every internal target look orphaned."""
    assert opc.resolve("word/document.xml", "settings.xml") == "word/settings.xml"
    assert opc.resolve("word/document.xml", "media/i.png") == "word/media/i.png"
    assert opc.resolve("", "word/document.xml") == "word/document.xml"
    assert opc.resolve("word/document.xml", "/docProps/core.xml") == "docProps/core.xml"


def test_rels_part_naming():
    assert opc.rels_part_for("word/document.xml") == "word/_rels/document.xml.rels"
    assert opc.rels_part_for("[Content_Types].xml") == "_rels/[Content_Types].xml.rels"


@pytest.mark.skipif(not C.HAVE_WORD, reason="Word samples absent")
def test_orphan_detection_catches_the_unreadable_content_failure(real):
    """Dropping a part without dropping its relationship is how a scrubber destroys a
    document while reporting success. F1 is not allowed to delete anything until this
    check is clean, so the check itself must actually fire."""
    pkg = opc.parse(open(real["msword"], "rb").read())
    kept = {p for p in pkg.parts() if not p.startswith("docProps")}
    orphans = pkg.orphans(kept)
    assert any("docProps/core.xml" in o for o in orphans)
    assert any("docProps/app.xml" in o for o in orphans)
    assert not pkg.orphans(set(pkg.parts()))          # nothing dropped, nothing orphaned


def test_content_types_override_beats_the_extension_default(real):
    pkg = opc.parse(open(next(iter(real.values())), "rb").read())
    assert pkg.content_type("word/document.xml").endswith("document.main+xml")
    assert pkg.content_type("_rels/.rels").endswith("relationships+xml")


def test_refusal_list_names_a_reason(tmp_path):
    data = _minimal(str(tmp_path / "m.docx"),
                    [("word/vbaProject.bin", b"\xd0\xcf\x11\xe0macro"),
                     ("word/embeddings/o.bin", b"\xd0\xcf\x11\xe0ole")])
    refusals = opc.parse(data).refusals()
    assert len(refusals) == 2
    assert all(": " in r for r in refusals), "a refusal without a reason is a failure"


def test_appledouble_entries_are_read_not_refused(tmp_path):
    """They are metadata to drop, not a structure we cannot parse -- and they are the
    Step 2 crash target, so the walker must survive them."""
    data = _minimal(str(tmp_path / "ad.docx"),
                    [("__MACOSX/word/._document.xml", b"\x00\x05\x16\x07")])
    pkg = opc.parse(data)
    assert "__MACOSX/word/._document.xml" in pkg.parts()
    assert not pkg.notes, "an AppleDouble entry is not an undeclared *part*"


# --------------------------------------------------------------------------- #
# It refuses what it cannot account for
# --------------------------------------------------------------------------- #
def test_duplicate_entry_names_are_refused(tmp_path):
    """Two parts, one name: readers disagree about which wins, so 'the document' is
    not well defined -- the PDF hybrid-xref refusal in another format."""
    p = str(tmp_path / "dup.docx")
    C.synthetic(p)
    with zipfile.ZipFile(p, "a", zipfile.ZIP_DEFLATED) as z:
        zi = zipfile.ZipInfo("word/document.xml", (1980, 1, 1, 0, 0, 0))
        zi.compress_type = zipfile.ZIP_DEFLATED
        z.writestr(zi, "<w:document/>")
    with pytest.raises(ParseError, match="duplicate entry name"):
        opc.parse(open(p, "rb").read())


@pytest.mark.parametrize("name,why", [
    ("../../etc/passwd", "relative segment"),
    ("/etc/passwd", "absolute entry name"),
    ("word\\document.xml", "backslash"),
    ("word/./document2.xml", "relative segment"),
])
def test_traversal_and_absolute_names_are_refused(tmp_path, name, why):
    """A scrubber that resolves one of these against the filesystem is a directory
    traversal bug, so they are refused at the reader before anything can act."""
    p = str(tmp_path / "trav.docx")
    C.synthetic(p)
    with zipfile.ZipFile(p, "a", zipfile.ZIP_DEFLATED) as z:
        zi = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
        zi.compress_type = zipfile.ZIP_DEFLATED
        z.writestr(zi, "x")
    with pytest.raises(ParseError, match=why):
        zipread.read(open(p, "rb").read())


def test_trailing_bytes_after_the_eocd_are_refused(tmp_path):
    p = str(tmp_path / "tail.docx")
    C.synthetic(p)
    with open(p, "ab") as f:
        f.write(b"APPENDED SECRET")
    with pytest.raises(ParseError, match="trailing bytes"):
        zipread.read(open(p, "rb").read())


def test_prepended_bytes_are_refused(tmp_path):
    """A self-extracting stub or a concatenation. Either way the leading bytes are
    unaccounted-for content that a scrub would carry through untouched."""
    p = str(tmp_path / "pre.docx")
    C.synthetic(p)
    raw = b"MZ stub bytes" + open(p, "rb").read()
    with pytest.raises(ParseError):
        zipread.read(raw)


def test_encrypted_entry_is_refused(tmp_path):
    p = str(tmp_path / "enc.docx")
    raw = bytearray(_minimal(p))
    cen = raw.find(b"PK\x01\x02")
    flags = struct.unpack_from("<H", raw, cen + 8)[0]
    struct.pack_into("<H", raw, cen + 8, flags | zipread.FLAG_ENCRYPTED)
    with pytest.raises(ParseError, match="encrypted"):
        zipread.read(bytes(raw))


def test_zip64_is_refused_rather_than_truncated(tmp_path):
    """Its 64-bit sizes live in extra fields and a second EOCD; a reader that ignores
    them silently truncates, which for a scrubber means missing a part entirely."""
    p = str(tmp_path / "z64.docx")
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
        zi = zipfile.ZipInfo("word/document.xml", (1980, 1, 1, 0, 0, 0))
        zi.compress_type = zipfile.ZIP_DEFLATED
        with z.open(zi, "w", force_zip64=True) as fh:
            fh.write(b"<w:document/>")
    with pytest.raises(ParseError, match="ZIP64"):
        zipread.read(open(p, "rb").read())


def test_crc_mismatch_is_refused_on_read(tmp_path):
    """A part whose bytes do not match its own checksum cannot be vouched for, so
    `content()` raises instead of returning what it happened to decode."""
    p = str(tmp_path / "crc.docx")
    raw = bytearray(_minimal(p))
    cen = raw.find(b"PK\x01\x02")
    struct.pack_into("<I", raw, cen + 16, 0xDEADBEEF)
    arc = zipread.read(bytes(raw))
    with pytest.raises(ParseError, match="CRC mismatch"):
        arc.entries[0].content()


def test_missing_content_types_is_not_an_opc_package(tmp_path):
    p = str(tmp_path / "nott.zip")
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", "<w:document/>")
    with pytest.raises(ParseError, match="Content_Types"):
        opc.parse(open(p, "rb").read())


def test_malformed_part_xml_fails_closed(tmp_path):
    p = str(tmp_path / "bad.docx")
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types><not closed>")
        z.writestr("word/document.xml", "<w:document/>")
    with pytest.raises(ParseError, match="malformed XML"):
        opc.parse(open(p, "rb").read())


# --------------------------------------------------------------------------- #
# Identification: the prefix decides nothing
# --------------------------------------------------------------------------- #
def test_a_plain_zip_is_not_claimed(tmp_path):
    p = str(tmp_path / "plain.zip")
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("readme.txt", "hello")
    assert not opc.looks_like(open(p, "rb").read(), "docx")


def test_an_xlsx_shaped_package_is_not_claimed_as_docx(tmp_path):
    """The trap this check exists for: XLSX, PPTX, ODT, EPUB and JAR all start with
    the same four bytes as a DOCX."""
    p = str(tmp_path / "book.xlsx")
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("[Content_Types].xml",
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
                   'content-types"/>')
        z.writestr("xl/workbook.xml", "<workbook/>")
    data = open(p, "rb").read()
    assert not opc.looks_like(data, "docx")
    assert opc.looks_like(data, "xlsx")


def test_looks_like_never_raises_on_garbage():
    """Dispatch asks every handler in turn, so a handler that crashes on a malformed
    file takes down dispatch for every other format too."""
    for junk in (b"", b"PK\x03\x04", b"PK\x03\x04" + b"\x00" * 40, b"not a zip"):
        assert opc.looks_like(junk, "docx") is False


# --------------------------------------------------------------------------- #
# The handler's identification half (M8). Registration lands with F1 (M10).
# --------------------------------------------------------------------------- #
def test_handler_claims_every_producer_and_no_plain_zip(real, tmp_path):
    from src.scrub.formats.docx.handler import DocxHandler
    h = DocxHandler()
    for name, path in real.items():
        data = open(path, "rb").read()
        assert h.matches(data[:16]) and h.claims(data), name

    p = str(tmp_path / "plain.zip")
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("readme.txt", "hello")
    data = open(p, "rb").read()
    assert h.matches(data[:16]), "the prefix is the same -- that is the whole problem"
    assert not h.claims(data), "claims() is what has to tell them apart"


def test_docx_is_registered_last_because_its_magic_is_the_weakest():
    """`PK\x03\x04` is shared with every ZIP ever made, so the DOCX handler is asked
    only after every format with a distinctive prefix has declined. Registration
    itself waited for F1 (M10): until a tier existed, the tool did not advertise a
    format it could not scrub."""
    from src.scrub.dispatch import default_dispatcher
    ids = [h.format_id for h in default_dispatcher()._handlers]
    assert ids[-1] == "docx"


def test_preflight_reports_input_side_refusals(tmp_path):
    from src.scrub.formats.docx.handler import DocxHandler
    data = _minimal(str(tmp_path / "m.docx"),
                    [("word/vbaProject.bin", b"\xd0\xcf\x11\xe0macro")])
    assert any("macro" in r for r in DocxHandler().preflight(data))
    assert DocxHandler().preflight(_minimal(str(tmp_path / "clean.docx"))) == []
