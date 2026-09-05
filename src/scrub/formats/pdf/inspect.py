"""What a PDF's metadata says — for the scrub report.

Read through pikepdf, which is the better object parser and is already a dependency
(the W0 decision moved byte *emission* into this project, not byte *interpretation*).
The revision count comes from our own walker instead, because that is the leak this
phase exists for and a library that presents the file as one logical document cannot
see it.
"""
from __future__ import annotations

from . import walker as w


def describe(data: bytes) -> dict[str, str]:
    out: dict[str, str] = {}

    # Revision history first: it is the headline leak of the format, and a reader
    # who sees "3 revisions" understands the scrub better than one who sees a list
    # of cleared tags.
    try:
        layout = w.walk(data)
        if len(layout.revisions) > 1:
            out["revisions"] = (f"{len(layout.revisions)} — earlier drafts are still "
                                f"in the file")
        if layout.superseded:
            out["superseded objects"] = f"{len(layout.superseded)} old object version(s)"
    except Exception:                                     # noqa: BLE001
        pass

    try:
        import pikepdf
    except ImportError:
        return out

    try:
        with pikepdf.open(_io(data)) as pdf:
            info = pdf.trailer.get("/Info")
            if info is not None:
                for key in info.keys():
                    value = info.get(key)
                    text = str(value)
                    if text.strip():
                        out[f"Info:{str(key).lstrip('/')}"] = text
            meta = pdf.Root.get("/Metadata")
            if meta is not None:
                out["XMP packet"] = f"({len(bytes(meta.read_raw_bytes()))} bytes)"
            ident = pdf.trailer.get("/ID")
            if ident is not None:
                out["/ID"] = bytes(ident[0]).hex()[:32]
            for i, page in enumerate(pdf.pages):
                if "/Thumb" in page:
                    out[f"page {i + 1} thumbnail"] = "(embedded preview)"
                if "/PieceInfo" in page:
                    out[f"page {i + 1} PieceInfo"] = "(application private data)"
    except Exception:                                     # noqa: BLE001
        return out
    return out


def _io(data: bytes):
    import io
    return io.BytesIO(data)
