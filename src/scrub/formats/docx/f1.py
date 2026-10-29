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

import re

from ...errors import ParseError
from ..ooxml import opc
from ..ooxml import package_f1 as pf
from ..ooxml import xmlsurgery as xs
from ..ooxml.opc import Package

# --------------------------------------------------------------------------- #
# What goes, from the M7 census (docs/p3_documents_plan.md §2.8)
# --------------------------------------------------------------------------- #
DROP_PARTS = pf.DROP_PARTS + (
    re.compile(r"^word/people\.xml$"),
    # Word's other hidden parts (docs/word_plan.md §2): task-pane add-ins -- which
    # add-ins, from which store, with their saved settings -- and printer settings, a
    # binary DEVMODE naming the printer. Neither draws anything.
    re.compile(r"^word/webextensions/"),
    re.compile(r"^word/printerSettings/"),
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
# not list straight through. EMF/WMF have their own handler since W2b
# (`formats/metafile/`), BMP since `docs/bmp_plan.md`; HD Photo has none: refused,
# named.
_NO_HANDLER_HINT = {".wdp": "HD Photo (JPEG XR)"}


def _is_dropped(name: str) -> bool:
    return pf.is_dropped(name, DROP_PARTS)


def _scrub_media(name: str, body: bytes) -> bytes:
    return pf.scrub_media(name, body, _NO_HANDLER_HINT)


# Shared with Excel and PowerPoint (`ooxml/package_f1.py`); the old names stay
# because F2 and the tests reach them through this module.
_blank_file_names = pf.blank_file_names
_drop_linked_paths = pf.drop_linked_paths
_prune_content_types = pf.prune_content_types
_prune_rels = pf.prune_rels
_rels_source = pf.rels_source
# Every unreferenced relationship from `settings.xml` goes, not only external ones:
# `w:attachedTemplate` and `w:mailMerge` are removed above, their paths stayed in
# its `.rels` (docs/word_plan.md §5).
_EVERY_UNREFERENCED = frozenset({"word/_rels/settings.xml.rels"})


def _prune_unreferenced(kept: dict[str, bytes]) -> None:
    pf.prune_unreferenced(kept, _EVERY_UNREFERENCED)


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


def _preflight(pkg: Package) -> list[str]:
    """Everything that makes a full scrub impossible, as reasons."""
    problems = pf.preflight(pkg)
    for name in pkg.parts():
        if any(p.search(name) for p in REFUSE_PARTS):
            problems.append(
                f"{name}: comments are content displayed with markup on, not "
                f"metadata; refused at F1 rather than silently deleted")

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

    pf.refuse_if(_preflight(pkg))

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

    return pf.write_checked(kept, dropped, _EVERY_UNREFERENCED)


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
