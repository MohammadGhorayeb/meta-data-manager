"""PngPlugin — harness-side format knowledge for PNG (FormatPlugin).

Mirrors JpegPlugin: magic dispatch, offset->chunk annotation for evidence,
decoded-pixel content identity, format-mandatory constants for the fingerprint
guard, and the A2 structural channel. For PNG the producer fingerprint lives in
the deflate stream (IDAT) and the chunk skeleton rather than a quant table, so
structural_features exposes those.
"""
from __future__ import annotations

import hashlib
from typing import Optional

from PIL import Image

from src.scrub.formats.png import chunks as ck


class PngPlugin:
    format_id = "png"

    def matches(self, header: bytes, path: str = "") -> bool:
        return header[:8] == ck.PNG_SIGNATURE

    def annotate(self, in_path: str, offset: int) -> Optional[str]:
        with open(in_path, "rb") as f:
            data = f.read()
        try:
            structure = ck.walk(data)
        except Exception:
            return None
        for c in structure.chunks:
            if c.offset <= offset < c.end:
                return f"{c.ctype}@+{offset - c.offset}"
        if offset >= structure.trailer_offset:
            return f"trailer@+{offset - structure.trailer_offset}"
        return None

    def canonical_content(self, path: str) -> bytes:
        """Decoded pixels in a fixed mode (RGBA captures colour+alpha for every
        PNG mode) — the content-identity definition for PNG."""
        with Image.open(path) as im:
            return im.convert("RGBA").tobytes()

    def mandatory_constants(self) -> list[bytes]:
        # The 8-byte signature and the two mandatory chunk types every PNG has.
        return [ck.PNG_SIGNATURE, b"IHDR", b"IEND"]

    def structural_features(self, path: str) -> dict:
        """A2 structural channel: the deflate fingerprint (IDAT digest), the
        chunk skeleton, and IHDR encoder geometry (bit depth / colour type /
        interlace — content-independent producer choices; width/height excluded
        as those are content). Returns {} on parse failure."""
        try:
            with open(path, "rb") as f:
                data = f.read()
            structure = ck.walk(data)
        except Exception:
            return {}
        idat = b"".join(c.data for c in structure.by_type("IDAT"))
        feats: dict = {
            "idat": hashlib.sha1(idat).hexdigest()[:16] if idat else "none",
            "chunks": tuple(c.ctype for c in structure.chunks),
        }
        ihdr = next((c for c in structure.chunks if c.ctype == "IHDR"), None)
        if ihdr is not None and len(ihdr.data) >= 13:
            # IHDR: width(4) height(4) bitdepth(1) colortype(1) compression(1)
            #       filter(1) interlace(1). Skip width/height (content).
            feats["ihdr"] = tuple(ihdr.data[8:13])
        return feats
