"""PptxPlugin — harness-side format knowledge for PowerPoint presentations (.pptx).

The DOCX and XLSX design (`plugins/docx.py`, `plugins/xlsx.py`): two producers in
one file, reported apart --

- the **packager** -- the ZIP layer, DOCX's keys;
- the **presentation model** -- which parts exist (how many layouts and masters a
  producer ships, a notes master, printer settings), which elements the
  presentation holds and in what order, how it numbers slides, how a slide's shape
  tree is built.

`struct:size` is its own channel.

Content identity is the deck as shown: every slide's text in presentation order and
the decoded pixels of every picture.
"""
from __future__ import annotations

import hashlib
import io
import re
import xml.etree.ElementTree as ET

from src.scrub.formats.ooxml import opc
from tests.harness.plugins.docx import (
    PACKAGER_KEYS,
    DocxPlugin,
    _selfclose,
    _xml_decl,
    packager_features,
)
from tests.harness.plugins.xlsx import _local, _parts, _rels, guard_view, header_pieces

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PRES = "ppt/presentation.xml"

MODEL_KEYS = ("struct:entry_set", "struct:xml_decl", "struct:selfclose_style",
              "struct:namespaces", "struct:part_types", "struct:presentation_children",
              "struct:presentation_attrs", "struct:slide_id_start",
              "struct:slide_tree", "struct:layout_count", "struct:has_theme")
SIZE_KEYS = ("struct:size",)
CHANNELS = {"packager": PACKAGER_KEYS, "model": MODEL_KEYS, "size": SIZE_KEYS}


def slides(parts: dict[str, bytes]) -> list[str]:
    """Slide part names in presentation order."""
    body = parts.get(PRES)
    if not body:
        return []
    rels = _rels(parts, PRES)
    return [rels[el.get(f"{{{R}}}id")] for el in ET.fromstring(body).iter(f"{{{P}}}sldId")
            if el.get(f"{{{R}}}id") in rels]


def texts(data: bytes) -> list[str]:
    """Each slide's text runs joined, in presentation order."""
    _arc, parts = _parts(data)
    out = []
    for name in slides(parts):
        root = ET.fromstring(parts.get(name, b"<x/>"))
        out.append(" ".join(t.text or "" for t in root.iter(f"{{{A}}}t")))
    return out


class PptxPlugin:
    format_id = "pptx"

    def matches(self, header: bytes, path: str = "") -> bool:
        if header[:4] != b"PK\x03\x04" or not path:
            return False
        try:
            return opc.looks_like(open(path, "rb").read(), "pptx")
        except OSError:
            return False

    def annotate(self, in_path: str, offset: int) -> str | None:
        return DocxPlugin().annotate(in_path, offset)

    def canonical_content(self, path: str) -> bytes:
        try:
            data = open(path, "rb").read()
            arc, _parts_ = _parts(data)
        except Exception:                                 # noqa: BLE001
            return b""
        h = hashlib.sha256(repr(texts(data)).encode())
        from PIL import Image
        pixels = []
        for e in arc.entries:
            if e.name.startswith("ppt/media/") and e.name.lower().endswith(
                    (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff")):
                with Image.open(io.BytesIO(e.content())) as im:
                    pixels.append(hashlib.sha256(im.convert("RGB").tobytes()).digest())
        for p in sorted(pixels):
            h.update(p)
        return h.digest()

    def mandatory_constants(self) -> list[bytes]:
        """The ZIP layer's declared fields and the names and namespaces a
        presentation must use. (Text-field IDs, which F1 derives by hash, are blanked
        in the guard's view rather than declared: `plugins/xlsx.guard_view`.)"""
        return header_pieces() + [
            b"[Content_Types].xml", b"_rels/.rels", PRES.encode(),
            b"ppt/_rels/presentation.xml.rels", b"ppt/slides/slide1.xml",
            b"http://schemas.openxmlformats.org/package/2006/content-types",
            b"http://schemas.openxmlformats.org/package/2006/relationships",
            P.encode(), A.encode(), R.encode(),
        ]

    def guard_view(self, data: bytes) -> bytes:
        return guard_view(data)

    def structural_features(self, path: str) -> dict:
        try:
            data = open(path, "rb").read()
            arc, parts = _parts(data)
            pkg = opc.parse(data)
            pres = parts.get(PRES, b"")
            root = ET.fromstring(pres)
        except Exception:                                 # noqa: BLE001
            return {}
        order = slides(parts)
        first = parts.get(order[0], b"") if order else b""
        tree = ()
        if first:
            sp_tree = ET.fromstring(first).find(f".//{{{P}}}spTree")
            if sp_tree is not None:
                tree = tuple(_local(c.tag) for c in sp_tree)
        ids = [el.get("id", "") for el in root.iter(f"{{{P}}}sldId")]
        return {
            **packager_features(arc),
            # --- presentation model ---
            "entry_set": tuple(sorted(e.name for e in arc.entries)),
            "xml_decl": _xml_decl(pres),
            "selfclose_style": _selfclose(first),
            "namespaces": tuple(sorted({m.decode() for m in
                                        re.findall(rb"xmlns:([\w.-]+)=", pres)})),
            "part_types": tuple(sorted(pkg.overrides.values())),
            "presentation_children": tuple(_local(c.tag) for c in root),
            "presentation_attrs": tuple(sorted(_local(a) for a in root.attrib)),
            "slide_id_start": ids[0] if ids else "",
            "slide_tree": tree,
            "layout_count": sum(1 for n in arc.names
                                if re.fullmatch(r"ppt/slideLayouts/slideLayout\d+\.xml", n)),
            "has_theme": any(n.startswith("ppt/theme/") for n in arc.names),
        }
