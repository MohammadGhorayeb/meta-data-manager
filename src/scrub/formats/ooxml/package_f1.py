"""F1 for any OOXML package -- the rules Word, Excel and PowerPoint share.

DOCX F1 was written first and carried these inline. Excel and PowerPoint are the
same container (`docs/office_plan.md` §3, "shared by all three"), so the package
layer lives here and each format's F1 adds only what its own program writes. A rule
copied into three formats is a rule that will be fixed in two (CLAUDE.md), so
nothing in this module knows which program wrote the package.

What it owns: deciding nothing about content. It prunes what a format's F1 already
decided to drop (content types, relationships, relationships nobody uses any more,
empty `.rels`), cleans pictures by their own handler, blanks picture names that are
file paths, and checks its own output for dangling relationships.
"""
from __future__ import annotations

import posixpath
import re
from collections.abc import Iterable

from ...errors import ContentError, ParseError, ResourceError
from . import opc, xmlspans, zipread, zipwrite
from . import xmlsurgery as xs
from .opc import Package

# Parts every flavour drops: the package's properties (author, editor, company,
# manager, dates, a sensitivity label's tenant), its thumbnail (a rendering that can
# be stale), custom XML, and the AppleDouble debris a Mac leaves in a re-zipped file.
DROP_PARTS = (
    re.compile(r"^docProps/core\.xml$"),
    re.compile(r"^docProps/app\.xml$"),
    re.compile(r"^docProps/custom\.xml$"),
    re.compile(r"^docProps/meta\.xml$"),              # the Cocoa-only part
    re.compile(r"^docProps/thumbnail\.[A-Za-z0-9]+$"),
    re.compile(r"^customXml/"),
    re.compile(r"^__MACOSX/"),
    re.compile(r"(^|/)\._"),
)


def is_dropped(name: str, patterns: Iterable[re.Pattern]) -> bool:
    return any(p.search(name) for p in patterns)


def scrub_media(name: str, body: bytes, no_handler_hint: dict[str, str]) -> bytes:
    """An embedded picture at its own format's F1. The invariant PDF F1 established:
    an embedded file is scrubbed by its own handler, never passed through."""
    from ... import embedded
    try:
        return embedded.scrub_bytes(body, name)
    except ParseError as exc:
        kind = no_handler_hint.get(posixpath.splitext(name)[1].lower())
        if kind and "no handler" in str(exc):
            raise ParseError(f"{name}: {kind} pictures have no handler in this "
                             "project; refused rather than passed through "
                             "unscrubbed") from None
        raise


# Pictures' names and descriptions (docs/word_plan.md §5, measured on 784 real Word
# documents): Word writes the inserted file's own name into `pic:cNvPr name` and
# `wp:docPr descr` -- 151 times, 53 of them WITH the folder it came from, some naming
# a user (`C:\Users\<name>\...`). A value that is a file name or a path is blanked;
# "Picture 3" and a real description stay. Excel (`xdr:cNvPr`) and PowerPoint
# (`p:cNvPr`) write the same attribute (docs/office_plan.md §2).
_NAME_ATTRS = ("name", "descr", "title", "o:title", "alt")
FILE_LIKE = re.compile(
    rb"\.(?:jpe?g|jpe|png|gif|bmp|tiff?|emf|wmf|heic|webp|svg|wdp)\b"
    rb"|(?:^|[\s(\"'])(?:[A-Za-z]:\\|\\\\[\w$.-]+\\|file:/)"
    rb"|/(?:Users|home)/", re.I)
# Any non-visual properties element, whatever its prefix (`pic:`, `wps:`, `wpg:`,
# and `a:` -- missed by a first list, found holding a full path under a user's
# folder in a real document), and VML's picture and shape elements.
_DRAWING_TAG = rb"(?:[\w]+:)?cNvPr|wp:docPr|v:imagedata|v:shape"


DRAWINGML_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_FIELD_ID = re.compile(rb'(?<![\w:.-])id\s*=\s*(["\'])([^"\']*)\1')
_FIELD_PLACEHOLDER = b"{00000000-0000-8000-8000-000000000000}"


def _field_guid(name: str, blanked: bytes, index: int) -> bytes:
    """A version-8 UUID (RFC 9562) from SHA-256 over the part's name, its bytes with
    every field ID blanked, and the field's position -- the EPUB identifier's rule
    (`epub/ocf.derive_identifier`) applied to one field."""
    import hashlib
    b = bytearray(hashlib.sha256(name.encode() + b"\0" + blanked + b"\0"
                                 + str(index).encode()).digest()[:16])
    b[6] = (b[6] & 0x0F) | 0x80
    b[8] = (b[8] & 0x3F) | 0x80
    x = b.hex().upper()
    return f"{{{x[:8]}-{x[8:12]}-{x[12:16]}-{x[16:20]}-{x[20:]}}}".encode()


def canonical_field_ids(name: str, body: bytes) -> bytes:
    """Replace every DrawingML text field's `id` with one derived from the cleaned
    part itself.

    `<a:fld id="{GUID}" type="slidenum">` -- a slide number, a date, a chart data
    label's value -- carries a GUID the schema requires. LibreOffice Impress draws a
    fresh one for every field in every layout **on every save** (measured, E-PPTX:
    two saves of one deck differ only there), and PowerPoint gives each field a
    user inserts its own: a per-file identifier that links a cleaned copy to the
    original and to other copies, the class of `a16:creationId`, which F1 already
    removes. It cannot be removed, so it is replaced: the same content gets the same
    ID, there is no per-save entropy and no tool constant, and running the rule on
    its own output changes nothing (the residual check relies on that).
    """
    if b"fld" not in body or DRAWINGML_NS.encode() not in body:
        return body
    els = xmlspans.parse(body, name)
    spans = []
    for el in els:
        if el.uri == DRAWINGML_NS and el.local == "fld":
            m = _FIELD_ID.search(body, el.start, el.open_end)
            if m:
                spans.append((m.start(2), m.end(2)))
    if not spans:
        return body
    blanked = xmlspans.splice(body, [(s, e, _FIELD_PLACEHOLDER) for s, e in spans])
    return xmlspans.splice(body, [(s, e, _field_guid(name, blanked, i))
                                  for i, (s, e) in enumerate(spans)])


def blank_file_names(body: bytes) -> bytes:
    """Blank picture names and descriptions that are file names or paths; keep the
    rest (a description a person wrote is alt text). VML `v:imagedata o:title` is
    blanked whatever it holds: Word writes the inserted file's name there without
    its extension (30 of 69 in real Word documents were non-empty), and nothing
    draws or reads it -- a VML picture's alt text is `v:shape alt`."""
    tag_rx = re.compile(rb"<(" + _DRAWING_TAG + rb")(\s[^>]*?)?(/?)>")
    attr_rx = re.compile(rb'(\s(?:' + b"|".join(re.escape(a.encode()) for a in _NAME_ATTRS)
                         + rb')\s*=\s*")([^"]*)(")')

    def tag(m):
        attrs = m.group(2) or b""
        always = m.group(1) == b"v:imagedata"

        def value(a):
            blank = (always and a.group(1).strip().startswith(b"o:title")
                     or FILE_LIKE.search(a.group(2)))
            return a.group(1) + (b"" if blank else a.group(2)) + a.group(3)
        return b"<" + m.group(1) + attr_rx.sub(value, attrs) + m.group(3) + b">"
    return tag_rx.sub(tag, body)


def drop_linked_paths(body: bytes) -> bytes:
    """A picture both embedded and linked shows the embedded copy; the link is a
    path on the author's machine. `r:link` goes from a DrawingML `a:blip` that has
    an `r:embed`, `o:href` from a VML `v:imagedata` that has an `r:id`. A picture
    that is ONLY linked keeps its link (it would show nothing without it)."""
    def blip(m):
        t = m.group(0)
        return re.sub(rb'\s+r:link="[^"]*"', b"", t) if b"r:embed=" in t else t

    def imagedata(m):
        t = m.group(0)
        return re.sub(rb'\s+o:href="[^"]*"', b"", t) if b"r:id=" in t else t
    body = re.sub(rb"<a:blip\s[^>]*>", blip, body)
    return re.sub(rb"<v:imagedata\s[^>]*>", imagedata, body)


def rels_source(rels_name: str) -> str:
    d = posixpath.dirname(posixpath.dirname(rels_name))
    base = posixpath.basename(rels_name)[:-len(".rels")]
    return posixpath.join(d, base) if base else ""


def prune_unreferenced(kept: dict[str, bytes], every: frozenset[str] = frozenset()) -> None:
    """Relationships nothing refers to any more, after the parts are cleaned:
    every EXTERNAL one (a path or a URL serving no element), and every one from a
    `.rels` part named in `every`. Measured on Word: F1 removed `w:attachedTemplate`
    and `w:mailMerge` while their relationships -- holding the template's path and
    the data source's -- stayed in `settings.xml.rels` (25 templates, 3 mail-merge
    sources and 1 recipient list in 784 real Word documents). Internal relationships
    elsewhere are left alone: Office finds styles, settings and fonts by
    relationship TYPE, not by a reference in the text."""
    for name in [n for n in kept if n.endswith(".rels")]:
        source = rels_source(name)
        if source not in kept:
            continue
        used = set(re.findall(rb'="([^"]*)"', kept[source]))
        all_unused = name in every

        def unused(tag: bytes, all_unused: bool = all_unused, used: set = used) -> bool:
            rid = re.search(rb'\sId="([^"]*)"', tag)
            external = re.search(rb'TargetMode="External"', tag) is not None
            return (all_unused or external) and rid is not None and rid.group(1) not in used
        written = re.search(rb"<([\w.-]*:)?Relationship[\s/>]", kept[name])
        if written:
            tag = (written.group(1) or b"").decode() + "Relationship"
            kept[name], _ = xs.remove_elements_where(kept[name], tag, unused)


def prune_content_types(body: bytes, dropped: set[str]) -> bytes:
    def is_dropped_override(tag: bytes) -> bool:
        pn = xs.attribute_value(tag, "PartName")
        return pn is not None and pn.decode("utf-8", "replace").lstrip("/") in dropped
    out, _ = xs.remove_elements_where(body, "Override", is_dropped_override)
    return out


def prune_rels(source_part: str, body: bytes, dropped: set[str]) -> bytes:
    def is_dropped_rel(tag: bytes) -> bool:
        target = xs.attribute_value(tag, "Target")
        if target is None:
            return False
        if (xs.attribute_value(tag, "TargetMode") or b"Internal") == b"External":
            # An external target is a URL or an absolute path -- metadata in its own
            # right, but removing it would break a hyperlink the reader can see.
            # Kept, and reported by the plugin as a residual.
            return False
        return opc.resolve(source_part, target.decode("utf-8", "replace")) in dropped
    out, _ = xs.remove_elements_where(body, "Relationship", is_dropped_rel)
    return out


def dropped_rel_ids(source_part: str, rels: bytes, dropped: set[str]) -> set[bytes]:
    """The ids of the relationships `prune_rels` is about to remove from `rels` --
    so a part that names one (`<pageSetup r:id="rId4"/>` naming the printer
    settings) can lose the reference too, rather than keep a dangling `r:id`."""
    out = set()
    for m in re.finditer(rb"<(?:[\w.-]*:)?Relationship\s[^>]*>", rels):
        tag = m.group(0)
        target = xs.attribute_value(tag, "Target")
        rid = xs.attribute_value(tag, "Id")
        if target is None or rid is None:
            continue
        if (xs.attribute_value(tag, "TargetMode") or b"Internal") == b"External":
            continue
        if opc.resolve(source_part, target.decode("utf-8", "replace")) in dropped:
            out.add(rid)
    return out


def dropped_rids_by_source(pkg: Package, dropped: set[str]) -> dict[str, set[bytes]]:
    """For every part, the ids of its relationships to parts F1 drops."""
    out: dict[str, set[bytes]] = {}
    for entry in pkg.archive.entries:
        if entry.is_dir or not entry.name.endswith(".rels"):
            continue
        source = rels_source(entry.name)
        ids = dropped_rel_ids(source, entry.content(), dropped)
        if ids:
            out[source] = ids
    return out


def drop_rid_refs(body: bytes, ids: set[bytes], owners: tuple[str, ...] = ()) -> bytes:
    """References to relationships F1 removed, so no part names one that is gone.

    An element in `owners` exists only to hold that reference (PowerPoint's
    `<p:tags r:id=…/>` naming a dropped add-in part) and goes whole; anywhere else
    the `r:id` attribute is optional and only it goes (Excel's
    `<pageSetup r:id=…/>` naming the dropped printer settings)."""
    if not ids:
        return body
    for tag in owners:
        body, _ = xs.remove_elements_where(
            body, tag, lambda t: xs.attribute_value(t, "r:id") in ids)
    return re.sub(rb'\s+r:id="(' + b"|".join(re.escape(i) for i in sorted(ids)) + rb')"',
                  b"", body)


def preflight(pkg: Package) -> list[str]:
    """What makes ANY package impossible to scrub fully, as reasons: the refused
    parts (`opc.REFUSE_PARTS`), a DOCTYPE, and relationships the input already
    breaks. A format's own F1 adds its own reasons to these."""
    problems = list(pkg.refusals())
    for entry in pkg.archive.entries:
        if entry.is_dir or not entry.name.endswith(".xml"):
            continue
        try:
            if xs.has_doctype(entry.content()):
                problems.append(
                    f"{entry.name}: a DOCTYPE declaration can define entities, which "
                    f"is both a data-hiding channel and an expansion attack; we do "
                    f"not model it and refuse rather than edit blind")
        except ParseError:
            continue

    # A relationship pointing at a part that is not in the package means the INPUT
    # is already inconsistent. Caught here so the post-scrub check can honestly say
    # "the scrub left dangling relationships" -- without this, F1 reports its own
    # output as broken for damage it inherited.
    inherited = pkg.orphans(set(pkg.parts()))
    if inherited:
        problems.append(
            "the input already has relationships pointing at parts it does not "
            f"contain: {inherited}")
    return problems


# Peak memory per byte of XML part, MEASURED on Word F1 (November hostile catalogue):
# a document.xml of 32, 128 and 256 MiB peaked at 912 MB, 3.5 GB and 5.9 GB -- 22 to
# 27 times its size, because every element becomes an object. With a margin, as the
# video factors have (limit #38). Other members cost what a plain archive's do.
XML_MEMORY_FACTOR = 28
_XML_PART = (".xml", ".rels", ".vml")


def check_memory(data: bytes, available: int | None = None) -> None:
    """Refuse up front a package whose DECLARED part sizes need more memory than the
    machine has free -- before any part is inflated. The declared sizes are what
    inflation is held to (`zipread`: a part that inflates past its size is refused),
    so a 1 MB file declaring a 1 GiB `document.xml` is priced at what it would
    really cost: it ran past 120 s and 6.5 GB before this check, and is now refused
    in a moment, saying how much it needs. `--skip-memory-check` turns it off."""
    from ... import resources
    from ..zip.f1 import MEMORY_FACTOR
    arc = zipread.read(data)
    xml = sum(e.uncomp_size for e in arc.entries if e.name.lower().endswith(_XML_PART))
    other = sum(e.uncomp_size for e in arc.entries) - xml
    need = 2 * len(data) + XML_MEMORY_FACTOR * xml + MEMORY_FACTOR * other
    free = resources.available_memory() if available is None else available
    if free is not None and need > free:
        raise ResourceError(
            f"this document's parts inflate to {resources._human(xml + other)} "
            f"({resources._human(xml)} of XML) and cleaning it needs about "
            f"{resources._human(need)}; this machine has {resources._human(free)} "
            "available. Nothing was written. Pass --skip-memory-check to try anyway.")


def malformed_parts(pkg: Package, is_dropped) -> list[str]:
    """Kept XML parts that are not well-formed. Most F1 rules edit the bytes in
    place, so a part no parser can read would pass through them -- and a part we
    cannot read is a part we cannot vouch for. Found fuzzing PowerPoint F1: 389 of
    3,000 damaged decks came out "clean" with a broken slide. Word adopted it
    after measuring the 5 of 946 real Word files that hold such a part
    (docs/office_plan.md §6)."""
    out = []
    for entry in pkg.archive.entries:
        if (entry.is_dir or not entry.name.endswith((".xml", ".rels"))
                or is_dropped(entry.name)):
            continue
        try:
            xmlspans.parse(entry.content(), entry.name)
        except ParseError as exc:
            out.append(str(exc))
    return out


def refuse_if(problems: list[str]) -> None:
    if problems:
        raise ContentError("; ".join(sorted(set(problems))))


def write_checked(kept: dict[str, bytes], dropped: set[str],
                  every: frozenset[str] = frozenset()) -> bytes:
    """Prune what nothing uses, write the package, and check the result.

    Deletion is checked, not trusted: our own output is re-read and no relationship
    may point at a part we removed. That is the "unreadable content" failure, and it
    is the reason the walker builds a graph at all."""
    prune_unreferenced(kept, every)
    # A .rels part that now declares nothing is itself removable, and leaving an empty
    # one behind is a tell that something was taken out of it.
    for name in [n for n in kept if n.endswith(".rels")]:
        if not re.search(rb"<Relationship[\s/>]", kept[name]):
            del kept[name]
            dropped.add(name)

    out = zipwrite.write(kept)
    result = opc.parse(out)
    orphans = result.orphans(set(result.parts()))
    if orphans:
        raise ParseError(f"scrub left dangling relationships: {orphans}")
    return out
