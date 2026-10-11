"""EPUB at F1 (Phase 6 M6): ZIP plus OCF's rules, the package document, the
navigation file, the chapters, re-keyed fonts, and validity kept."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from src.scrub import cli  # noqa: E402
from src.scrub.dispatch import default_dispatcher  # noqa: E402
from src.scrub.errors import ParseError, UnsupportedFormatError  # noqa: E402
from src.scrub.formats.epub import f1, ocf  # noqa: E402
from src.scrub.formats.ooxml import zipread  # noqa: E402
from src.scrub.formats.zip import f1 as zip_f1  # noqa: E402
from tests.scrub import epub_corpus as ec  # noqa: E402
from tests.scrub import zip_corpus as zc  # noqa: E402

HAVE_EPUBCHECK = shutil.which("epubcheck") is not None


def _opf(data: bytes) -> str:
    return next(b for n, b in ec.members(data).items()
                if n.endswith("content.opf")).decode()


def _identifier(data: bytes) -> str:
    return re.search(r"<dc:identifier[^>]*>([^<]*)<", _opf(data)).group(1)


# --- every locus ------------------------------------------------------------------

@pytest.mark.parametrize("shape", ec.SHAPES)
def test_every_planted_value_is_gone(shape):
    data = ec.build(3, shape=shape)
    planted = ec.planted(3, shape)
    assert all(any(p in b for b in zc.expanded(data)) for p in planted)
    out = f1.scrub(data)
    assert [p for p in planted if any(p in b for b in zc.expanded(out))] == []
    assert f1.residuals(out) == []


@pytest.mark.parametrize("shape", ec.SHAPES)
def test_variants_differing_only_in_metadata_scrub_to_the_same_bytes(shape):
    assert len({f1.scrub(ec.build(v, shape=shape)) for v in (1, 2, 3)}) == 1


def test_scrub_is_idempotent_and_deterministic():
    data = ec.build(1, shape="calibre")
    out = f1.scrub(data)
    assert f1.scrub(out) == out and f1.scrub(data) == out


# --- what stays -------------------------------------------------------------------

def test_the_book_stays():
    data = ec.build(2, shape="pandoc")
    before, after = ec.members(data), ec.members(f1.scrub(data))
    for name in ("OEBPS/style.css", "OEBPS/nav.xhtml"):
        assert after[name] == before[name]
    assert ec.BYLINE.encode() in after["OEBPS/title.xhtml"]
    assert b"Words of chapter 1" in after["OEBPS/ch1.xhtml"]
    opf = after["OEBPS/content.opf"].decode()
    for kept in ("<dc:title", "<dc:language", 'property="title-type"',
                 "schema:accessMode", "ibooks:specified-fonts", "<manifest>", "<spine"):
        assert kept in opf, kept
    import io

    from PIL import Image
    a = Image.open(io.BytesIO(before["OEBPS/cover.jpg"])).convert("RGB").tobytes()
    b = Image.open(io.BytesIO(after["OEBPS/cover.jpg"])).convert("RGB").tobytes()
    assert a == b


def test_ocf_packaging():
    out = f1.scrub(ec.build(1, shape="libreoffice"))     # mimetype last-but-sorted
    a = zipread.read(out)
    first = a.entries[0]
    assert (first.name, first.method, first.extra_loc, first.content()) == (
        "mimetype", zipread.STORED, b"", ocf.MIMETYPE)
    assert out[30:38] == b"mimetype" and out[38:58] == ocf.MIMETYPE


def test_the_identifier_is_replaced_everywhere_and_derived_from_content():
    a, b = f1.scrub(ec.build(1, shape="pandoc")), f1.scrub(ec.build(1, shape="pandoc",
                                                                       seed=4))
    ida, idb = _identifier(a), _identifier(b)
    assert re.fullmatch(r"urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-8[0-9a-f]{3}-"
                        r"[89ab][0-9a-f]{3}-[0-9a-f]{12}", ida)
    assert ida != idb                                       # different books
    assert f'content="{ida}"'.encode() in ec.members(a)["OEBPS/toc.ncx"]


def test_modified_is_the_zip_epoch():
    assert "<meta property=\"dcterms:modified\">1980-01-01T00:00:00Z</meta>" in \
        _opf(f1.scrub(ec.build(1, shape="pandoc")))


def test_the_epub2_cover_pointer_and_one_identifier_stay():
    opf = _opf(f1.scrub(ec.build(1, shape="calibre")))
    assert '<meta name="cover" content="cover"/>' in opf
    assert opf.count("<dc:identifier") == 1 and "opf:scheme" not in opf
    assert "calibre:" not in opf


@pytest.mark.parametrize("shape,algorithm", [("sigil", ec.IDPF),
                                             ("indesign", ec.ADOBE)])
def test_obfuscated_fonts_are_rekeyed_to_the_new_identifier(shape, algorithm):
    out = f1.scrub(ec.build(1, shape=shape))
    font = ec.members(out)["OEBPS/fonts/book.ttf"]
    assert ec.obfuscate(font, _identifier(out), algorithm) == ec.font(0)
    assert font != ec.font(0)                               # still obfuscated


def test_a_font_that_does_not_unscramble_is_refused():
    book = ec.files(1, "sigil")
    book["OEBPS/fonts/book.ttf"] = b"garbage" * 300
    entries = [zc.Entry(b"mimetype", ocf.MIMETYPE, method=0)] + [
        zc.Entry(n.encode(), b) for n, b in sorted(book.items())]
    with pytest.raises(ParseError, match="unscramble"):
        f1.scrub(zc.write(entries))


# --- what goes --------------------------------------------------------------------

def test_unlisted_files_and_container_metadata_go():
    names = set(ec.members(f1.scrub(ec.build(1, shape="calibre"))))
    assert "iTunesMetadata.plist" not in names
    assert "META-INF/calibre_bookmarks.txt" not in names
    assert "META-INF/container.xml" in names


def test_ncx_author_and_generator_go_but_its_required_fields_stay():
    ncx = ec.members(f1.scrub(ec.build(1, shape="calibre")))["OEBPS/toc.ncx"]
    assert b"docAuthor" not in ncx and b"dtb:generator" not in ncx
    assert b'name="dtb:depth"' in ncx and b'name="dtb:uid"' in ncx


def test_chapter_meta_goes_but_viewport_and_charset_stay():
    page = (b'<?xml version="1.0" encoding="UTF-8"?>\n<html xmlns="http://www.w3.org/'
            b'1999/xhtml"><head><meta charset="utf-8"/><meta name="viewport" '
            b'content="width=600"/><meta name="author" content="X"/><link rel="author"'
            b' href="me.html"/><title>T</title></head><body><p>a <meta name="b" '
            b'content="c"/> b</p></body></html>')
    out = f1.clean_xhtml(page)
    assert b'name="author"' not in out and b'rel="author"' not in out
    assert b'name="viewport"' in out and b'charset="utf-8"' in out
    assert b"<p>a  b</p>" in out          # mixed content: no whitespace eaten


# --- refusals ---------------------------------------------------------------------

def _repack(book: dict[str, bytes]) -> bytes:
    return zc.write([zc.Entry(b"mimetype", ocf.MIMETYPE, method=0)] + [
        zc.Entry(n.encode(), b) for n, b in sorted(book.items())])


@pytest.mark.parametrize("name", ["META-INF/rights.xml", "META-INF/signatures.xml"])
def test_drm_and_signatures_are_refused(name):
    book = ec.files(1, "pandoc")
    book[name] = b"<x/>"
    with pytest.raises(ParseError, match="refused"):
        f1.scrub(_repack(book))


def test_encryption_other_than_font_obfuscation_is_refused():
    book = ec.files(1, "sigil")
    book["META-INF/encryption.xml"] = book["META-INF/encryption.xml"].replace(
        ec.IDPF.encode(), b"http://www.w3.org/2001/04/xmlenc#aes128-cbc")
    with pytest.raises(ParseError, match="DRM"):
        f1.scrub(_repack(book))


def test_an_unknown_binary_in_the_book_is_refused_by_name():
    book = ec.files(1, "pandoc")
    book["OEBPS/content.opf"] = book["OEBPS/content.opf"].replace(
        b"</manifest>", b'<item id="x" href="data.bin" media-type="application/'
        b'octet-stream"/></manifest>')
    book["OEBPS/data.bin"] = bytes(range(256))
    with pytest.raises(ParseError, match="OEBPS/data.bin"):
        f1.scrub(_repack(book))
    out = f1.scrub(_repack(book), keep_unknown=True)
    assert ec.members(out)["OEBPS/data.bin"] == bytes(range(256))


def test_two_renditions_are_refused():
    book = ec.files(1, "pandoc")
    book["META-INF/container.xml"] = book["META-INF/container.xml"].replace(
        b"</rootfiles>", b'<rootfile full-path="OEBPS/content.opf" media-type="'
        b'application/oebps-package+xml"/></rootfiles>')
    with pytest.raises(ParseError, match="rendition"):
        f1.scrub(_repack(book))


# --- dispatch, the plain-archive handler, the CLI ---------------------------------

def test_dispatch_sends_books_here_and_other_ocf_packages_nowhere_quietly():
    d = default_dispatcher()
    assert d.resolve(ec.build(1, shape="pandoc")).format_id == "epub"
    odt = zc.write([zc.Entry(b"mimetype", b"application/vnd.oasis.opendocument.text",
                             method=0), zc.Entry(b"content.xml", b"<x/>")])
    assert d.resolve(odt).format_id == "zip"
    with pytest.raises(UnsupportedFormatError, match="opendocument"):
        zip_f1.scrub(odt)


def test_a_book_whose_mimetype_is_out_of_place_is_still_a_book():
    book = ec.files(1, "pandoc")
    data = zc.write([zc.Entry(n.encode(), b) for n, b in sorted(book.items())]
                    + [zc.Entry(b"mimetype", ocf.MIMETYPE, method=0)])
    assert default_dispatcher().resolve(data).format_id == "epub"
    assert zipread.read(f1.scrub(data)).entries[0].name == "mimetype"


def test_the_report_lists_the_package_and_advises_on_printed_names(tmp_path):
    book = ec.files(1, "pandoc")
    book["OEBPS/title.xhtml"] = book["OEBPS/title.xhtml"].replace(
        ec.BYLINE.encode(), ec.secret("CREATOR", 1).encode())
    src = tmp_path / "b.epub"
    src.write_bytes(_repack(book))
    advisories, rep = cli.scrub_file_reported(str(src), str(tmp_path / "o.epub"), "F1")
    removed = {i.locus for i in rep.removed}
    assert {"package: dc:creator", "package: dc:publisher",
            "OEBPS/ch1.xhtml: meta generator"} <= removed
    assert any("OEBPS/cover.jpg: EXIF" in k for k in removed)
    assert any("is printed in OEBPS/title.xhtml" in a for a in advisories)


# --- validity, judged by the W3C's checker ----------------------------------------

def _epubcheck_errors(path: str) -> set[str]:
    r = subprocess.run(["epubcheck", path], capture_output=True, text=True)
    lines = (r.stdout + r.stderr).splitlines()
    return {re.sub(r"^.*?\.epub/|\(\d+,\d+\)", "", ln) for ln in lines
            if ln.startswith(("ERROR", "FATAL"))}


@pytest.mark.skipif(not HAVE_EPUBCHECK, reason="epubcheck not installed")
@pytest.mark.parametrize("shape", ec.SHAPES)
def test_the_clean_adds_no_epubcheck_error(shape, tmp_path):
    src, out = tmp_path / "in.epub", tmp_path / "out.epub"
    src.write_bytes(ec.build(1, shape=shape))
    out.write_bytes(f1.scrub(src.read_bytes()))
    assert _epubcheck_errors(str(out)) <= _epubcheck_errors(str(src))
