"""BmpPlugin — harness-side format knowledge for BMP.

Content identity is decoded: the picture's RGBA through Pillow (a writer may store
the rows top-down or bottom-up, or as a palette; the colours are what must not
change). `structural_features` is the A2 channel: the header version, the row
order, the depth and compression, the masks, the colour space and intent, the
palette size, the resolution and the size. The pixel bytes themselves are left
out, as GIF leaves out its LZW data: F1 copies them by definition.
"""
from __future__ import annotations

import hashlib
import struct

from src.scrub.formats.bmp import f1


class BmpPlugin:
    format_id = "bmp"

    def matches(self, header: bytes, path: str = "") -> bool:
        return f1.is_bmp(header)

    def annotate(self, in_path: str, offset: int) -> str | None:
        data = open(in_path, "rb").read()
        try:
            b = f1._parse(data)
        except Exception:                                 # noqa: BLE001
            return None
        head_end = 14 + b.hs + b.masks_len
        if offset < 14:
            return "file_header"
        if offset < 14 + b.hs:
            return "info_header"
        if offset < head_end + (b.colors + b.extra_palette) * b.entry:
            return "masks_palette"
        if b.profile and b.profile[0] <= offset < b.profile[1]:
            return "profile"
        if b.pixels[0] <= offset < b.pixels[1]:
            return "pixels"
        return "outside"

    def canonical_content(self, path: str) -> bytes:
        from PIL import Image
        with Image.open(path) as im:
            return hashlib.sha256(im.convert("RGBA").tobytes()).digest()

    def mandatory_constants(self) -> list[bytes]:
        return []

    def structural_features(self, path: str) -> dict:
        data = open(path, "rb").read()
        try:
            b = f1._parse(data)
        except Exception:                                 # noqa: BLE001
            return {}
        feats = {"header": b.hs, "top_down": b.top_down, "bpp": b.bpp,
                 "compression": b.comp, "palette": b.colors + b.extra_palette,
                 "offset": b.off, "size": len(data),
                 "reserved": bytes(data[6:10])}
        if b.hs > 12:
            feats["resolution"] = struct.unpack_from("<ii", data, 38)
            feats["colors_field"], feats["important"] = struct.unpack_from(
                "<II", data, 46)
            feats["size_image"] = struct.unpack_from("<I", data, 34)[0]
        if b.hs > 40 or b.masks_len:
            feats["masks"] = bytes(data[54:14 + min(b.hs, 56)]) + bytes(
                data[14 + b.hs:14 + b.hs + b.masks_len])
        if b.hs >= 108:
            feats["colour_space"] = b.cs
            feats["cs_fields"] = bytes(data[74:122])
        if b.hs >= 124:
            feats["intent"] = struct.unpack_from("<I", data, 122)[0]
        return feats
