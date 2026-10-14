"""DOCX F1 — surgical deletion, recursive, fail-closed.

F1 here means what it means for PDF: **we only delete.** Parts go whole; attributes
and elements are excised from the parts that survive; every byte we do not delete
stays exactly where it was, and the document's text comes out byte-identical. The ZIP
container is rewritten by `ooxml/zipwrite.py` because container layout is not content.

Three things make this different from stripping tags out of an image:

1. **Consistency is the hard part, not deletion.** Dropping a part means dropping its
   `[Content_Types].xml` override *and* its relationship, or Word shows "unreadable
   content" — the scrub has destroyed the document while reporting success. So the
   last thing `scrub()` does before writing is ask the walker for orphans, and refuse
   if there are any. Deletion is checked, not trusted.

2. **The interesting leaks are attributes, not parts.** `docProps/core.xml` is the
   locus everyone names; `w14:paraId` is the one that links a paragraph pasted into
   another document back to this one, and MAT2 leaves it (§2.6, §2.8).

3. **Some things are content and are therefore refused, not removed.** Tracked
   changes and comments are displayed with markup on, so deleting them would change
   what a reader sees — a content-preservation failure wearing a scrub's clothes.
   They are refused at F1 and belong to F2, where the tier already licenses a rewrite.
"""
from __future__ import annotations

import posixpath
import re

from ...errors import ContentError, ParseError
from ..ooxml import opc, zipwrite
from ..ooxml import xmlsurgery as xs
from ..ooxml.opc import Package

# --------------------------------------------------------------------------- #
# What goes, from the M7 census (docs/p3_documents_plan.md §2.8)
# --------------------------------------------------------------------------- #
DROP_PARTS = (
    re.compile(r"^docProps/core\.xml$"),
    re.compile(r"^docProps/app\.xml$"),
    re.compile(r"^docProps/custom\.xml$"),
    re.compile(r"^docProps/meta\.xml$"),              # the Cocoa-only part
    re.compile(r"^docProps/thumbnail\.[A-Za-z0-9]+$"),
    re.compile(r"^word/people\.xml$"),
    re.compile(r"^customXml/"),
    # Word's other hidden parts (docs/word_plan.md §2): task-pane add-ins -- which
    # add-ins, from which store, with their saved settings -- and printer settings, a
    # binary DEVMODE naming the printer. Neither draws anything.
    re.compile(r"^word/webextensions/"),
    re.compile(r"^word/printerSettings/"),
    re.compile(r"^__MACOSX/"),
    re.compile(r"(^|/)\._"),
)

# Attributes, matched on their WRITTEN name because F1 must not rewrite prefixes.
DROP_ATTRS = r"w:rsid[A-Za-z]*|w1[0-9]:paraId|w1[0-9]:textId"

# Elements removed with their children.
DROP_ELEMENTS = (
    "w:rsids",                  # the settings.xml session pool
    # `w:rsid` is not only an attribute and not only inside `w:rsids`: Word writes a
    # standalone `<w:rsid w:val="…"/>` inside EVERY `w:style` in styles.xml, marking
    # the session that created the style, with values drawn from the same pool. The
    # attribute rule and the container rule both missed it, and only a per-part
    # assertion found it -- a raw search over the archive bytes cannot, because
    # deflate hides the string.
    "w:rsid",
    "w:rsidRoot",
    # A persistent per-document GUID -- in BOTH namespaces. Word writes `w14:docId`
    # and `w15:docId` in the same settings.xml, and handling only the newer one left
    # the older one in a real document. Microsoft versions its namespaces (w14 = Word
    # 2010, w15 = 2012, w16 = 2021), so an element ruled in one almost certainly
    # exists in the others; `test_no_identifier_family_has_an_unruled_sibling`
    # scans the corpus for exactly that, so the next one is caught rather than
    # remembered.
    "w14:docId",
    "w15:docId",
    "w:attachedTemplate",       # frequently a UNC path naming a user or a share
    "w:proofState",             # the machine's proofing configuration
    "w:proofErr",               # which words the speller flagged, per word
    "w:lastRenderedPageBreak",  # pagination from whichever renderer last opened it
    "w:documentProtection",     # a password hash and salt: a credential, not a setting
    "w:mailMerge",              # an absolute path to a file on the author's machine
    # docs/word_plan.md §2 (W1). The "read-only recommended" password: another hash.
    "w:writeProtection",
    # Template and document-management variables (client, matter, user): read only by
    # a DOCVARIABLE field, whose result is already in the text until it is updated.
    "w:docVars",
    # Editing permissions: range markers naming who may edit (`w:ed`, an e-mail
    # address -- measured surviving F1). They draw nothing; the text between stays.
    "w:permStart",
    "w:permEnd",
)

# Content that is not currently displayed, and therefore is not ours to delete.
REFUSE_ELEMENTS = {
    "w:ins": "tracked insertion",
    "w:del": "tracked deletion",
    "w:moveFrom": "tracked move",
    "w:moveTo": "tracked move",
    "w:commentReference": "comment anchor",
    # Tracked FORMATTING changes and table-cell revisions carry the same reviewer's
    # name and date, and were measured passing through F1 untouched (docs/word_plan.md
    # §2): only insertions and deletions had been looked for.
    "w:rPrChange": "tracked formatting change",
    "w:pPrChange": "tracked formatting change",
    "w:sectPrChange": "tracked formatting change",
    "w:tblPrChange": "tracked formatting change",
    "w:tblPrExChange": "tracked formatting change",
    "w:trPrChange": "tracked formatting change",
    "w:tcPrChange": "tracked formatting change",
    "w:tblGridChange": "tracked formatting change",
    "w:numberingChange": "tracked numbering change",
    "w:cellIns": "tracked table-cell insertion",
    "w:cellDel": "tracked table-cell deletion",
    "w:cellMerge": "tracked table-cell merge",
}
REFUSE_PARTS = (
    re.compile(r"^word/comments.*\.xml$"),
    re.compile(r"^word/threadedComments/"),
)

# Pictures are cleaned by their own format's handler, found by CONTENT through the
# shared `embedded.py` (docs/word_plan.md W2): JPEG, PNG, GIF, TIFF, WebP, SVG and
# anything else a handler claims. Until W2 this went by extension, recursed only
# into JPEG and PNG, refused GIF/TIFF/SVG outright -- and passed any extension it did
# not list straight through. EMF/WMF have no handler: refused, named, as before.
_NO_HANDLER_HINT = {".emf": "EMF", ".wmf": "WMF", ".wdp": "HD Photo (JPEG XR)",
                    ".bmp": "BMP"}


def _is_dropped(name: str) -> bool:
    return any(p.search(name) for p in DROP_PARTS)


def _scrub_media(name: str, body: bytes) -> bytes:
    """An embedded picture at its own format's F1. The invariant PDF F1 established:
    an embedded file is scrubbed by its own handler, never passed through."""
    from ... import embedded
    try:
        return embedded.scrub_bytes(body, name)
    except ParseError as exc:
        kind = _NO_HANDLER_HINT.get(posixpath.splitext(name)[1].lower())
        if kind and "no handler" in str(exc):
            raise ParseError(f"{name}: {kind} pictures have no handler in this "
                             "project (EMF/WMF can carry an application and file name "
                             "in their header); refused rather than passed through "
                             "unscrubbed") from None
        raise


# Pictures' names and descriptions (docs/word_plan.md §5, measured on 784 real Word
# documents): Word writes the inserted file's own name into `pic:cNvPr name` and
# `wp:docPr descr` -- 151 times, 53 of them WITH the folder it came from, some naming
# a user (`C:\Users\<name>\...`). A value that is a file name or a path is blanked;
# "Picture 3" and a real description stay.
_NAME_ATTRS = ("name", "descr", "title", "o:title", "alt")
_FILE_LIKE = re.compile(
    rb"\.(?:jpe?g|jpe|png|gif|bmp|tiff?|emf|wmf|heic|webp|svg|wdp)\b"
    rb"|(?:^|[\s(\"'])(?:[A-Za-z]:\\|\\\\[\w$.-]+\\|file:/)"
    rb"|/(?:Users|home)/", re.I)
# Any non-visual properties element, whatever its prefix (`pic:`, `wps:`, `wpg:`,
# and `a:` -- missed by a first list, found holding a full path under a user's
# folder in a real document), and VML's picture and shape elements.
_DRAWING_TAG = rb"(?:[\w]+:)?cNvPr|wp:docPr|v:imagedata|v:shape"


def _blank_file_names(body: bytes) -> bytes:
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
                     or _FILE_LIKE.search(a.group(2)))
            return a.group(1) + (b"" if blank else a.group(2)) + a.group(3)
        return b"<" + m.group(1) + attr_rx.sub(value, attrs) + m.group(3) + b">"
    return tag_rx.sub(tag, body)


def _drop_linked_paths(body: bytes) -> bytes:
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


def _prune_unreferenced(kept: dict[str, bytes]) -> None:
    """Relationships nothing refers to any more, after the parts are cleaned:
    every EXTERNAL one (a path or a URL serving no element), and every one from
    `settings.xml`. Measured: F1 removed `w:attachedTemplate` and `w:mailMerge`
    while their relationships -- holding the template's path and the data source's
    -- stayed in `settings.xml.rels` (25 templates, 3 mail-merge sources and 1
    recipient list in 784 real Word documents). Internal relationships elsewhere
    are left alone: Word finds styles, settings and fonts by relationship TYPE, not
    by a reference in the text."""
    for name in [n for n in kept if n.endswith(".rels")]:
        source = _rels_source(name)
        if source not in kept:
            continue
        used = set(re.findall(rb'="([^"]*)"', kept[source]))
        every = name == "word/_rels/settings.xml.rels"

        def unused(tag: bytes, every: bool = every, used: set = used) -> bool:
            rid = re.search(rb'\sId="([^"]*)"', tag)
            external = re.search(rb'TargetMode="External"', tag) is not None
            return (every or external) and rid is not None and rid.group(1) not in used
        written = re.search(rb"<([\w.-]*:)?Relationship[\s/>]", kept[name])
        if written:
            tag = (written.group(1) or b"").decode() + "Relationship"
            kept[name], _ = xs.remove_elements_where(kept[name], tag, unused)


def _scrub_xml(name: str, body: bytes) -> bytes:
    # Comments first: they are never rendered, so removing them cannot change what a
    # reader sees, and they are reached by none of the name-based rules below.
    body, _ = xs.remove_comments(body)
    body, _ = xs.remove_processing_instructions(body)
    body, _ = xs.remove_attributes(body, DROP_ATTRS)
    for tag in DROP_ELEMENTS:
        body, _ = xs.remove_elements(body, tag)
    # `_GoBack` is where the cursor sat at the last save. Other bookmark names are
    # link targets that hyperlinks and TOC fields reference by name, so removing them
    # would break the document -- they survive, and are recorded as a residual.
    goback = {xs.attribute_value(m.group(0), "w:id")
              for m in re.finditer(rb"<w:bookmarkStart\s[^>]*>", body)
              if xs.attribute_value(m.group(0), "w:name") == b"_GoBack"}
    body, _ = xs.remove_elements_where(
        body, "w:bookmarkStart",
        lambda tag: xs.attribute_value(tag, "w:name") == b"_GoBack")
    # Its end marker too: removing only the start left a dangling `w:bookmarkEnd`
    # (found rendering 60 real Word documents, docs/word_plan.md §5).
    body, _ = xs.remove_elements_where(
        body, "w:bookmarkEnd", lambda tag: xs.attribute_value(tag, "w:id") in goback)
    body = _blank_file_names(body)
    return _drop_linked_paths(body)


def _prune_content_types(body: bytes, dropped: set[str]) -> bytes:
    def is_dropped_override(tag: bytes) -> bool:
        pn = xs.attribute_value(tag, "PartName")
        return pn is not None and pn.decode("utf-8", "replace").lstrip("/") in dropped
    out, _ = xs.remove_elements_where(body, "Override", is_dropped_override)
    return out


def _prune_rels(source_part: str, body: bytes, dropped: set[str]) -> bytes:
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


def _preflight(pkg: Package) -> list[str]:
    """Everything that makes a full scrub impossible, as reasons."""
    problems = list(pkg.refusals())
    for name in pkg.parts():
        if any(p.search(name) for p in REFUSE_PARTS):
            problems.append(
                f"{name}: comments are content displayed with markup on, not "
                f"metadata; refused at F1 rather than silently deleted")
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

    for entry in pkg.archive.entries:
        if entry.is_dir or not entry.name.endswith(".xml"):
            continue
        try:
            body = entry.content()
        except ParseError as exc:
            problems.append(str(exc))
            continue
        for tag, what in REFUSE_ELEMENTS.items():
            if re.search(rb"<" + re.escape(tag.encode()) + rb"[\s/>]", body):
                problems.append(
                    f"{entry.name}: {what} ({tag}) is content displayed with markup "
                    f"on; refused at F1")
                break
    return problems


def scrub(data: bytes) -> bytes:
    pkg = opc.parse(data)
    if pkg.flavour != "docx":
        raise ParseError(f"not a DOCX package (looks like {pkg.flavour})")

    problems = _preflight(pkg)
    if problems:
        raise ContentError("; ".join(sorted(set(problems))))

    dropped = {n for n in pkg.parts() if _is_dropped(n)}
    kept: dict[str, bytes] = {}
    for entry in pkg.archive.entries:
        if entry.is_dir or entry.name in dropped:
            continue
        body = entry.content()
        if entry.name == opc.CONTENT_TYPES:
            body = _prune_content_types(body, dropped)
        elif entry.name.endswith(".rels"):
            source = _rels_source(entry.name)
            body = _prune_rels(source, body, dropped)
        elif entry.name.startswith("word/media/"):
            body = _scrub_media(entry.name, body)
        elif entry.name.endswith(".xml"):
            body = _scrub_xml(entry.name, body)
        kept[entry.name] = body

    _prune_unreferenced(kept)
    # A .rels part that now declares nothing is itself removable, and leaving an empty
    # one behind is a tell that something was taken out of it.
    for name in [n for n in kept if n.endswith(".rels")]:
        if not re.search(rb"<Relationship[\s/>]", kept[name]):
            del kept[name]
            dropped.add(name)

    out = zipwrite.write(kept)

    # Deletion is checked, not trusted: re-read our own output and confirm no
    # relationship points at a part we removed. This is the "unreadable content"
    # failure, and it is the reason the walker builds a graph at all.
    result = opc.parse(out)
    orphans = result.orphans(set(result.parts()))
    if orphans:
        raise ParseError(f"scrub left dangling relationships: {orphans}")
    return out


def _rels_source(rels_name: str) -> str:
    d = posixpath.dirname(posixpath.dirname(rels_name))
    base = posixpath.basename(rels_name)[:-len(".rels")]
    return posixpath.join(d, base) if base else ""


def residuals(data: bytes) -> list[str]:
    """Metadata that should have been removed and was not — a scrub **failure**.

    This feeds `verify()`, and `cli.scrub_file` refuses on a non-empty list, so it
    must contain only genuine failures. Things we knowingly leave — bookmark names,
    external link targets, the font table — are not failures; they are documented
    limits, and they go to `advisories()` instead.

    That distinction was got wrong first and caught by the first real file: with the
    documented residuals wired into `verify()`, every LibreOffice and Word document
    was refused for carrying a font table, which the tier never promised to remove.
    W7 had already recorded the same rule for the PDF redaction detector — a property
    of the input is not a scrub failure — and this is that rule in another format.
    """
    out: list[str] = []
    try:
        pkg = opc.parse(data)
    except ParseError as exc:
        return [f"unparseable: {exc}"]

    dropped_attr = re.compile(DROP_ATTRS)
    for entry in pkg.archive.entries:
        if entry.is_dir or entry.name in ("[Content_Types].xml",):
            continue
        if _is_dropped(entry.name):
            out.append(f"{entry.name}: a part that F1 drops is still present")
            continue
        if entry.name == "word/_rels/settings.xml.rels":
            rels = entry.content()
            for kind in (b"attachedTemplate", b"mailMergeSource",
                         b"mailMergeHeaderSource", b"recipientData"):
                if kind in rels:
                    out.append(f"{entry.name}: a {kind.decode()} relationship survived")
        if not entry.name.endswith(".xml"):
            continue
        try:
            body = entry.content()
        except ParseError as exc:
            out.append(str(exc))
            continue
        for attr in {m.decode() for m in
                     re.findall(rb'\s([A-Za-z_][\w.-]*:[\w.-]+)\s*=\s*"', body)}:
            if dropped_attr.fullmatch(attr):
                out.append(f"{entry.name}: {attr} survived")
        for tag in DROP_ELEMENTS:
            if re.search(rb"<" + re.escape(tag.encode()) + rb"[\s/>]", body):
                out.append(f"{entry.name}: <{tag}> survived")
        if _blank_file_names(body) != body:
            out.append(f"{entry.name}: a picture's name or description is a file name")
        if _drop_linked_paths(body) != body:
            out.append(f"{entry.name}: an embedded picture kept its link path")
        _, n = xs.remove_comments(body)
        if n:
            out.append(f"{entry.name}: {n} XML comment(s) survived")
        _, n = xs.remove_processing_instructions(body)
        if n:
            out.append(f"{entry.name}: {n} processing instruction(s) survived")
    return out


def advisories(data: bytes) -> list[str]:
    """What F1 knowingly leaves behind, reported rather than hidden.

    Not wired into `verify()`: each of these is a property of the input that the tier
    deliberately preserves, and refusing the scrub over one would be refusing to
    clean any real document. They are the rows this format contributes to
    `docs/limits.md` (#20, #21), surfaced through a reporting path the way the PDF
    redaction advisory is.
    """
    out: list[str] = []
    try:
        pkg = opc.parse(data)
    except ParseError as exc:
        return [f"unparseable: {exc}"]

    for r in pkg.external_targets():
        out.append(f"external relationship target survives: {r.target}")
    if pkg.archive.by_name("word/fontTable.xml") is not None:
        out.append("word/fontTable.xml: the font list is a weak machine-profile hint")

    # Microsoft's versioned namespaces are declared even when barely used, so the
    # declaration set dates the producing Word. Removing a declaration means
    # rewriting the part (and `mc:Ignorable` references it), which is an F2
    # canonicalisation -- so at F1 it is named, not removed.
    #
    # Reported ONCE for the package rather than once per part: the same set appears
    # in every part, and five near-identical lines buried every other advisory in
    # the scrub report.
    versioned: set[str] = set()
    parts_with_ns: set[str] = set()
    for entry in pkg.archive.entries:
        if not entry.name.endswith(".xml"):
            continue
        try:
            head = entry.content()[:4000]
        except ParseError:
            continue
        found = {m.decode() for m in
                 re.findall(rb"xmlns:(w1[0-9][a-z]*)=", head)}
        if found:
            versioned |= found
            parts_with_ns.add(entry.name)

    if versioned:
        out.append(
            f"{len(versioned)} versioned namespaces declared across "
            f"{len(parts_with_ns)} part(s) ({', '.join(sorted(versioned))}) — the "
            f"set dates the producing application; F2 removes them")

    for entry in pkg.archive.entries:
        if not entry.name.endswith(".xml"):
            continue
        try:
            body = entry.content()
        except ParseError:
            continue
        for n in re.findall(rb'<w:bookmarkStart[^>]*w:name="([^"]*)"', body):
            out.append(f"{entry.name}: bookmark name survives "
                       f"({n.decode('utf-8', 'replace')}) — it is a link target")
    return out
