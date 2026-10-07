"""Plain-TIFF corpus for CI: TIFFs written byte by byte with every locus planted.

What the Phase 6 survey measured (`docs/p6_tail_plan.md` §1): the IFD0 strings
(including `DocumentName` and `PageName`, which tools fill with the file's own
path, and Windows Explorer's UTF-16 XP fields), an ExifIFD with dates, serials, a
lens and a maker note, a GPS IFD, XMP, IPTC and Photoshop blocks, an ICC profile
-- one published colour space that must survive byte for byte, one personal
calibration that must be sanitized -- a second page with its own name, and an EXIF
thumbnail JPEG carrying its own EXIF. Every planted value derives from `variant`
at a fixed length, so two variants are the same picture with different metadata.

Pixels are uncompressed RGB strips Pillow decodes: the independent check that the
picture is unchanged. Imports nothing from `src`.
"""
from __future__ import annotations

import io
import struct

from PIL import Image

from .raw_corpus import Ref, Tiff, ascii_

W, H = 24, 16


def secret(name: str, variant: int = 0) -> bytes:
    return f"SENTINEL-{name}-V{variant:02d}".encode()


def document_path(variant: int = 0) -> bytes:
    return f"/Users/sentinel{variant:02d}/Documents/secret_scan.tif".encode()


def pixels(seed: int = 0, page: int = 0) -> bytes:
    return bytes(((x * 9 + y * 5 + seed * 31 + page * 77) & 0xFF)
                 for y in range(H) for x in range(W) for _ in range(3))


def icc_profile(description: bytes, creator: bytes = b"SNTL",
                date: tuple = (2001, 2, 3, 4, 5, 6)) -> bytes:
    """A minimal ICC v2 display profile: a header whose provenance fields are set
    and a single `desc` tag. Enough for a reader to identify it; no colour data a
    test depends on."""
    desc = (b"desc" + bytes(4) + struct.pack(">I", len(description) + 1)
            + description + b"\0" + bytes(4 + 4 + 2 + 1 + 67))
    desc += bytes(-len(desc) % 4)
    tags = struct.pack(">I", 1) + struct.pack(">4sII", b"desc", 128 + 4 + 12, len(desc))
    body = tags + desc
    size = 128 + len(body)
    # ICC.1 header: size, CMM, version, class, colour space, PCS, date (6 x u16),
    # 'acsp', platform, flags, manufacturer, model, attributes, intent (68 bytes),
    # illuminant (12), creator (4), profile ID (16), reserved (28) = 128.
    header = struct.pack(">I4sI4s4s4s6H4s4sI4s4sQI", size, creator, 0x02100000,
                         b"mntr", b"RGB ", b"XYZ ", *date, b"acsp", b"APPL", 0,
                         creator, b"MODL", 0, 0)
    header += struct.pack(">3i", 63190, 65536, 54061) + creator + bytes(16)
    header = header.ljust(128, b"\0")
    return header + body


STANDARD_ICC = icc_profile(b"sRGB IEC61966-2.1", creator=b"HP  ")
CUSTOM_ICC = icc_profile(b"SENTINEL calibrated display", creator=b"SNTL")


def thumbnail(variant: int = 0) -> bytes:
    """A real JPEG with its own EXIF, as an EXIF thumbnail carries."""
    img = Image.new("RGB", (8, 6), (90, 160, 30))
    exif = Image.Exif()
    exif[0x013B] = secret("THUMB", variant).decode()
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85, exif=exif.tobytes())
    return buf.getvalue()


def _xp(text: bytes) -> tuple[int, bytes]:
    return 1, text.decode().encode("utf-16-le") + b"\0\0"


def build(variant: int = 0, *, order: str = "<", pages: int = 2,
          icc: str = "custom", thumb: bool = True, seed: int = 0) -> bytes:
    v = variant
    tiff = Tiff(order)
    image = [(0x0100, 3, W), (0x0101, 3, H), (0x0102, 3, [8, 8, 8]), (0x0103, 3, 1),
             (0x0106, 3, 2), (0x0115, 3, 3), (0x0116, 3, H), (0x011C, 3, 1)]
    meta0 = [
        (0x010D, *ascii_(document_path(v))), (0x010E, *ascii_(secret("DESC", v))),
        (0x010F, *ascii_(secret("MAKE", v))), (0x0110, *ascii_(secret("MODEL", v))),
        (0x011D, *ascii_(secret("PAGE", v))), (0x0131, *ascii_(secret("SOFT", v))),
        (0x0132, *ascii_(f"2001:02:03 04:05:{v:02d}".encode())),
        (0x013B, *ascii_(secret("ARTIST", v))), (0x013C, *ascii_(secret("HOST", v))),
        (0x8298, *ascii_(secret("COPY", v))), (0x9C9D, *_xp(secret("XPAUTH", v))),
        (0x02BC, 1, b"<x:xmpmeta>" + secret("XMP", v) + b"</x:xmpmeta>"),
        (0x83BB, 7, b"\x1c\x02\x50\x00\x12" + secret("IPTC", v).ljust(18, b"_")),
        (0x8649, 1, b"8BIM\x04\x04\0\0\0\0\0\x10" + secret("PSHOP", v)[:16]),
        (0x8769, 4, Ref("exif")), (0x8825, 4, Ref("gps")),
    ]
    if icc != "none":
        meta0.append((0x8773, 7, STANDARD_ICC if icc == "standard" else CUSTOM_ICC))
    pages_list = [f"page{p}" for p in range(pages)]
    tiff.ifd("ifd0", image + [(0x0111, 4, Ref("px0")), (0x0117, 4, W * H * 3)] + meta0,
             next_ifd=pages_list[1] if pages > 1 else ("ifd1" if thumb else None))
    tiff.ifd("exif", [
        (0x9003, *ascii_(f"2001:02:03 04:05:{v:02d}".encode())),
        (0x9011, *ascii_(f"+0{v % 10}:00".encode())),
        (0x9286, 7, b"ASCII\0\0\0" + secret("COMMENT", v)),
        (0x927C, 7, b"SENTINEL-MAKERNOTE" + secret("MN", v)),
        (0xA420, *ascii_(secret("UNIQUE", v))), (0xA430, *ascii_(secret("OWNER", v))),
        (0xA431, *ascii_(secret("SERIAL", v))), (0xA434, *ascii_(secret("LENS", v))),
    ])
    tiff.ifd("gps", [(0x0000, 1, b"\x02\x03\x00\x00"), (0x0001, 2, b"N\0"),
                     (0x0002, 5, [(40, 1), (42, 1), (4600 + v, 100)]),
                     (0x0003, 2, b"W\0"), (0x0004, 5, [(74, 1), (0, 1), (2160, 100)])])
    for p in range(1, pages):
        nxt = pages_list[p + 1] if p + 1 < pages else ("ifd1" if thumb else None)
        tiff.ifd(pages_list[p], image + [(0x0111, 4, Ref(f"px{p}")),
                                         (0x0117, 4, W * H * 3),
                                         (0x011D, *ascii_(secret(f"PAGE{p}", v)))],
                 next_ifd=nxt)
    if thumb:
        jpg = thumbnail(v)
        tiff.ifd("ifd1", [(0x0103, 3, 6), (0x0201, 4, Ref("thumb")),
                          (0x0202, 4, len(jpg))])
        tiff.blob("thumb", jpg)
    for p in range(pages):
        tiff.blob(f"px{p}", pixels(seed, p))
    return tiff.build()


SECRETS = ("DESC", "MAKE", "MODEL", "PAGE", "SOFT", "ARTIST", "HOST", "COPY", "XMP",
           "IPTC", "COMMENT", "MN", "UNIQUE", "OWNER", "SERIAL", "LENS", "PAGE1",
           "THUMB")


def planted(variant: int = 0) -> list[bytes]:
    return ([secret(s, variant) for s in SECRETS] + [document_path(variant),
            secret("XPAUTH", variant).decode().encode("utf-16-le")])
