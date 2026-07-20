"""JpegPlugin — harness-side format knowledge for JPEG (FormatPlugin).

Gives the A1/A2 oracles JPEG-aware behaviour: magic dispatch, offset→segment
annotation for evidence, decoded-pixel content-identity (so metadata-variant
files count as the same content), and the format-mandatory constants the
fingerprint guard must exclude.

Note (p1 plan §5): annotate() reuses the scrubber's walker, so a walker blind
spot would mislabel a leak's *location* — but never the verdict, which comes from
raw byte-space variance in the oracle, wholly independent of this walker. The
independent-parse cross-check (exiftool) is a separate M1 verification step.
"""
from __future__ import annotations

import io
from typing import Optional

from PIL import Image

from src.scrub.formats.jpeg import segments as seg


class JpegPlugin:
    format_id = "jpeg"

    def matches(self, header: bytes, path: str) -> bool:
        return header[:3] == b"\xff\xd8\xff"

    def annotate(self, in_path: str, offset: int) -> Optional[str]:
        with open(in_path, "rb") as f:
            data = f.read()
        try:
            structure = seg.walk(data)
        except Exception:
            return None
        for s in structure.segments:
            if s.offset <= offset < s.end:
                return f"{s.kind}@+{offset - s.offset}"
        if offset >= structure.trailer_offset:
            return f"trailer@+{offset - structure.trailer_offset}"
        return None

    def canonical_content(self, path: str) -> bytes:
        """Decoded pixels in a fixed mode — the content-identity definition for
        JPEG (p1 plan W2): two files with identical pixels but different metadata
        are the same content, which is exactly what A1 needs."""
        with Image.open(path) as im:
            return im.convert("RGB").tobytes()

    def mandatory_constants(self) -> list[bytes]:
        # Format-required byte invariants, excluded from the fingerprint guard so
        # SOI/EOI aren't mistaken for a tool signature.
        return [b"\xff\xd8", b"\xff\xd9"]
