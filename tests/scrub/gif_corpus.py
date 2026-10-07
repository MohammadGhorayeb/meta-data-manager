"""GIF corpus for CI: Pillow draws the frames, this module adds the blocks.

Every locus the survey measured (`docs/p6_tail_plan.md` §1): a comment extension,
an XMP application extension (with the 258-byte "magic trailer" XMP-in-GIF uses),
an unknown application extension, an ICC application extension carrying a profile
across sub-blocks, a plain-text extension (rendered text -- content, kept), and
bytes after the trailer. Values derive from `variant` at a fixed length.

Imports nothing from `src`.
"""
from __future__ import annotations

import io

from PIL import Image

from .tiff_corpus import CUSTOM_ICC, STANDARD_ICC

W, H = 30, 20


def secret(name: str, variant: int = 0) -> bytes:
    return f"SENTINEL-{name}-V{variant:02d}".encode()


def frame(seed: int = 0, k: int = 0) -> Image.Image:
    img = Image.new("P", (W, H))
    img.putpalette([(i * 3 + seed) & 0xFF for i in range(768)])
    img.putdata([((x * 5 + y * 3 + k * 11 + seed) & 0xFF) for y in range(H)
                 for x in range(W)])
    return img


def sub_blocks(payload: bytes) -> bytes:
    out = b""
    for i in range(0, len(payload), 255):
        piece = payload[i:i + 255]
        out += bytes([len(piece)]) + piece
    return out + b"\0"


def app_ext(ident: bytes, payload: bytes, raw: bool = False) -> bytes:
    body = payload if raw else sub_blocks(payload)
    return b"\x21\xff\x0b" + ident + body


def xmp_ext(variant: int = 0) -> bytes:
    """XMP in GIF: the packet as raw bytes (its own lengths are the data), then a
    258-byte magic trailer so a sub-block reader lands on the terminator."""
    packet = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><dc:creator>'
              + secret("XMP", variant) + b"</dc:creator></x:xmpmeta>")
    trailer = b"\x01" + bytes(range(255, -1, -1)) + b"\0"
    return app_ext(b"XMP DataXMP", packet + trailer, raw=True)


def _header_end(gif: bytes) -> int:
    flags = gif[10]
    return 13 + (3 * (2 << (flags & 7)) if flags & 0x80 else 0)


def build(variant: int = 0, *, frames: int = 1, icc: str = "custom",
          trailing: bool = True, seed: int = 0) -> bytes:
    buf = io.BytesIO()
    imgs = [frame(seed, k) for k in range(frames)]
    if frames > 1:
        imgs[0].save(buf, "GIF", save_all=True, append_images=imgs[1:], duration=80,
                     loop=0)
    else:
        imgs[0].save(buf, "GIF")
    gif = buf.getvalue()
    gif = b"GIF89a" + gif[6:]
    at = _header_end(gif)
    blocks = b"\x21\xfe" + sub_blocks(secret("COMMENT", variant))
    blocks += xmp_ext(variant)
    blocks += app_ext(b"SENTINEL1.0", secret("APP", variant))
    if icc != "none":
        blocks += app_ext(b"ICCRGBG1012",
                          STANDARD_ICC if icc == "standard" else CUSTOM_ICC)
    text = b"\x21\x01\x0c" + bytes(12) + sub_blocks(b"PLAIN-TEXT-CONTENT")
    gif = gif[:at] + blocks + text + gif[at:]
    return gif + (secret("TRAILER", variant) if trailing else b"")


def planted(variant: int = 0) -> list[bytes]:
    return [secret(n, variant) for n in ("COMMENT", "XMP", "APP", "TRAILER")]
