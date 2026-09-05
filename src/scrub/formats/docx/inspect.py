"""What a Word document's metadata says — for the scrub report.

Reuses the disposition thinking behind the locus census rather than re-deriving it:
the properties parts are read field by field, and the editing-session identifiers —
the family MAT2 leaves and the literature does not name — are counted, because
listing 36 opaque hex ids would bury everything a person can actually read.
"""
from __future__ import annotations

import re
from xml.etree import ElementTree as ET

from ..ooxml import opc

_PROPS_PARTS = ("docProps/core.xml", "docProps/app.xml", "docProps/custom.xml",
                "docProps/meta.xml")

# Properties worth naming. Everything else in those parts is counted.
_INTERESTING = {
    "creator", "lastModifiedBy", "lastPrinted", "revision", "created", "modified",
    "title", "subject", "keywords", "description", "category", "company",
    "manager", "Application", "AppVersion", "Company", "Manager", "Template",
    "TotalTime", "creator ", "generator",
}

# The session-id family, counted rather than listed (see E-SESSION-ID).
_SESSION_IDS = {
    "w:rsid attributes": re.compile(rb'\sw:rsid[A-Za-z]*="'),
    "w:rsid elements": re.compile(rb"<w:rsid[\s/>]"),
    "w14:paraId / textId": re.compile(rb'\sw1[0-9]:(?:paraId|textId)="'),
    "docId (per-document GUID)": re.compile(rb"<w1[0-9]:docId[\s/>]"),
}

_NAMED_PARTS = {
    "word/people.xml": "comment/revision authors",
    "word/comments.xml": "comments",
    "docProps/thumbnail.jpeg": "page-1 thumbnail (with its own EXIF)",
    "docProps/thumbnail.emf": "page-1 thumbnail",
    "word/vbaProject.bin": "macros",
}


def describe(data: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        pkg = opc.parse(data)
    except Exception:                                     # noqa: BLE001
        return out

    parts = {}
    for entry in pkg.archive.entries:
        if entry.is_dir:
            continue
        try:
            parts[entry.name] = entry.content()
        except Exception:                                 # noqa: BLE001
            continue

    for name in _PROPS_PARTS:
        body = parts.get(name)
        if body is None:
            continue
        short = name.split("/")[-1].replace(".xml", "")
        out.update(_properties(short, body))

    for name, label in _NAMED_PARTS.items():
        if name in parts:
            out[label] = f"({len(parts[name])} bytes)"

    media = [n for n in parts if n.startswith("word/media/")]
    if media:
        out["embedded media"] = f"{len(media)} file(s)"

    counts: dict[str, int] = {}
    for name, body in parts.items():
        if not name.endswith(".xml"):
            continue
        for label, rx in _SESSION_IDS.items():
            n = len(rx.findall(body))
            if n:
                counts[label] = counts.get(label, 0) + n
    for label, n in counts.items():
        out[label] = f"{n} occurrence(s)"

    # A template reference is frequently a UNC path naming a user or a share.
    settings = parts.get("word/settings.xml", b"")
    if b"<w:documentProtection" in settings:
        out["document protection"] = "password hash + salt present"
    if b"<w:mailMerge" in settings:
        out["mail merge"] = "data-source reference present"

    for rel in pkg.external_targets():
        out[f"external link ({rel.id})"] = rel.target
    return out


def _properties(part: str, body: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return {f"{part}.xml": f"({len(body)} bytes, unparseable)"}

    other = 0
    for el in root.iter():
        if el is root:
            continue
        text = (el.text or "").strip()
        if not text:
            continue
        tag = el.tag.split("}")[-1]
        if tag in _INTERESTING:
            out[f"{part}:{tag}"] = text
        else:
            other += 1
    # `custom.xml` holds its value in a child of `<property name=...>`, so the name
    # is on the parent -- worth surfacing, since Purview sensitivity labels live
    # here and carry a tenant GUID.
    for prop in root.iter():
        name = prop.get("name")
        if name and part == "custom":
            value = "".join(c.text or "" for c in prop).strip()
            out[f"custom:{name}"] = value or "(empty)"
            other = max(0, other - 1)
    if other:
        out[f"{part}: other properties"] = f"{other} more"
    return out
