"""MetafilePlugin — harness-side format knowledge for EMF and WMF.

Content identity is the drawing: every record that draws (EMF, EMF+ and WMF, the
same lists the scrubber's own safety net compares), with each EMF+ picture decoded
to pixels rather than taken as bytes -- its metadata is what F1 removes.
`structural_features` is the A2 channel: what a writer chooses that is not the
drawing -- header layout and version, the reference device, EMF+ DPI, the kinds of
record and comment, a WMF's units per inch, and the size.
"""
from __future__ import annotations

import hashlib
import io
import struct

from src.scrub.formats.metafile import emf, wmf


def _read(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def _pictures(data: bytes) -> list[bytes]:
    """Every EMF+ compressed picture, continuation chunks joined."""
    out, current = [], None
    recs, _ = emf.records(data)
    for t, s, _e in recs:
        if t != emf.EMR_GDICOMMENT or data[s + 12:s + 16] != b"EMF+":
            continue
        size = struct.unpack_from("<I", data, s + 8)[0]
        for ptype, flags, body, _ in emf._plus_records(data[s + 16:s + 12 + size]):
            if not emf._is_image(ptype, flags):
                continue
            if flags & emf.CONTINUED or current is not None:
                current = (current or b"") + body[4:]
                if not flags & emf.CONTINUED:
                    out.append(current)
                    current = None
            else:
                out.append(body)
    return [p for p in out if len(p) > 28 and struct.unpack_from("<I", p, 4)[0] == 1
            and struct.unpack_from("<I", p, 24)[0] == 1]


class MetafilePlugin:
    format_id = "metafile"

    def matches(self, header: bytes, path: str = "") -> bool:
        data = _read(path) if path else header
        return emf.is_emf(data) or wmf.is_wmf(data)

    def annotate(self, in_path: str, offset: int) -> str | None:
        data = _read(in_path)
        try:
            if emf.is_emf(data):
                recs, end = emf.records(data)
                hit = next((t for t, s, e in recs if s <= offset < e), None)
                return f"emf:record:{hit}" if hit is not None else (
                    "emf:after-eof" if offset >= end else None)
            _, _, recs, end = wmf._parse(data)
            hit = next((f for f, s, e in recs if s <= offset < e), None)
            return f"wmf:function:{hit:#06x}" if hit is not None else (
                "wmf:after-eof" if offset >= end else "wmf:header")
        except Exception:                                 # noqa: BLE001
            return None

    def canonical_content(self, path: str) -> bytes:
        from PIL import Image
        data = _read(path)
        h = hashlib.sha256()
        if emf.is_emf(data):
            for rec in emf._drawing(data):
                h.update(rec)
            for pic in _pictures(data):
                with Image.open(io.BytesIO(pic[28:])) as im:
                    h.update(im.convert("RGBA").tobytes())
        else:
            for rec in wmf._drawing(data):
                h.update(rec)
        return h.digest()

    def mandatory_constants(self) -> list[bytes]:
        return []

    def structural_features(self, path: str) -> dict:
        data = _read(path)
        try:
            if emf.is_emf(data):
                recs, _ = emf.records(data)
                head = data[:struct.unpack_from("<I", data, 4)[0]]
                return {"kind": "emf", "header_size": len(head),
                        "version": struct.unpack_from("<I", data, 44)[0],
                        "device": struct.unpack_from("<iiii", data, 72),
                        "records": tuple(t for t, *_ in recs), "size": len(data)}
            placeable, header, recs, _ = wmf._parse(data)
            return {"kind": "wmf", "placeable": bool(placeable),
                    "inch": struct.unpack_from("<H", placeable, 14)[0] if placeable else 0,
                    "version": struct.unpack_from("<H", header, 4)[0],
                    "functions": tuple(f for f, *_ in recs), "size": len(data)}
        except Exception:                                 # noqa: BLE001
            return {}
