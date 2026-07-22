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

    def structural_features(self, path: str) -> dict:
        """A2 structural fingerprint channel (p1 plan W2/E3; fields.py hook).

        Exposes the content-independent *encoder* structure that the variance
        engine tests for producer separation — above all the quantization
        tables (DQT), the fingerprint Kornblum showed classifies the producer
        and that survives F1/F2 by definition. Also emits the Huffman-table
        digest, the marker skeleton, and SOF geometry so E3 can see which
        structural facts separate producers and which the scrubber normalizes.

        All values are hashable tokens (hex strings / tuples) so they drop
        straight into the variance grid. Returns {} on any parse failure — a
        structural feature we cannot read is not a silent pass, it is absent.
        """
        import hashlib
        try:
            with open(path, "rb") as f:
                data = f.read()
            structure = seg.walk(data)
        except Exception:
            return {}

        def _sha(chunks: bytes) -> str:
            return hashlib.sha1(chunks).hexdigest()[:16]

        dqt = b"".join(s.payload for s in structure.by_kind("dqt"))
        dht = b"".join(s.payload for s in structure.by_kind("dht"))

        feats: dict = {
            "dqt": _sha(dqt) if dqt else "none",
            "dht": _sha(dht) if dht else "none",
            "markers": tuple(s.kind for s in structure.segments),
        }
        sof = next((s for s in structure.segments if s.kind.startswith("sof")), None)
        if sof is not None and len(sof.payload) >= 6:
            p = sof.payload
            precision = p[0]
            ncomp = p[5]
            sampling = tuple(p[7 + i * 3] for i in range(ncomp)
                             if 7 + i * 3 < len(p))
            feats["sof"] = (sof.kind, precision, ncomp, sampling)
        return feats
