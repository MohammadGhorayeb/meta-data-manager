"""BMP corpus for CI: bitmaps written byte by byte with every locus planted.

What the BMP survey measured (`docs/bmp_plan.md` §1): a BMP is a 14-byte file
header, an info header of one of six sizes, colour masks, a palette and the pixel
rows -- and every place in it a writer can put bytes no reader shows. Planted here:
the file header's two reserved fields, the resolution, the "important colours"
count, the palette's reserved byte, bytes in a gap before the pixels, row padding,
bytes after the pixels, a palette in a picture that has no use for one, the V4/V5
colour-space fields a non-calibrated picture ignores, V5's reserved field, an
embedded ICC profile (published or personal) and a LINKED profile -- a file path on
the machine that wrote it. RLE pictures add the pad byte of each literal run and
bytes after the end-of-bitmap code. Values derive from `variant` at a fixed length,
so two variants are the same picture with different metadata.

Pixels are written by this module and decoded by Pillow: the independent check
that the picture is unchanged. Imports nothing from `src`.
"""
from __future__ import annotations

import struct

from .tiff_corpus import STANDARD_ICC, icc_profile

W, H = 30, 20      # 30 pixels: 24-bit and 8-bit rows end in padding, 1-bit in spare bits

HEADER_SIZES = {"core": 12, "info": 40, "v3": 56, "v4": 108, "v5": 124}
COMPRESSION = {"rgb": 0, "rle8": 1, "rle4": 2, "bitfields": 3}
CS_SRGB, CS_EMBEDDED, CS_LINKED = b"BGRs", b"DEBM", b"KNIL"   # 'sRGB' 'MBED' 'LINK'


def secret(name: str, variant: int = 0) -> bytes:
    return f"SENTINEL-{name}-V{variant:02d}".encode()


def custom_icc(variant: int = 0) -> bytes:
    """A personal display calibration: provenance in the header, a name in `desc`."""
    return icc_profile(b"SENTINEL calibrated display V%02d" % variant, creator=b"SNTL")


def linked_path(variant: int = 0) -> bytes:
    """What a V5 header holds for a LINKED profile: a path, NUL-terminated."""
    return (f"C:\\Users\\sentinel{variant:02d}\\AppData\\Local\\display.icm"
            .encode("cp1252") + b"\0")


def _filler(name: str, variant: int, n: int) -> bytes:
    return (secret(name, variant) * (n // 16 + 1))[:n]


def _index(x: int, y: int, seed: int, colors: int, runs: bool) -> int:
    if runs:                                   # runs and a literal stretch, for RLE
        v = (x // 5) * 3 + y + seed if x < 18 or x > 24 else x * 7 + y * 3 + seed
    else:
        v = x * 7 + y * 13 + seed * 29
    return v % colors


def palette(colors: int, seed: int = 0) -> list[tuple[int, int, int]]:
    return [((i * 37 + seed) & 0xFF, (i * 91 + 40) & 0xFF, (255 - i * 13) & 0xFF)
            for i in range(colors)]


def _rgba(x: int, y: int, seed: int) -> tuple[int, int, int, int]:
    return ((x * 8 + seed) & 0xFF, (y * 12) & 0xFF, ((x ^ y) * 9) & 0xFF,
            255 if (x + y) % 5 else 96)


def _row(bpp: int, y: int, seed: int, colors: int, w: int, runs: bool,
         bitfields: bool) -> bytes:
    """One row's pixel bytes, without padding."""
    if bpp <= 8:
        idx = [_index(x, y, seed, colors, runs) for x in range(w)]
        if bpp == 8:
            return bytes(idx)
        per = 8 // bpp
        out = bytearray((w * bpp + 7) // 8)
        for x, v in enumerate(idx):
            out[x // per] |= v << (8 - bpp * (x % per + 1))
        return bytes(out)
    out = bytearray()
    for x in range(w):
        r, g, b, a = _rgba(x, y, seed)
        if bpp == 16:
            v = ((r >> 3) << 11 | (g >> 2) << 5 | b >> 3) if bitfields else \
                ((r >> 3) << 10 | (g >> 3) << 5 | b >> 3)
            out += struct.pack("<H", v)
        elif bpp == 24:
            out += bytes((b, g, r))
        else:
            out += bytes((b, g, r, a))
    return bytes(out)


def _rle(rows: list[bytes], rle4: bool, pad: int) -> bytes:
    """Rows (bottom row first) as RLE8 or RLE4: encoded runs, literal runs of three
    or more (each padded to a 16-bit boundary with `pad`), an end-of-line code after
    every row and the end-of-bitmap code."""
    out = bytearray()
    for row in rows:
        px = ([v for b in row for v in (b >> 4, b & 15)][:W] if rle4 else list(row))
        x = 0
        while x < len(px):
            n = 1
            while x + n < len(px) and px[x + n] == px[x] and n < 255:
                n += 1
            if n >= 2:
                out += bytes((n, px[x] * 17 if rle4 else px[x]))
                x += n
                continue
            lit = 1                     # literal: until a run of two starts
            while x + lit < len(px) and lit < 255 and not (
                    x + lit + 1 < len(px) and px[x + lit] == px[x + lit + 1]):
                lit += 1
            if rle4 and lit % 2:        # Pillow misreads odd RLE4 literals
                lit -= 1
            if lit < 3:
                out += bytes((1, px[x] * 17 if rle4 else px[x]))
                x += 1
                continue
            chunk = px[x:x + lit]
            if rle4:
                body = bytes((chunk[i] << 4 | (chunk[i + 1] if i + 1 < lit else 0))
                             for i in range(0, lit, 2))
            else:
                body = bytes(chunk)
            out += bytes((0, lit)) + body + (bytes((pad,)) if len(body) % 2 else b"")
            x += lit
        out += b"\0\0"
    out[-2:] = b"\0\1"                  # the last end-of-line becomes end-of-bitmap
    return bytes(out)


def build(variant: int = 0, *, header: str = "v5", bpp: int = 24,
          compression: str = "rgb", icc: str = "custom", colors: int | None = None,
          planted: bool = True, top_down: bool = False, seed: int = 0,
          profile_at: str = "end", w: int = W, h: int = H) -> bytes:
    """A BMP with the given layout. `icc` (V4/V5 only): "none" (an sRGB tag),
    "calibrated" (V4/V5 endpoints and gamma: colour data), "standard"/"custom"
    (an embedded profile, V5) or "linked" (a path, V5)."""
    hs = HEADER_SIZES[header]
    comp = COMPRESSION[compression]
    v = variant if planted else 0
    colors = colors or (1 << bpp if bpp <= 8 else 0)
    runs = compression.startswith("rle")
    rows = [_row(bpp, y, seed, colors or 1, w, runs, compression == "bitfields")
            for y in range(h)]
    stored = rows if top_down else rows[::-1]

    if runs:
        pixels = _rle(stored, compression == "rle4", 0xA0 + v if planted else 0)
        if planted:
            pixels += _filler("RLE-SLACK", v, 16)      # inside sizeImage, after EOB
    else:
        stride = ((bpp * w + 31) // 32) * 4
        pixels = b""
        for row in stored:
            pad = stride - len(row)
            if planted and bpp < 8 and (w * bpp) % 8:  # spare bits in the last byte
                row = row[:-1] + bytes((row[-1] | ((1 << (8 - (w * bpp) % 8)) - 1),))
            pixels += row + (_filler("PAD", v, pad) if planted else bytes(pad))

    if header == "core":
        info = struct.pack("<IHHHH", 12, w, h, 1, bpp)
        pal = b"".join(bytes((b, g, r)) for r, g, b in palette(colors, seed))
    else:
        # A full palette's count is 0 by the format's rule; writers (Pillow) say it.
        used = (colors if planted or colors < 1 << bpp else 0) if bpp <= 8 else 0
        pal = b"".join(bytes((b, g, r, (0x50 + v + i) & 0xFF if planted else 0))
                       for i, (r, g, b) in enumerate(palette(colors, seed)))
        if bpp > 8 and planted:                        # an "optimisation" palette
            used = 4
            pal = _filler("OPTPAL", v, 16)
        ppm = 2835 + v * 100 if planted else 0
        info = struct.pack("<IiiHHIIiiII", hs, w, -h if top_down else h, 1, bpp, comp,
                           len(pixels), ppm, ppm, used, (v + 1) % (used + 1)
                           if planted else 0)
        masks = ((0xF800, 0x7E0, 0x1F, 0) if bpp == 16 else
                 (0xFF0000, 0xFF00, 0xFF, 0xFF000000))
        if hs >= 56:
            info += struct.pack("<4I", *masks)
        if hs >= 108:
            if icc == "calibrated":
                cs = bytes(4)
                ends = struct.pack("<9i", *[(k + 1) * 0x1000000 for k in range(9)])
                gamma = struct.pack("<3I", 0x23333, 0x23333, 0x23333)
            else:
                cs = {"standard": CS_EMBEDDED, "custom": CS_EMBEDDED,
                      "linked": CS_LINKED}.get(icc, CS_SRGB)
                ends = _filler("ENDS", v, 36) if planted else bytes(36)
                gamma = _filler("GAMA", v, 12) if planted else bytes(12)
            info += cs + ends + gamma
        if hs >= 124:
            info += struct.pack("<IIII", 4, 0, 0, 0x5E000000 + v if planted else 0)
    mask_block = (struct.pack("<3I", *masks[:3])
                  if compression == "bitfields" and hs == 40 else b"")
    gap = _filler("GAP", v, 16) if planted else b""

    profile = b""
    if hs >= 124 and icc in ("standard", "custom", "linked"):
        profile = {"standard": STANDARD_ICC, "custom": custom_icc(v),
                   "linked": linked_path(v)}[icc]
    head_len = 14 + len(info) + len(mask_block) + len(pal)
    if profile and profile_at == "gap":
        prof_at = head_len + len(gap)
        off = prof_at + len(profile)
        body = info + mask_block + pal + gap + profile + pixels
    else:
        off = head_len + len(gap)
        prof_at = off + len(pixels)
        body = info + mask_block + pal + gap + pixels + profile
    if profile:
        body = (body[:112] + struct.pack("<II", prof_at - 14, len(profile))
                + body[120:])
    size = 14 + len(body)
    head = b"BM" + struct.pack("<IHHI", size, 0x5300 + v if planted else 0,
                               0x4E00 + v if planted else 0, off)
    return head + body + (_filler("TRAILER", v, 20) if planted else b"")


def planted(variant: int = 0) -> list[bytes]:
    """Values that must not survive, wherever this corpus put them."""
    return [secret(n, variant) for n in ("GAP", "TRAILER")]


def expected_rgba(bpp: int = 24, compression: str = "rgb", seed: int = 0,
                  colors: int | None = None, w: int = W, h: int = H) -> list:
    """The picture as this module meant it, for checking a decoder (8-bit channels:
    not for 16-bit pictures, which a decoder widens from 5 or 6 bits)."""
    colors = colors or (1 << bpp if bpp <= 8 else 0)
    runs = compression.startswith("rle")
    pal = palette(colors, seed) if bpp <= 8 else []
    out = []
    for y in range(h):
        for x in range(w):
            if bpp <= 8:
                out.append(pal[_index(x, y, seed, colors, runs)])
            else:
                out.append(_rgba(x, y, seed)[:3])
    return out
