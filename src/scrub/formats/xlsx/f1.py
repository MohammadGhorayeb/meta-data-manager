"""XLSX F1 — Excel's own loci, on the shared OOXML package layer.

The package rules (properties, thumbnail, custom XML, pictures, relationships, the
orphan check) are `ooxml/package_f1.py`, shared with Word and PowerPoint. This module
adds only what Excel writes (`docs/office_plan.md` §3), and keeps F1's promise: we
only delete. Every cell's value, type and formula is byte-identical afterwards --
nothing here edits `<sheetData>`.

What the probes measured (`docs/office_plan.md` §2), and MAT2 0.14.0 leaves:
`x15ac:absPath` -- the folder the workbook was saved in, a user name in
`C:\\Users\\…` -- the per-document, per-sheet and per-view GUIDs of the `xr*`
revision namespaces, the password hash, a picture's file path, and text in the
shared-string table that no cell uses any more.

Refused rather than cleaned, because each is data the workbook USES or SHOWS that
F1 may not delete: comments (shown as notes), the shared-workbook change log,
another workbook's path and a cached copy of its cells, a pivot table's copy of its
source data, data connections and Power Query, the Power Pivot data model.
"""
from __future__ import annotations

import re

from ...errors import ParseError
from ..ooxml import opc, xmlspans
from ..ooxml import package_f1 as pf
from ..ooxml import xmlsurgery as xs
from ..ooxml.opc import Package

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
ABSPATH_NS = "http://schemas.microsoft.com/office/spreadsheetml/2010/11/ac"
# Excel's co-authoring ("revision") namespaces: xr, xr2, xr3, xr6, xr10, xr16 ...
# Every attribute in them is an ID or a co-authoring counter.
_XR_NS = re.compile(rb'xmlns:([A-Za-z_][\w.-]*)="(http://schemas\.microsoft\.com/office/'
                    rb'spreadsheetml/20\d\d/revision\d*)"')

DROP_PARTS = pf.DROP_PARTS + (
    re.compile(r"^xl/printerSettings/"),    # a DEVMODE naming the printer
    re.compile(r"^xl/webextensions/"),      # task-pane add-ins and their settings
    # Who commented: display name, e-mail or account id. Only threaded comments use
    # it, and those are refused below; on its own it is a list of people.
    re.compile(r"^xl/persons/"),
)

REFUSE_PARTS = (
    (re.compile(r"^xl/comments\d*\.xml$"),
     "comments (notes) are content shown on the sheet; refused at F1 rather than "
     "silently deleted"),
    (re.compile(r"^xl/threadedComments/"),
     "threaded comments are content shown on the sheet, with their authors; refused "
     "at F1 rather than silently deleted"),
    (re.compile(r"^xl/revisions/"),
     "a shared workbook's change log (every editor and every old value) is tracked "
     "changes; refused at F1"),
    (re.compile(r"^xl/externalLinks/"),
     "a link to another workbook holds its path and a cached copy of its cells, "
     "which formulas use; refused at F1"),
    (re.compile(r"^xl/pivotCache/pivotCacheRecords"),
     "a pivot table's cache holds a full copy of its source data, which may be on no "
     "sheet at all; refused at F1"),
    (re.compile(r"^xl/connections\.xml$"),
     "data connections name servers and accounts, and the workbook refreshes from "
     "them; refused at F1"),
    (re.compile(r"^xl/queryTables/"),
     "query tables are filled from a data connection; refused at F1"),
    (re.compile(r"^xl/model/"),
     "the Power Pivot data model is a database of its own; refused at F1"),
)

# Credentials: password hashes, salts and spin counts, a reservation password, the
# Windows security descriptor of a protected range, and who reserved the file. The
# lock flags stay, so a protected sheet stays protected -- without a password.
_CREDENTIAL_TAGS = ("workbookProtection", "sheetProtection", "protectedRange",
                    "fileSharing")
_CREDENTIAL_ATTRS = re.compile(
    r"password|hashValue|saltValue|spinCount|algorithmName|securityDescriptor"
    r"|(?:workbook|revisions)(?:Password|HashValue|SaltValue|SpinCount|AlgorithmName)"
    r"|reservationPassword|userName")
# Where the window was on the screen it was last open on.
_WINDOW_ATTRS = re.compile(r"xWindow|yWindow|windowWidth|windowHeight")
# Who refreshed a pivot table, and when.
_REFRESH_ATTRS = r"refreshedBy|refreshedDate|refreshedDateIso"


def _prefix(body: bytes) -> bytes:
    """The written prefix of SpreadsheetML in this part: b'' when it is the default
    namespace (what Excel writes), b'x:' and the like otherwise (the Open XML SDK)."""
    m = re.search(rb'xmlns:([A-Za-z_][\w.-]*)="' + re.escape(MAIN_NS.encode()) + rb'"',
                  body)
    if m and not re.search(rb'xmlns="' + re.escape(MAIN_NS.encode()) + rb'"', body):
        return m.group(1) + b":"
    return b""


def _strip_attrs_in(body: bytes, tags: tuple[str, ...], attrs: re.Pattern,
                    prefix: bytes) -> bytes:
    names = b"|".join(re.escape(prefix + t.encode()) for t in tags)

    def tag(m):
        out, _ = xs.remove_attributes(m.group(0), attrs.pattern)
        return out
    return re.sub(rb"<(?:" + names + rb")(?=[\s/>])[^>]*>", tag, body)


def _cut_abspath(body: bytes) -> bytes:
    """`x15ac:absPath` sits in an `mc:AlternateContent` of its own. The wrapper goes
    with it when absPath is all it holds; otherwise only the element goes."""
    if ABSPATH_NS.encode() not in body:
        return body
    els = xmlspans.parse(body, "workbook")
    spans = []
    for el in els:
        if el.uri != ABSPATH_NS or el.local != "absPath":
            continue
        span = (el.start, el.end)
        node = el
        while node.parent is not None:
            parent = els[node.parent]
            if parent.uri == MC_NS and parent.local in ("Choice", "AlternateContent"):
                if _only_abspath(els, node.parent):
                    span = (parent.start, parent.end)
                    node = parent
                    continue
            break
        spans.append(span)
    return xmlspans.cut(body, spans)


def _only_abspath(els: list, index: int) -> bool:
    for c in els[index].children:
        el = els[c]
        if el.uri == ABSPATH_NS and el.local == "absPath":
            continue
        if el.uri == MC_NS and el.local == "Choice" and _only_abspath(els, c):
            continue
        return False
    return True


def _referenced_strings(parts: dict[str, bytes]) -> set[int]:
    """Indexes into the shared-string table that some cell uses -- in EVERY part,
    not in the parts named like worksheets: a sheet stored under another path would
    otherwise have its text blanked as unused."""
    used: set[int] = set()
    for name, body in parts.items():
        if name == "xl/sharedStrings.xml" or MAIN_NS.encode() not in body:
            continue
        els = xmlspans.parse(body, name)
        for el in els:
            if el.uri == MAIN_NS and el.local == "c" and el.attr("t") == "s":
                for c in el.children:
                    v = els[c]
                    if v.uri == MAIN_NS and v.local == "v":
                        try:
                            used.add(int(v.text.strip()))
                        except ValueError:
                            raise ParseError(f"{name}: a shared-string cell whose "
                                             "index is not a number") from None
    return used


def _blank_unused_strings(body: bytes, used: set[int]) -> bytes:
    """Text in the shared-string table that no cell shows: what was typed and then
    deleted, or a sheet's text removed by another program. Blanked in place -- the
    entry stays, so every other cell's index still points at the same string."""
    els = xmlspans.parse(body, "xl/sharedStrings.xml")
    root = els[0]
    edits = []
    index = 0
    for c in root.children:
        si = els[c]
        if si.uri != MAIN_NS or si.local != "si":
            continue
        if index not in used and si.inner[0] < si.inner[1] and _has_text(els, c):
            written = re.match(rb"<([\w.:-]+)", body[si.start:si.open_end]).group(1)
            prefix = written[:-2] if written.endswith(b"si") else b""
            edits.append((si.inner[0], si.inner[1], b"<" + prefix + b"t/>"))
        index += 1
    return xmlspans.splice(body, edits) if edits else body


def _has_text(els: list, index: int) -> bool:
    stack = [index]
    while stack:
        el = els[stack.pop()]
        if el.text.strip():
            return True
        stack.extend(el.children)
    return False


def _scrub_xml(name: str, body: bytes, ctx: dict) -> bytes:
    body, _ = xs.remove_comments(body)
    body, _ = xs.remove_processing_instructions(body)
    p = _prefix(body)
    xr = {m.group(1).decode() for m in _XR_NS.finditer(body)}
    if xr:
        body, _ = xs.remove_attributes(
            body, "(?:" + "|".join(re.escape(x) for x in sorted(xr)) + r"):[\w.-]+")
        for x in sorted(xr):
            body, _ = xs.remove_elements(body, f"{x}:revisionPtr")
    body = _cut_abspath(body)
    body = _strip_attrs_in(body, _CREDENTIAL_TAGS, _CREDENTIAL_ATTRS, p)
    body = _strip_attrs_in(body, ("workbookView", "customWorkbookView"), _WINDOW_ATTRS, p)
    for tag in ("selection", "customWorkbookViews", "customSheetViews",
                "securityDescriptor"):
        body, _ = xs.remove_elements(body, p.decode() + tag)
    if name.startswith("xl/pivotCache/pivotCacheDefinition"):
        body, _ = xs.remove_attributes(body, _REFRESH_ATTRS)
    if name == "xl/sharedStrings.xml":
        body = _blank_unused_strings(body, ctx["used_strings"])
    body = pf.drop_rid_refs(body, ctx["dropped_rids"].get(name, set()))
    body = pf.blank_file_names(body)
    return pf.drop_linked_paths(body)


def _scrub_vml(body: bytes) -> bytes:
    # Legacy VML (note shapes, header and footer pictures) is not always
    # well-formed XML, so only the two shared regex rules run on it.
    return pf.drop_linked_paths(pf.blank_file_names(body))


def _preflight(pkg: Package) -> list[str]:
    problems = pf.preflight(pkg) + pf.malformed_parts(pkg, _is_dropped)
    for name in pkg.parts():
        for pat, why in REFUSE_PARTS:
            if pat.search(name):
                problems.append(f"{name}: {why}")
        # Power Query keeps its queries (paths, URLs, server names) in a custom XML
        # part, which the package rules drop; dropping it would silently break every
        # query, so a workbook carrying one is refused instead.
        if name.startswith("customXml/") and name.endswith(".xml"):
            try:
                if b"DataMashup" in pkg.archive.by_name(name).content():
                    problems.append(f"{name}: Power Query's queries; refused at F1")
            except ParseError as exc:
                problems.append(str(exc))
    return problems


def _context(pkg: Package, dropped: set[str]) -> dict:
    parts = {}
    for entry in pkg.archive.entries:
        if not entry.is_dir and entry.name.endswith(".xml"):
            parts[entry.name] = entry.content()
    return {"used_strings": _referenced_strings(parts),
            "dropped_rids": pf.dropped_rids_by_source(pkg, dropped)}


def _is_dropped(name: str) -> bool:
    return pf.is_dropped(name, DROP_PARTS)


def scrub(data: bytes) -> bytes:
    pkg = opc.parse(data)
    if pkg.flavour != "xlsx":
        raise ParseError(f"not an XLSX package (looks like {pkg.flavour})")
    pf.refuse_if(_preflight(pkg))

    dropped = {n for n in pkg.parts() if _is_dropped(n)}
    ctx = _context(pkg, dropped)
    kept: dict[str, bytes] = {}
    for entry in pkg.archive.entries:
        if entry.is_dir or entry.name in dropped:
            continue
        body = entry.content()
        if entry.name == opc.CONTENT_TYPES:
            body = pf.prune_content_types(body, dropped)
        elif entry.name.endswith(".rels"):
            body = pf.prune_rels(pf.rels_source(entry.name), body, dropped)
        elif entry.name.endswith(".xml"):
            body = _scrub_xml(entry.name, body, ctx)
        elif entry.name.endswith(".vml"):
            body = _scrub_vml(body)
        else:
            # Pictures, wherever they are stored, and any other binary part: cleaned
            # by the handler its CONTENT names, or refused -- never passed through
            # because of the folder it sits in.
            body = pf.scrub_media(entry.name, body, _NO_HANDLER_HINT)
        kept[entry.name] = body
    return pf.write_checked(kept, dropped)


_NO_HANDLER_HINT = {".wdp": "HD Photo (JPEG XR)"}


def residuals(data: bytes) -> list[str]:
    """Metadata F1 should have removed and did not -- scrub FAILURES only.

    Every rule above is a deletion, so a clean part is one the rules leave
    unchanged: running them again over the output is the check, rule by rule, and
    a part they would still change names itself."""
    try:
        pkg = opc.parse(data)
    except ParseError as exc:
        return [f"unparseable: {exc}"]
    out: list[str] = []
    try:
        ctx = _context(pkg, set())
    except ParseError as exc:
        return [str(exc)]
    for entry in pkg.archive.entries:
        if entry.is_dir:
            continue
        name = entry.name
        if _is_dropped(name):
            out.append(f"{name}: a part that F1 drops is still present")
            continue
        try:
            body = entry.content()
            if name.endswith(".xml") and name != opc.CONTENT_TYPES:
                if _scrub_xml(name, body, ctx) != body:
                    out.append(f"{name}: still holds what F1 removes")
            elif name.endswith(".vml") and _scrub_vml(body) != body:
                out.append(f"{name}: a picture's name or link path survived")
        except ParseError as exc:
            out.append(str(exc))
    return out


def advisories(data: bytes) -> list[str]:
    """What F1 knowingly keeps, reported rather than hidden: content the reader
    may not see, links out of the file, and Excel's own version. Counts and kinds,
    never values -- this travels with the file in folder mode."""
    try:
        pkg = opc.parse(data)
    except ParseError as exc:
        return [f"unparseable: {exc}"]
    out: list[str] = []
    for r in pkg.external_targets():
        out.append(f"external link target survives: {r.target}")
    workbook = pkg.archive.by_name("xl/workbook.xml")
    if workbook is not None:
        els = xmlspans.parse(workbook.content(), "xl/workbook.xml")
        states = [el.attr("state") for el in els if el.uri == MAIN_NS and el.local == "sheet"]
        for state, label in (("hidden", "hidden"), ("veryHidden", "very hidden")):
            n = states.count(state)
            if n:
                out.append(f"{n} {label} sheet(s) kept: content the reader does not see")
        n = sum(1 for el in els if el.uri == MAIN_NS and el.local == "definedName"
                and el.attr("hidden") in ("1", "true"))
        if n:
            out.append(f"{n} hidden defined name(s) kept")
        if any(el.uri == MAIN_NS and el.local == "fileVersion" for el in els):
            out.append("xl/workbook.xml: fileVersion and calcId name Excel's build; "
                       "kept at F1")
    rows = cols = headers = 0
    for entry in pkg.archive.entries:
        if not (pkg.content_type(entry.name) or "").endswith(".worksheet+xml"):
            continue
        for el in xmlspans.parse(entry.content(), entry.name):
            if el.uri != MAIN_NS:
                continue
            if el.local == "row" and el.attr("hidden") in ("1", "true"):
                rows += 1
            elif el.local == "col" and el.attr("hidden") in ("1", "true"):
                cols += 1
            elif el.local in ("oddHeader", "oddFooter", "evenHeader", "evenFooter",
                              "firstHeader", "firstFooter") and el.text.strip():
                headers += 1
    if rows or cols:
        out.append(f"{rows} hidden row(s) and {cols} hidden column range(s) kept")
    if headers:
        out.append(f"{headers} header/footer text(s) kept: printed content, may name "
                   "a person or a file")
    return out
