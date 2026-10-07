"""WebpPlugin — harness-side format knowledge for WebP.

Content identity is decoded, never parsed: every frame's pixels through Pillow.
`structural_features` is the A2 channel: the chunk sequence, the VP8X flags, the
animation header, and the size. The coded bitstream itself is left out, as MP4
leaves out its video: F1 copies it by definition.
"""
from __future__ import annotations

import hashlib

from src.scrub.formats.webp import f1


class WebpPlugin:
    format_id = "webp"

    def matches(self, header: bytes, path: str = "") -> bool:
        return header[:4] == b"RIFF" and header[8:12] == b"WEBP"

    def annotate(self, in_path: str, offset: int) -> str | None:
        data = open(in_path, "rb").read()
        try:
            for fourcc, at, n in f1.chunks(data, 12, len(data)):
                if at - 8 <= offset < at + n:
                    return fourcc.decode("latin-1").strip()
        except Exception:                                 # noqa: BLE001
            return None
        return None

    def canonical_content(self, path: str) -> bytes:
        from PIL import Image
        h = hashlib.sha256()
        with Image.open(path) as im:
            for k in range(getattr(im, "n_frames", 1)):
                im.seek(k)
                h.update(im.convert("RGBA").tobytes())
        return h.digest()

    def mandatory_constants(self) -> list[bytes]:
        return []

    def structural_features(self, path: str) -> dict:
        data = open(path, "rb").read()
        try:
            found = f1.chunks(data, 12, len(data))
        except Exception:                                 # noqa: BLE001
            return {}
        feats = {"chunks": tuple(f for f, _, _ in found), "size": len(data)}
        for fourcc, at, n in found:
            if fourcc == b"VP8X":
                feats["vp8x_flags"] = data[at]
            if fourcc == b"ANIM":
                feats["anim"] = bytes(data[at:at + n])
        return feats


