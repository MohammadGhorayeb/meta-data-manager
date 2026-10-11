"""EPUB at F1: a ZIP with OCF's rules, a package document, and a book inside.

Phase 6 M6 (`docs/p6_tail_plan.md` §1, §4 D5, §11). An EPUB names its author four
ways: in the package document (OPF: creator, contributor, publisher, rights, dates,
a description, subjects, and whatever the producing program adds -- LibreOffice
its exact build, platform and source commit), in the navigation file (NCX:
`docAuthor`), in each chapter's `<head>` (`<meta name="author">`, a generator), and
in the pictures it holds, each with its own metadata. And it carries a **random
per-export identifier** in two places (the OPF's `dc:identifier`, the NCX's
`dtb:uid`), which ties every copy of a file to the one export that made it.

F1, on top of the ZIP rebuild (`zip/f1.py`):

- **OCF**: `mimetype` first and stored, as the container format requires (wherever
  the input had it); the rest sorted. `META-INF` keeps the container, encryption
  and Apple display-options files; any other (`calibre_bookmarks.txt`, container
  metadata) is dropped. A file the package does not list is not part of the book
  -- no reading system shows it -- and is dropped. Rights and signatures are
  refused (`ocf.REFUSED`), and so is encryption other than font obfuscation.
- **OPF**: in every `metadata` block, Dublin Core elements other than the title, the
  language and the unique identifier go, as do `link`s and every `meta` except
  what a reading system uses to show the book: `dcterms:modified` (required by
  EPUB 3; set to 1980-01-01, the ZIP epoch), rendition and media-overlay
  properties, Apple's specified-fonts switch, accessibility properties, the EPUB 2
  `cover` pointer, and a title's type and display order. The identifier is
  **replaced** by one derived from the cleaned book (`ocf.derive_identifier`), and
  the NCX's copy follows; obfuscated fonts are re-keyed to it.
- **NCX**: `docAuthor` goes; in `head`, only the four `dtb:` fields readers use.
- **Chapters (XHTML)**: `<meta>` naming anything (author, generator, keywords...)
  goes except `viewport` (fixed layout needs it); `link`s to an author or to
  metadata schemas go; pictures inlined as `data:` are cleaned by their handlers
  (shared with SVG); inline SVG `<metadata>` goes.
- Comments and processing instructions go from every one of those (the XML
  declaration and `xml-stylesheet` stay).

Kept, and reported: what the book prints (a title page naming its author is the
book's text; `advise` says where a removed value is still visible), stylesheets
and scripts as written, fonts with their licence text (a font's names are its
designer's credit, not the book author's identity). Everything is deletion or a
value replaced in place; no part is re-serialised.
"""
from __future__ import annotations

import posixpath
import re
import urllib.parse

from ...errors import ParseError, ScrubError
from ..ooxml import xmlspans as xs
from ..ooxml import xmlsurgery, zipread, zipwrite
from ..svg import f1 as svg_f1
from ..zip import f1 as zip_f1
from . import ocf

OPF = "http://www.idpf.org/2007/opf"
DC = "http://purl.org/dc/elements/1.1/"
NCX = "http://www.daisy.org/z3986/2005/ncx/"
XHTML = "http://www.w3.org/1999/xhtml"
SVG = "http://www.w3.org/2000/svg"
MODIFIED = "1980-01-01T00:00:00Z"

KEPT_PROPERTIES = {"dcterms:modified", "media:duration", "media:active-class",
                   "media:playback-active-class", "ibooks:specified-fonts"}
KEPT_PROPERTY_PREFIXES = ("rendition:", "schema:access")
KEPT_REFINEMENTS = {"title-type", "display-seq", "alternate-script"}
KEPT_NCX_META = {"dtb:uid", "dtb:depth", "dtb:totalPageCount", "dtb:maxPageNumber",
                 ""}
NCX_TYPE = "application/x-dtbncx+xml"
XHTML_TYPES = {"application/xhtml+xml", "text/html", "text/x-oeb1-document"}
FONT_TYPES = ("font/", "application/font", "application/x-font",
              "application/vnd.ms-opentype")


# --- edits shared by every XML part --------------------------------------------

def _strip_comments_and_pis(data: bytes) -> bytes:
    data, _ = xmlsurgery.remove_comments(data)
    out, i = bytearray(), 0
    for m in re.finditer(rb"<\?([\w.:-]+)(.*?)\?>", data, re.S):
        if m.group(1) in (b"xml", b"xml-stylesheet"):
            continue
        out += data[i:m.start()]
        i = m.end()
    return bytes(out + data[i:])


def _span(data: bytes, el: xs.Element, tidy: bool = True) -> tuple[int, int]:
    """An element's span, with the whitespace before it when that follows a tag
    (its own line in pretty-printed XML), so a removal leaves no empty line. Not
    in mixed content (`tidy=False`), where that whitespace is a visible space."""
    if not tidy:
        return el.start, el.end
    s = el.start
    while s > 0 and data[s - 1:s] in b" \t\r\n":
        s -= 1
    return (s if data[s - 1:s] == b">" else el.start), el.end


def _set_text(data: bytes, el: xs.Element, value: str) -> tuple[int, int, bytes]:
    s, e = el.inner
    if el.children:
        raise ParseError(f"EPUB: <{el.local}> holds elements where text was expected")
    if s == e:                                            # <x/>: write it out
        return el.start, el.end, (data[el.start:el.open_end - 2].rstrip() + b">"
                                  + xs.escape_text(value) + b"</"
                                  + _written_name(data, el) + b">")
    return s, e, xs.escape_text(value)


def _written_name(data: bytes, el: xs.Element) -> bytes:
    return re.match(rb"<([^\s/>]+)", data[el.start:el.open_end]).group(1)


def _drop_attribute(data: bytes, el: xs.Element, local: str) -> list:
    tag = data[el.start:el.open_end]
    m = re.search(rb"\s+(?:[\w.-]+:)?" + local.encode()
                  + rb"\s*=\s*(\"[^\"]*\"|'[^']*')", tag)
    return [] if m is None else [(el.start + m.start(), el.start + m.end(), b"")]


def _set_attribute(data: bytes, el: xs.Element, local: str, value: str) -> list:
    tag = data[el.start:el.open_end]
    m = re.search(rb"(\s" + local.encode() + rb"\s*=\s*)(\"[^\"]*\"|'[^']*')", tag)
    if m is None:
        return []
    new = b'"' + xs.escape_text(value).replace(b'"', b"&quot;") + b'"'
    return [(el.start + m.start(2), el.start + m.end(2), new)]


def _apply(data: bytes, cuts: list, edits: list) -> bytes:
    """Cut spans, then splice edits that fall outside them."""
    spans = sorted(cuts)
    kept = [e for e in edits
            if not any(s <= e[0] and e[1] <= t for s, t in spans)]
    return xs.splice(data, [(s, t, b"") for s, t in _merge(spans)] + kept)


def _merge(spans):
    out = []
    for s, e in sorted(spans):
        if out and s < out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


# --- the package document --------------------------------------------------------

class Package:
    """What the OPF says, read once: the unique identifier and the manifest."""

    def __init__(self, path: str, data: bytes):
        self.path = path
        self.els = xs.parse(data, f"EPUB {path}")
        pkg = self.els[0] if self.els else None
        if pkg is None or (pkg.uri, pkg.local) != (OPF, "package"):
            raise ParseError(f"EPUB: {path} is not a package document")
        self.uid_ref = pkg.attr("unique-identifier") or ""
        self.identifier = None
        self.manifest: dict[str, tuple[str, str]] = {}      # path -> (id, type)
        base = posixpath.dirname(path)
        for el in self.els:
            if (el.uri, el.local) == (DC, "identifier") and \
                    el.attr("id") == self.uid_ref:
                self.identifier = el.text.strip()
            if (el.uri, el.local) == (OPF, "item"):
                href = el.attr("href") or ""
                if re.match(r"[a-z][a-z0-9+.-]*:", href, re.I):
                    continue                              # remote resource
                target = posixpath.normpath(posixpath.join(
                    base, urllib.parse.unquote(href.split("#")[0])))
                if target.startswith("../") or target == "..":
                    raise ParseError(f"EPUB: manifest item {href!r} points outside "
                                     "the book")
                self.manifest[target] = (el.attr("id") or "",
                                         (el.attr("media-type") or "").lower())

    def ids(self) -> dict[str, xs.Element]:
        return {el.attr("id"): el for el in self.els if el.attr("id")}


def _metadata_children(els: list[xs.Element]):
    for i, el in enumerate(els):
        if el.local != "metadata" or el.uri != OPF:
            continue
        stack = list(el.children)
        while stack:
            k = stack.pop()
            child = els[k]
            # OPF 2.0's legacy wrappers hold the same elements one level down.
            if child.local in ("dc-metadata", "x-metadata"):
                stack.extend(child.children)
                continue
            yield i, child


def _verdict(el, uid_ref, kept_ids) -> str:
    """'keep', 'identifier', 'modified' or 'drop' for one metadata child."""
    if el.uri == DC:
        if el.local == "identifier":
            return "identifier" if el.attr("id") == uid_ref else "drop"
        return "keep" if el.local in ("title", "language") else "drop"
    if el.uri == OPF and el.local == "meta":
        prop = el.attr("property")
        if prop is None:
            return "keep" if el.attr("name") == "cover" else "drop"
        refines = el.attr("refines")
        if refines:
            # A title's type and order are how it displays; a media overlay's
            # duration refines a manifest item. Anything else refines something
            # that is going (a creator's role, an identifier's type).
            if prop == "media:duration":
                return "keep"
            return "keep" if (prop in KEPT_REFINEMENTS
                              and refines.lstrip("#") in kept_ids) else "drop"
        if prop == "dcterms:modified":
            return "modified"
        return "keep" if (prop in KEPT_PROPERTIES
                          or prop.startswith(KEPT_PROPERTY_PREFIXES)) else "drop"
    return "drop"                                         # link, foreign elements


def clean_opf(data: bytes, identifier: str, path: str = "OPF") -> bytes:
    data = _strip_comments_and_pis(data)
    els = xs.parse(data, f"EPUB {path}")
    uid_ref = els[0].attr("unique-identifier") or ""
    children = list(_metadata_children(els))
    kept_ids = {el.attr("id") for _, el in children
                if el.uri == DC and el.local in ("title",) and el.attr("id")}
    cuts, edits = [], []
    for _, el in children:
        v = _verdict(el, uid_ref, kept_ids)
        if v == "drop":
            cuts.append(_span(data, el))
        elif v == "identifier":
            edits.append(_set_text(data, el, identifier))
            edits += _drop_attribute(data, el, "scheme")
        elif v == "modified":
            edits.append(_set_text(data, el, MODIFIED))
    return _apply(data, cuts, edits)


def removed_values(data: bytes) -> list[tuple[str, str]]:
    """(name, value) of what `clean_opf` removes or rewrites (the identifier and
    the modified time): the report's and the advice's view of the package."""
    els = xs.parse(_strip_comments_and_pis(data), "EPUB OPF")
    uid_ref = els[0].attr("unique-identifier") or ""
    children = list(_metadata_children(els))
    kept_ids = {el.attr("id") for _, el in children
                if el.uri == DC and el.local == "title" and el.attr("id")}
    out = []
    for _, el in children:
        v = _verdict(el, uid_ref, kept_ids)
        if v == "keep":
            continue
        if el.uri == DC:
            name = f"dc:{el.local}"
        else:
            name = (el.attr("property") or el.attr("name") or el.local)
            if el.attr("refines"):
                name += f" of {el.attr('refines')}"
        value = el.text.strip() or el.attr("content") or el.attr("href") or ""
        out.append((name, value))
    return out


# --- navigation and chapters -----------------------------------------------------

def clean_ncx(data: bytes, identifier: str, path: str = "NCX") -> bytes:
    data = _strip_comments_and_pis(data)
    els = xs.parse(data, f"EPUB {path}")
    cuts, edits = [], []
    for el in els:
        if el.uri != NCX:
            continue
        if el.local == "docAuthor":
            cuts.append(_span(data, el))
        elif el.local == "meta" and el.parent is not None and \
                els[el.parent].local == "head":
            name = el.attr("name") or ""
            if name not in KEPT_NCX_META:
                cuts.append(_span(data, el))
            elif name == "dtb:uid":
                edits += _set_attribute(data, el, "content", identifier)
    return _apply(data, cuts, edits)


_AUTHOR_RELS = {"author", "me", "meta", "schema.dc", "schema.dcterms"}


def clean_xhtml(data: bytes, path: str = "XHTML") -> bytes:
    data = _strip_comments_and_pis(data)
    data = svg_f1._clean_data_uris(data, [], 0, label=f"EPUB {path}")
    els = xs.parse(data, f"EPUB {path}")
    cuts = []
    for el in els:
        in_head = el.parent is not None and els[el.parent].local == "head"
        if el.uri == XHTML and el.local == "meta":
            named = el.attr("name") or el.attr("property")
            if named is not None and named.lower() != "viewport":
                cuts.append(_span(data, el, in_head))
        elif el.uri == XHTML and el.local == "link":
            rels = set((el.attr("rel") or "").lower().split())
            if rels & _AUTHOR_RELS:
                cuts.append(_span(data, el, in_head))
        elif el.uri == SVG and el.local == "metadata":
            cuts.append(_span(data, el))
    return _apply(data, cuts, [])


# --- the book ----------------------------------------------------------------------

def is_epub(archive: zipread.ZipArchive) -> bool:
    m = archive.by_name("mimetype")
    try:
        return m is not None and m.content().strip() == ocf.MIMETYPE
    except ParseError:
        return False


class Book:
    """The archive read once: names, bodies, and what each member is to the book."""

    def __init__(self, data: bytes, check_memory: bool = True):
        self.archive = zipread.read(data)
        if not is_epub(self.archive):
            raise ParseError("not an EPUB (no 'application/epub+zip' mimetype)")
        if check_memory:
            zip_f1.check_memory_for(self.archive, "")
        self.entries: dict[str, tuple[bytes, bool, zipread.ZipEntry]] = {}
        for e in self.archive.entries:
            name, utf8, text = zip_f1.member_name(e)
            if text in self.entries:
                raise ParseError(f"EPUB: two members named {text!r}")
            self.entries[text] = (name, utf8, e)
        for name, why in ocf.REFUSED.items():
            if name in self.entries:
                raise ParseError(f"EPUB: {why} ({name}); refused")
        if ocf.CONTAINER not in self.entries:
            raise ParseError(f"EPUB: no {ocf.CONTAINER}")
        self.opf_path = ocf.rootfile(self.body(ocf.CONTAINER))
        if self.opf_path not in self.entries:
            raise ParseError(f"EPUB: the package document {self.opf_path} is missing")
        self.package = Package(self.opf_path, self.body(self.opf_path))
        self.fonts: dict[str, str] = {}
        if ocf.ENCRYPTION in self.entries:
            self.fonts = {urllib.parse.unquote(k): v for k, v in
                          ocf.obfuscated(self.body(ocf.ENCRYPTION)).items()}
            if self.fonts and not self.package.identifier:
                raise ParseError("EPUB: obfuscated fonts, but no unique identifier "
                                 "to unscramble them with")

    def body(self, name: str) -> bytes:
        return self.entries[name][2].content()

    def role(self, name: str) -> str:
        """What a member is: mimetype, opf, ncx, xhtml, font, resource,
        meta-inf (kept), container-metadata or unlisted (dropped), dir."""
        e = self.entries[name][2]
        if e.is_dir:
            return "dir"
        if name == "mimetype":
            return "mimetype"
        if name == self.opf_path:
            return "opf"
        if name.startswith("META-INF/"):
            return "meta-inf" if name in ocf.KEPT_META_INF else "container-metadata"
        if name in self.fonts:
            return "font"
        listed = self.package.manifest.get(name)
        if listed is None:
            return "unlisted"
        media_type = listed[1]
        if media_type == NCX_TYPE:
            return "ncx"
        if media_type in XHTML_TYPES:
            return "xhtml"
        if media_type.startswith(FONT_TYPES):
            return "font"
        return "resource"


def scrub(data: bytes, *, keep_unknown: bool = False, check_memory: bool = True,
          dispatcher=None) -> bytes:
    from ...dispatch import default_dispatcher
    book = Book(data, check_memory)
    d = dispatcher or default_dispatcher()
    old_id = book.package.identifier or ""

    plain: dict[str, bytes] = {}             # everything but the OPF and NCX
    deferred: dict[str, str] = {}            # opf/ncx name -> role
    unknown: list[str] = []
    for name in book.entries:
        role = book.role(name)
        if role in ("dir", "mimetype", "container-metadata", "unlisted"):
            continue
        body = book.body(name)
        if zip_f1.sidecar(name, body):
            continue
        if role in ("opf", "ncx"):
            deferred[name] = role
        elif role == "meta-inf":
            plain[name] = _strip_comments_and_pis(body)
        elif role == "xhtml":
            plain[name] = clean_xhtml(body, name)
        elif role == "font":
            if name in book.fonts:
                body = ocf.xor(body, ocf.key(old_id, book.fonts[name]),
                               book.fonts[name])
                if not ocf.is_font(body):
                    raise ParseError(f"EPUB: {name} does not unscramble with the "
                                     "book's identifier; refused")
            plain[name] = body                   # unscrambled until the end
        elif body:
            plain[name] = zip_f1.scrub_member(body, name, d, keep_unknown,
                                              check_memory, 0, unknown)
        else:
            plain[name] = body
    if unknown:
        raise ParseError(
            f"{len(unknown)} file(s) of a type this project has no handler for, "
            f"refused rather than passed through unscrubbed: {', '.join(unknown[:10])}"
            ". --keep-unknown-members keeps such files byte for byte, unscrubbed")

    def finish(identifier: str) -> dict[str, bytes]:
        out = dict(plain)
        for name, role in deferred.items():
            clean = clean_opf if role == "opf" else clean_ncx
            out[name] = clean(book.body(name), identifier, name)
        return out

    identifier = ocf.derive_identifier(finish(ocf.PLACEHOLDER))
    members = finish(identifier)
    for name, algorithm in book.fonts.items():
        if name in members:
            members[name] = ocf.xor(members[name], ocf.key(identifier, algorithm),
                                    algorithm)
    ordered = [zipwrite.Member(b"mimetype", ocf.MIMETYPE, store=True)]
    for name in sorted(members, key=lambda n: book.entries[n][0]):
        raw, utf8, _ = book.entries[name]
        ordered.append(zipwrite.Member(raw, members[name], utf8=utf8))
    return zipwrite.write_archive(ordered)


# --- reading it back ---------------------------------------------------------------

def residuals(data: bytes) -> list[str]:
    """What a cleaned book must not hold: anything the ZIP check finds (container
    fields, sidecars, members' own metadata), a `mimetype` out of place, a file
    the book does not list, and any XML part the clean would still change -- a
    cleaned book is a fixed point of the clean."""
    out = zip_f1.residuals(data)
    try:
        book = Book(data, check_memory=False)
    except ScrubError as exc:
        return out + [f"unreadable book ({exc})"]
    first = book.archive.entries[0]
    if first.name != "mimetype" or first.method != zipread.STORED:
        out.append("mimetype is not the first member, stored")
    identifier = book.package.identifier or ""
    for name in book.entries:
        role = book.role(name)
        if role in ("container-metadata", "unlisted"):
            out.append(f"{name}: not part of the book")
            continue
        try:
            body = book.body(name)
            if role == "opf":
                clean = clean_opf(body, identifier, name)
            elif role == "ncx":
                clean = clean_ncx(body, identifier, name)
            elif role == "xhtml":
                clean = clean_xhtml(body, name)
            elif role == "meta-inf":
                clean = _strip_comments_and_pis(body)
            else:
                continue
            if clean != body:
                out.append(f"{name}: metadata the clean removes")
        except ScrubError as exc:
            out.append(f"{name}: unreadable ({exc})")
    return out


def describe(data: bytes) -> dict[str, str]:
    out = zip_f1.describe(data)
    book = Book(data, check_memory=False)
    counts: dict[str, int] = {}
    for name, value in removed_values(book.body(book.opf_path)):
        counts[name] = counts.get(name, 0) + 1
        key = name if counts[name] == 1 else f"{name} ({counts[name]})"
        out[f"package: {key}"] = value
    for name in book.entries:
        role = book.role(name)
        if role in ("container-metadata", "unlisted"):
            out[f"{name}"] = "a file the book does not list"
        elif role == "xhtml":
            for el in xs.parse(_strip_comments_and_pis(book.body(name)), name):
                named = el.attr("name") if el.local == "meta" else None
                if el.uri == XHTML and named and named.lower() != "viewport":
                    out[f"{name}: meta {named}"] = el.attr("content") or ""
        elif role == "ncx":
            els = xs.parse(book.body(name), name)
            for el in els:
                if el.uri == NCX and el.local == "docAuthor":
                    words = " ".join(els[k].text.strip() for k in el.children)
                    out[f"{name}: docAuthor"] = words.strip() or "(author)"
    return out


def kept(data: bytes) -> list[str]:
    book = Book(data, check_memory=False)
    fonts = [n for n in book.entries if book.role(n) == "font"]
    styles = [n for n in book.entries if book.role(n) == "resource"
              and book.package.manifest[n][1] in ("text/css", "text/javascript",
                                                   "application/javascript")]
    out = []
    if fonts:
        out.append("fonts kept with their licence text: " + ", ".join(fonts[:6]))
    if styles:
        out.append("stylesheets and scripts kept as written: " + ", ".join(styles[:6]))
    out.append("the book's identifier is derived from its cleaned content, so it "
               "links copies of the same text and nothing else")
    return out


def advise(data: bytes) -> list[str]:
    """Where a value the clean removes from the package is still printed in the
    book (a title page, a copyright line): the book's text, kept."""
    book = Book(data, check_memory=False)
    values = {v for n, v in removed_values(book.body(book.opf_path))
              if n in ("dc:creator", "dc:contributor", "dc:publisher", "dc:rights")
              and len(v) >= 3}
    out = []
    pages = [n for n in book.entries if book.role(n) == "xhtml"]
    for value in sorted(values):
        for name in pages:
            if value.encode() in book.body(name):
                out.append(f"{value!r} is printed in {name}: that is the book's text, "
                           "and stays -- edit the book to remove it")
                break
    for e in embedded_notes(book):
        out.append(e)
    return out


def embedded_notes(book: Book) -> list[str]:
    """Advice the pictures' own handlers give (PDF-style warnings), by path."""
    from ...dispatch import default_dispatcher
    d = default_dispatcher()
    out = []
    for name in book.entries:
        if book.role(name) != "resource":
            continue
        body = book.body(name)
        try:
            handler = d.resolve(body)
        except ScrubError:
            continue
        fn = getattr(handler, "advise", None)
        try:
            out += [f"{name}: {a}" for a in (fn(body) if fn else [])]
        except Exception:                                 # noqa: BLE001
            pass
    return out
