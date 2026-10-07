"""WebP corpus for CI: Pillow encodes the picture, this module builds the file.

Every locus the survey measured (`docs/p6_tail_plan.md` §1): EXIF (artist, serial,
GPS) and XMP chunks with their VP8X flags set, an ICC profile (published or
personal), an unknown chunk -- readers skip those, so it could hold anything --
both at the top level and inside an animation frame, and bytes after the RIFF's
declared end. Values derive from `variant` at a fixed length. The coded image is
lossless and deterministic, so pixels are an exact check.

Imports nothing from `src`.
"""
from __future__ import annotations

import io
import struct

from PIL import Image

from .tiff_corpus import CUSTOM_ICC, STANDARD_ICC

W, H = 40, 24


def secret(name: str, variant: int = 0) -> bytes:
    return f"SENTINEL-{name}-V{variant:02d}".encode()


def picture(seed: int = 0, frame: int = 0) -> Image.Image:
    img = Image.new("RGBA", (W, H))
    img.putdata([(((x * 6 + seed * 17 + frame * 40) & 0xFF), ((y * 9) & 0xFF),
                  ((x ^ y) * 5) & 0xFF, 255 if (x + y + frame) % 7 else 128)
                 for y in range(H) for x in range(W)])
    return img


def _chunks(data: bytes, start: int, end: int) -> list[tuple[bytes, bytes]]:
    out, pos = [], start
    while pos < end:
        fourcc, size = data[pos:pos + 4], struct.unpack_from("<I", data, pos + 4)[0]
        out.append((fourcc, data[pos + 8:pos + 8 + size]))
        pos += 8 + size + (size & 1)
    return out


def chunk(fourcc: bytes, payload: bytes) -> bytes:
    return fourcc + struct.pack("<I", len(payload)) + payload + b"\0" * (len(payload) & 1)


def exif(variant: int = 0) -> bytes:
    e = Image.Exif()
    e[0x013B] = secret("ARTIST", variant).decode()
    e[0x8298] = secret("COPY", variant).decode()
    e[0x0131] = secret("SOFT", variant).decode()
    return e.tobytes()


def xmp(variant: int = 0) -> bytes:
    return (b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><dc:creator>' + secret("XMP", variant)
            + b"</dc:creator></x:xmpmeta>")


def _encode(images: list[Image.Image]) -> bytes:
    buf = io.BytesIO()
    if len(images) == 1:
        images[0].save(buf, "WEBP", lossless=True, exact=True, method=4)
    else:
        images[0].save(buf, "WEBP", lossless=True, exact=True, method=4, save_all=True,
                       append_images=images[1:], duration=100, loop=0)
    return buf.getvalue()


def build(variant: int = 0, *, frames: int = 1, icc: str = "custom",
          unknown: bool = True, trailing: bool = True, seed: int = 0) -> bytes:
    coded = _encode([picture(seed, f) for f in range(frames)])
    parts = _chunks(coded, 12, len(coded))
    image = [(f, p) for f, p in parts if f != b"VP8X"]
    flags = 0x10 | 0x08 | 0x04 | (0x02 if frames > 1 else 0) | (0x20 if icc != "none"
                                                                   else 0)
    vp8x = bytes([flags]) + bytes(3) + struct.pack("<I", W - 1)[:3] \
        + struct.pack("<I", H - 1)[:3]
    body = chunk(b"VP8X", vp8x)
    if icc != "none":
        body += chunk(b"ICCP", STANDARD_ICC if icc == "standard" else CUSTOM_ICC)
    for fourcc, payload in image:
        if fourcc == b"ANMF" and unknown:
            payload = payload + chunk(b"SNTF", secret("FRAME", variant))
        body += chunk(fourcc, payload)
    body += chunk(b"EXIF", exif(variant)) + chunk(b"XMP ", xmp(variant))
    if unknown:
        body += chunk(b"SNTL", secret("UNKNOWN", variant))
    out = b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + body
    return out + (secret("TRAILER", variant) if trailing else b"")


def planted(variant: int = 0, frames: int = 1) -> list[bytes]:
    names = ["ARTIST", "COPY", "SOFT", "XMP", "UNKNOWN", "TRAILER"]
    if frames > 1:
        names.append("FRAME")
    return [secret(n, variant) for n in names]
