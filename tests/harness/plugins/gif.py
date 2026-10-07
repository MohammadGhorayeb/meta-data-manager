"""GifPlugin — harness-side format knowledge for GIF.

Content identity is decoded: every frame's RGBA through Pillow (a writer may reorder
a palette; the colours are what must not change). `structural_features` is the A2
channel: the version, the screen descriptor's flags, the block sequence, each
image's LZW code size and colour-table use, and the size.
"""
from __future__ import annotations

import hashlib

from src.scrub.formats.gif import f1


class GifPlugin:
    format_id = "gif"

    def matches(self, header: bytes, path: str = "") -> bool:
        return header[:6] in (b"GIF87a", b"GIF89a")

    def annotate(self, in_path: str, offset: int) -> str | None:
        data = open(in_path, "rb").read()
        try:
            _, blocks, _ = f1._blocks(data)
        except Exception:                                 # noqa: BLE001
            return None
        for kind, start, end, app in blocks:
            if start <= offset < end:
                return f"{kind}:{app.decode('latin-1')}" if app else kind
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
            _, blocks, _ = f1._blocks(data)
        except Exception:                                 # noqa: BLE001
            return {}
        images = []
        for kind, start, _end, _ in blocks:
            if kind == "image":
                fl = data[start + 9]
                lct = 3 * (2 << (fl & 7)) if fl & 0x80 else 0
                images.append((fl, data[start + 10 + lct]))
        return {"version": bytes(data[:6]), "screen_flags": data[10],
                "background": data[11], "aspect": data[12],
                "blocks": tuple(k if not a else f"{k}:{a.decode('latin-1')}"
                                for k, _, _, a in blocks),
                "images": tuple(images), "size": len(data)}
