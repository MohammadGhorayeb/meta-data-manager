"""What a PowerPoint presentation's metadata says — for the scrub report.

The properties parts are read with Word's reader (they are the same parts);
PowerPoint's own loci are named and counted.
"""
from __future__ import annotations

import re

from ..docx.inspect import _properties
from ..ooxml import opc

_PROPS_PARTS = ("docProps/core.xml", "docProps/app.xml", "docProps/custom.xml")

_COUNTED = {
    "per-shape IDs (a16:creationId)": re.compile(rb"<[\w.-]+:creationId\s+id="),
    "per-slide IDs (p14:creationId)": re.compile(rb"<[\w.-]+:creationId\s+val="),
}

_NAMED_PARTS = (
    (re.compile(r"^ppt/comments/"), "comments"),
    (re.compile(r"^ppt/(commentAuthors|authors)\.xml$"), "comment authors"),
    (re.compile(r"^ppt/revisionInfo\.xml$"), "saving clients' IDs (revisionInfo)"),
    (re.compile(r"^ppt/changesInfos/"), "co-authors' change log"),
    (re.compile(r"^ppt/tags/"), "add-in tags"),
    (re.compile(r"^ppt/notesSlides/"), "speaker notes"),
    (re.compile(r"^ppt/printerSettings/"), "printer settings"),
    (re.compile(r"^docProps/thumbnail\."), "thumbnail"),
    (re.compile(r"^ppt/vbaProject\.bin$"), "macros"),
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
    media = [n for n in parts if n.startswith("ppt/media/")]
    if media:
        out["embedded media"] = f"{len(media)} file(s)"
    for label, rx in _COUNTED.items():
        n = sum(len(rx.findall(b)) for p, b in parts.items() if p.endswith(".xml"))
        if n:
            out[label] = f"{n} occurrence(s)"
    for rel in pkg.external_targets():
        out[f"external link ({rel.id})"] = rel.target
    return out
