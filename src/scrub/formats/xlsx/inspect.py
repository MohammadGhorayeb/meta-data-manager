"""What an Excel workbook's metadata says — for the scrub report.

The properties parts are read field by field with Word's reader (they are the same
parts); Excel's own loci are named and counted, because a list of GUIDs would bury
everything a person can read.
"""
from __future__ import annotations

import re

from ..docx.inspect import _properties
from ..ooxml import opc

_PROPS_PARTS = ("docProps/core.xml", "docProps/app.xml", "docProps/custom.xml")

_COUNTED = {
    "save folder (x15ac:absPath)": re.compile(rb"<[\w.-]+:absPath[\s/>]"),
    "co-authoring IDs (xr* uid, revisionPtr)": re.compile(
        rb'\sxr\d*:[\w.-]+="|<xr\d*:revisionPtr[\s/>]'),
    "protection password hash": re.compile(rb'\s(?:\w*[Hh]ashValue|\w*[Pp]assword)="'),
}

_NAMED_PARTS = (
    (re.compile(r"^xl/comments\d*\.xml$"), "comments"),
    (re.compile(r"^xl/threadedComments/"), "threaded comments"),
    (re.compile(r"^xl/persons/"), "comment authors (persons)"),
    (re.compile(r"^xl/externalLinks/"), "links to other workbooks"),
    (re.compile(r"^xl/pivotCache/pivotCacheRecords"), "pivot cache records"),
    (re.compile(r"^xl/connections\.xml$"), "data connections"),
    (re.compile(r"^xl/printerSettings/"), "printer settings"),
    (re.compile(r"^docProps/thumbnail\."), "thumbnail"),
    (re.compile(r"^xl/vbaProject\.bin$"), "macros"),
)


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
        if name in parts:
            out.update(_properties(name.split("/")[-1].replace(".xml", ""), parts[name]))
    for rx, label in _NAMED_PARTS:
        n = sum(1 for p in parts if rx.search(p))
        if n:
            out[label] = f"{n} part(s)"
    media = [n for n in parts if n.startswith("xl/media/")]
    if media:
        out["embedded media"] = f"{len(media)} file(s)"
    for label, rx in _COUNTED.items():
        n = sum(len(rx.findall(b)) for p, b in parts.items() if p.endswith(".xml"))
        if n:
            out[label] = f"{n} occurrence(s)"
    for rel in pkg.external_targets():
        out[f"external link ({rel.id})"] = rel.target
    return out
