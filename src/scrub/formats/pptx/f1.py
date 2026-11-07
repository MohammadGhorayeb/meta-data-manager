"""PPTX F1 — PowerPoint's own loci, on the shared OOXML package layer.

The package rules (properties, thumbnail, custom XML, pictures, relationships, the
orphan check) are `ooxml/package_f1.py`, shared with Word and Excel. This module
adds what PowerPoint writes (`docs/office_plan.md` §3) and keeps F1's promise: we
only delete. Every slide's text and geometry is unchanged afterwards.

What the probes measured (§2), and MAT2 0.14.0 leaves: `a16:creationId`, a GUID
on every shape -- designed to stay with it when it is copied into another deck,
which is not yet measured here (MAT2 randomizes only the older `p14:creationId`,
the per-slide one) -- and a picture's file path. What MAT2 refuses outright and we remove: `revisionInfo.xml` (the
GUID of every client that saved the file) and `changesInfos/` (co-authors' names
and account IDs).

Refused rather than cleaned: comments (shown on the slide, with their authors),
and embedded fonts, whose key and names are not measured yet. Speaker notes and
hidden slides are content -- kept, and counted in the report, because they are
what people most often forget a deck still carries.
"""
from __future__ import annotations

import re

from ...errors import ParseError
from ..ooxml import opc, xmlspans
from ..ooxml import package_f1 as pf
from ..ooxml import xmlsurgery as xs
from ..ooxml.opc import Package

P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
A16_NS = "http://schemas.microsoft.com/office/drawing/2014/main"
P14_NS = "http://schemas.microsoft.com/office/powerpoint/2010/main"

# Per-object IDs, each in an extension of its own: a shape's GUID, a table row's
# and column's IDs, the shape an object was derived from, a slide's ID.
_ID_ELEMENTS = {(A16_NS, "creationId"), (A16_NS, "rowId"), (A16_NS, "colId"),
                (A16_NS, "predDERef"), (P14_NS, "creationId")}

DROP_PARTS = pf.DROP_PARTS + (
    re.compile(r"^ppt/printerSettings/"),    # a DEVMODE naming the printer
    re.compile(r"^ppt/webextensions/"),      # task-pane add-ins and their settings
    # Add-in data (`p:custDataLst/p:tags`): nothing draws it.
    re.compile(r"^ppt/tags/"),
    re.compile(r"^ppt/revisionInfo\.xml$"),  # the GUID of every client that saved it
    re.compile(r"^ppt/changesInfos/"),       # co-authors' names and account IDs
    re.compile(r"^ppt/viewProps\.xml$"),     # the view it was last left in
    # Who commented. Comments themselves are refused below, so these lists of
    # people only reach F1 when no comment is left.
    re.compile(r"^ppt/commentAuthors\.xml$"),
    re.compile(r"^ppt/authors\.xml$"),
)

REFUSE_PARTS = (
    (re.compile(r"^ppt/comments/"),
     "comments are content shown on the slide, with their authors; refused at F1 "
     "rather than silently deleted"),
    (re.compile(r"^ppt/fonts/"),
     "embedded fonts: what their key and names hold is not measured yet; refused "
     "at F1"),
)

_NO_HANDLER_HINT = {".wdp": "HD Photo (JPEG XR)"}


def _prefix(body: bytes, uri: str, default: bytes) -> bytes:
    """The written prefix for `uri` in this part (`p:` almost always)."""
    m = re.search(rb'xmlns:([A-Za-z_][\w.-]*)="' + re.escape(uri.encode()) + rb'"', body)
    return m.group(1) + b":" if m else default


def _cut_id_extensions(body: bytes) -> bytes:
    """Remove every extension that holds nothing but a per-object ID, and an
    extension list left with nothing in it: an empty `<a:extLst/>` would mark the
    file as edited."""
    if A16_NS.encode() not in body and P14_NS.encode() not in body:
        return body
    els = xmlspans.parse(body)
    only_ids = set()
    for i, el in enumerate(els):
        if el.local == "ext" and el.uri in (A_NS, P_NS) and el.children and all(
                (els[c].uri, els[c].local) in _ID_ELEMENTS for c in el.children):
            only_ids.add(i)
    spans = []
    for el in els:
        if el.local != "extLst" or el.uri not in (A_NS, P_NS):
            continue
        exts = [c for c in el.children if els[c].local == "ext"]
        if exts and all(c in only_ids for c in exts) and len(exts) == len(el.children):
            spans.append((el.start, el.end))
        else:
            spans += [(els[c].start, els[c].end) for c in exts if c in only_ids]
    return xmlspans.cut(body, spans)


def _scrub_xml(name: str, body: bytes, ctx: dict) -> bytes:
    body, _ = xs.remove_comments(body)
    body, _ = xs.remove_processing_instructions(body)
    body = _cut_id_extensions(body)
    p = _prefix(body, P_NS, b"p:").decode()
    if name == "ppt/presProps.xml":
        body, _ = xs.remove_elements(body, p + "clrMru")      # recently used colours
    body = pf.drop_rid_refs(body, ctx["dropped_rids"].get(name, set()),
                            owners=(p + "tags", p + "custData"))
    # A custom-data list emptied by the line above.
    body, _ = xs.remove_elements_where(
        body, p + "custDataLst", lambda tag: tag.rstrip().endswith(b"/>"))
    body = re.sub(rb"<" + re.escape(p.encode()) + rb"custDataLst>\s*</"
                  + re.escape(p.encode()) + rb"custDataLst>", b"", body)
    body = pf.blank_file_names(body)
    # Last: the field IDs are derived from the part as every other rule left it.
    return pf.canonical_field_ids(name, pf.drop_linked_paths(body))


def _scrub_vml(body: bytes) -> bytes:
    return pf.drop_linked_paths(pf.blank_file_names(body))


def _preflight(pkg: Package) -> list[str]:
    problems = pf.preflight(pkg) + pf.malformed_parts(pkg, _is_dropped)
    for name in pkg.parts():
        for pat, why in REFUSE_PARTS:
            if pat.search(name):
                problems.append(f"{name}: {why}")
    return problems


def _is_dropped(name: str) -> bool:
    return pf.is_dropped(name, DROP_PARTS)


def scrub(data: bytes) -> bytes:
    pkg = opc.parse(data)
    if pkg.flavour != "pptx":
        raise ParseError(f"not a PPTX package (looks like {pkg.flavour})")
    pf.refuse_if(_preflight(pkg))

    dropped = {n for n in pkg.parts() if _is_dropped(n)}
    ctx = {"dropped_rids": pf.dropped_rids_by_source(pkg, dropped)}
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
            # Pictures, video and audio, wherever they are stored, and any other
            # binary part: cleaned by the handler its CONTENT names, or refused.
            body = pf.scrub_media(entry.name, body, _NO_HANDLER_HINT)
        kept[entry.name] = body
    return pf.write_checked(kept, dropped)


def residuals(data: bytes) -> list[str]:
    """Metadata F1 should have removed and did not -- scrub FAILURES only. Every
    rule is a deletion, so running the rules again over the output is the check."""
    try:
        pkg = opc.parse(data)
    except ParseError as exc:
        return [f"unparseable: {exc}"]
    ctx = {"dropped_rids": {}}
    out: list[str] = []
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
    """What F1 knowingly keeps, reported rather than hidden. Counts and kinds,
    never values -- this travels with the file in folder mode."""
    try:
        pkg = opc.parse(data)
    except ParseError as exc:
        return [f"unparseable: {exc}"]
    out = [f"external link target survives: {r.target}" for r in pkg.external_targets()]
    notes = hidden = cropped = sections = 0
    versioned: set[str] = set()
    for entry in pkg.archive.entries:
        if entry.is_dir or not entry.name.endswith(".xml"):
            continue
        body = entry.content()
        versioned |= {m.decode() for m in re.findall(rb"xmlns:(p1[0-9]\d*|a1[0-9]\d*)=",
                                                     body[:4000])}
        if not entry.name.startswith("ppt/"):
            continue
        els = xmlspans.parse(body, entry.name)
        if entry.name.startswith("ppt/notesSlides/"):
            if any(e.uri == A_NS and e.local == "t" and e.text.strip() for e in els):
                notes += 1
        for e in els:
            if e.uri == P_NS and e.local == "sld" and e.attr("show") in ("0", "false"):
                hidden += 1
            elif e.uri == A_NS and e.local == "srcRect" and any(
                    (e.attr(k) or "0") not in ("0", "") for k in ("l", "t", "r", "b")):
                cropped += 1
            elif e.uri == P14_NS and e.local == "section":
                sections += 1
    if notes:
        out.append(f"{notes} slide(s) with speaker notes kept: text the audience does "
                   "not see")
    if hidden:
        out.append(f"{hidden} hidden slide(s) kept")
    if cropped:
        out.append(f"{cropped} cropped picture(s): the whole uncropped picture is still "
                   "in the file")
    if sections:
        out.append(f"{sections} section name(s) kept, with their IDs")
    if versioned:
        out.append(f"{len(versioned)} versioned namespaces declared "
                   f"({', '.join(sorted(versioned))}) — the set dates the producing "
                   "application; kept at F1")
    return out
