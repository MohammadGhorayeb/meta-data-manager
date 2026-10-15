"""EMF and WMF built record by record, with each locus the survey of real Word
metafiles found (`docs/word_plan.md` §6) planted as a SENTINEL.

`emf(variant)` is an EMF+ dual file -- the shape Office writes: an EMF+ header, EMF+
objects (a picture, possibly split across continuation records), `GetDC`, then the
GDI drawing (a brush, a rectangle, a bitmap) -- with the planted loci around it.
`wmf(variant)` is a placeable WMF with a private comment and, when asked, the EMF
it was made from embedded in 'WMFC' chunks, as Windows writes it.

Variants differ only in planted values; `seed` changes the drawing (for the
fingerprint guard's diverse set). Nothing here is read from a real file.
"""
from __future__ import annotations

import os
import struct

from tests.scrub import zip_corpus as zc

# Every distinct metafile inside the 784 real Word documents of the W1b census
# (`docs/word_plan.md` §5-§6), extracted to the research folder -- never the repo.
SURVEY_DIR = os.path.expanduser("~/metadata-research/step2/emf")

secret = zc.secret

EMF_LOCI = ("description", "private_comment", "group", "wmf_copy", "string_comment",
            "plus_comment", "plus_image", "plus_image_continued", "trailing")
WMF_LOCI = ("comment", "embedded_emf", "trailing")


def _pad4(b: bytes) -> bytes:
    return b + b"\0" * (-len(b) % 4)


def _rec(rtype: int, body: bytes) -> bytes:
    body = _pad4(body)
    return struct.pack("<II", rtype, 8 + len(body)) + body


def _comment(data: bytes) -> bytes:
    return _rec(70, struct.pack("<I", len(data)) + data)


def _plus(ptype: int, flags: int, data: bytes) -> bytes:
    return struct.pack("<HHII", ptype, flags, 12 + len(_pad4(data)), len(data)) + _pad4(data)


def _utf16(s: str) -> bytes:
    return s.encode("utf-16-le")


def _dib(seed: int) -> bytes:
    """A 4x4 24-bit bitmap: header, then rows padded to 4 bytes."""
    head = struct.pack("<IiiHHIIiiII", 40, 4, 4, 1, 24, 0, 48, 2835, 2835, 0, 0)
    rows = b"".join(bytes(v for x in range(4)
                          for v in ((x * 60 + seed * 31) % 256, y * 50, 200 - x * 40))
                    for y in range(4))                     # 12 bytes: already aligned
    return head + rows


def _picture(seed: int) -> bytes:
    """An EmfPlusImage holding a compressed bitmap: a whole JPEG file."""
    jpeg = zc.photo(0, seed=seed)
    return struct.pack("<IIiiiII", 0xDBC01002, 1, 32, 24, 0, 0, 1) + jpeg


def emf(variant: int = 0, loci=EMF_LOCI, seed: int = 0, chunk: int = 512) -> bytes:
    recs = []
    if "group" in loci:
        desc = _utf16(secret("EMF-GROUP", variant) + "\0")
        recs.append(_comment(b"GDIC" + struct.pack("<I", 2) + struct.pack("<iiii", 0, 0, 99, 99)
                             + struct.pack("<I", len(desc) // 2) + desc))
    plus = [_plus(0x4001, 1, struct.pack("<IIII", 0xDBC01002, 1, 96, 96))]
    if "plus_comment" in loci:
        plus.append(_plus(0x4003, 0, secret("EMF-PLUS-COMMENT", variant).encode()))
    if "plus_image" in loci:
        # The picture's own planted EXIF differs by variant, its pixels do not.
        obj = _picture(seed)
        obj = obj[:28] + zc.photo(variant, seed=seed)
        plus.append(_plus(0x4008, (5 << 8) | 0, obj))
    recs.append(_comment(b"EMF+" + b"".join(plus)))
    if "plus_image_continued" in loci:
        obj = _picture(seed)
        obj = obj[:28] + zc.photo(variant, seed=seed + 1)
        pieces = [obj[k:k + chunk] for k in range(0, len(obj), chunk)]
        for n, piece in enumerate(pieces):
            flags = (5 << 8) | 1 | (0x8000 if n < len(pieces) - 1 else 0)
            recs.append(_comment(b"EMF+" + _plus(0x4008, flags,
                                                 struct.pack("<I", len(obj)) + piece)))
    # Draw the picture(s): DrawImage, source in pixels, destination rectangle.
    draws = [(0, 50.0)] * ("plus_image" in loci) + [(1, 10.0)] * ("plus_image_continued" in loci)
    recs.append(_comment(b"EMF+" + b"".join(
        _plus(0x401A, oid, struct.pack("<Ii4f4f", 0, 2, 0, 0, 32 + 2 * (seed + oid), 24,
                                       x, 60.0, 32.0, 24.0))
        for oid, x in draws) + _plus(0x4004, 0, b"")))                  # then GetDC
    if "private_comment" in loci:
        recs.append(_comment(_utf16(f"C:\\Users\\{secret('USER', variant)}\\Documents"
                                    "\\chart.xlsx\0")))
    if "string_comment" in loci:
        recs.append(_comment(b"GDIC" + struct.pack("<I", 0x40)
                             + _utf16(secret("EMF-STRING", variant))))
    if "wmf_copy" in loci:
        copy = wmf(variant, loci=("comment",), seed=seed)
        recs.append(_comment(b"GDIC" + struct.pack("<IIHII", 0x80000001, 0x300, 0, 0,
                                                   len(copy)) + copy))
    # The drawing: a solid brush, a rectangle, a bitmap.
    recs.append(_rec(39, struct.pack("<IIII", 1, 0, 0x00336699 + seed, 0)))
    recs.append(_rec(37, struct.pack("<I", 1)))
    recs.append(_rec(43, struct.pack("<iiii", 10, 10, 45 - seed % 7, 45)))
    dib = _dib(seed)
    recs.append(_rec(81, struct.pack("<iiii", 20, 20, 60, 60)
                     + struct.pack("<iiiiiiIIIIIIii", 20, 20, 0, 0, 4, 4, 80, 40, 120,
                                   48, 0, 0x00CC0020, 40, 40) + dib))
    if "group" in loci:
        recs.append(_comment(b"GDIC" + struct.pack("<I", 3)))
    recs.append(_rec(14, struct.pack("<III", 0, 16, 20)))
    desc = (_utf16(f"Microsoft Office\0{secret('EMF', variant)}.emf\0\0")
            if "description" in loci else b"")
    body = b"".join(recs)
    size = 108 + len(_pad4(desc))
    header = (struct.pack("<II", 1, size) + struct.pack("<iiii", 0, 0, 99, 99)
              + struct.pack("<iiii", 0, 0, 2646, 2646)
              + struct.pack("<IIIIHH", 0x464D4520, 0x10000, size + len(body),
                            1 + len(recs), 2, 0)
              + struct.pack("<III", len(desc) // 2, 108 if desc else 0, 0)
              + struct.pack("<iiii", 1920, 1080, 508, 286)
              + struct.pack("<IIIii", 0, 0, 0, 508000, 285750) + _pad4(desc))
    out = header + body
    if "trailing" in loci:
        out += secret("EMF-TRAILING", variant).encode()
    return out


def _wrec(fn: int, params: bytes) -> bytes:
    params += b"\0" * (len(params) % 2)
    return struct.pack("<IH", (6 + len(params)) // 2, fn) + params


def _escape(data: bytes) -> bytes:
    return _wrec(0x0626, struct.pack("<HH", 0x000F, len(data)) + data)


def wmf(variant: int = 0, loci=WMF_LOCI, seed: int = 0, placeable: bool = True,
        chunk: int = 700) -> bytes:
    recs = [_wrec(0x020B, struct.pack("<hh", 0, 0)),
            _wrec(0x020C, struct.pack("<hh", 100, 100))]
    if "comment" in loci:
        recs.append(_escape(b"MGXI" + secret("WMF-COMMENT", variant).encode()))
    wmfc_at = None
    if "embedded_emf" in loci:
        inner = emf(variant, loci=("description", "private_comment"), seed=seed)
        pieces = [inner[k:k + chunk] for k in range(0, len(inner), chunk)]
        remaining = len(inner)
        wmfc_at = len(recs)
        for piece in pieces:
            remaining -= len(piece)
            recs.append(_escape(b"WMFC" + struct.pack(
                "<IIHIIIII", 1, 0x00010000, 0, 0, len(pieces), len(piece), remaining,
                len(inner)) + piece))
    recs += [_wrec(0x02FC, struct.pack("<HIH", 0, 0x00336699 + seed, 0)),
             _wrec(0x012D, struct.pack("<H", 0)),
             _wrec(0x041B, struct.pack("<hhhh", 90, 90 - seed % 7, 10, 10)),
             _wrec(0x0000, b"")]
    body = b"".join(recs)
    header = bytearray(struct.pack("<HHHIHIH", 1, 9, 0x0300, 9 + len(body) // 2, 1,
                                   max(len(r) // 2 for r in recs), 0))
    out = bytearray(header + body)
    if wmfc_at is not None:
        # Windows' rule (measured on Word's files): the first chunk's checksum
        # makes the metafile's 16-bit words sum to zero.
        at = 18 + sum(len(r) for r in recs[:wmfc_at]) + 22
        total = sum(w for (w,) in struct.iter_unpack("<H", bytes(out))) & 0xFFFF
        struct.pack_into("<H", out, at, -total & 0xFFFF)
    if placeable:
        head = bytearray(struct.pack("<IHhhhhHIH", 0x9AC6CDD7, 0, 0, 0, 100, 100, 96, 0, 0))
        check = 0
        for (w,) in struct.iter_unpack("<H", bytes(head[:20])):
            check ^= w
        struct.pack_into("<H", head, 20, check)
        out = head + out
    if "trailing" in loci:
        out += secret("WMF-TRAILING", variant).encode()
    return bytes(out)


def planted(variant: int, locus: str, kind: str = "emf") -> list[bytes]:
    """The SENTINEL bytes a locus plants, as they appear in the file. `trailing` is
    a locus of both formats: `kind` says which."""
    if locus == "trailing":
        return [secret(f"{kind.upper()}-TRAILING", variant).encode()]
    names = {"description": [_utf16(secret("EMF", variant))],
             "private_comment": [_utf16(secret("USER", variant))],
             "group": [_utf16(secret("EMF-GROUP", variant))],
             "wmf_copy": [secret("WMF-COMMENT", variant).encode()],
             "string_comment": [_utf16(secret("EMF-STRING", variant))],
             "plus_comment": [secret("EMF-PLUS-COMMENT", variant).encode()],
             "plus_image": [secret("PHOTO-ARTIST", variant).encode()],
             "plus_image_continued": [],      # split across chunks: checked decoded
             "comment": [secret("WMF-COMMENT", variant).encode()],
             "embedded_emf": [_utf16(secret("EMF", variant)),
                              _utf16(secret("USER", variant))]}
    return names[locus]


def survey_samples() -> list[str]:
    """The survey's real metafiles, or [] (not on this machine, or ignored)."""
    if os.environ.get("SCRUB_IGNORE_REAL_SAMPLES") or not os.path.isdir(SURVEY_DIR):
        return []
    return sorted(os.path.join(SURVEY_DIR, f) for f in os.listdir(SURVEY_DIR)
                  if f.endswith((".emf", ".wmf")))
