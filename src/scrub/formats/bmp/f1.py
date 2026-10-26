"""BMP at F1: the bitmap rebuilt from the parts that draw the picture.

`docs/bmp_plan.md`. A BMP is a 14-byte file header, an info header (12, 40, 52, 56,
108 or 124 bytes), colour masks, a palette, the pixel rows and -- in a V5 header --
a colour profile, EMBEDDED (an ICC profile) or LINKED (a file name: a path on the
machine that wrote it). Only the file header's pixel offset and V5's profile offset
point anywhere, so F1 rebuilds the file from its parts, in the order the format
defines:

- kept byte for byte: the info header's drawing fields (size, depth, compression,
  masks, colour space, calibration, rendering intent), the masks, the palette's
  colours, every row's pixels -- or an RLE stream up to its end-of-bitmap code;
- kept, sanitized: an embedded ICC profile, by TIFF's rule (`icc.is_standard`): a
  published colour space byte for byte, anything else with its provenance zeroed;
- replaced: a linked profile -> the sRGB tag, the path removed (no reader but the
  machine that wrote it could open the file it names);
- zeroed: the file header's reserved fields, the resolution, the important-colours
  count, the palette's reserved byte, row padding and the spare bits of a row's
  last byte, an RLE literal run's pad byte, V5's reserved field, and the
  colour-space fields a picture that is not calibrated ignores;
- dropped: a palette in a picture of more than 8 bits (no decoder draws with it),
  any gap before the pixels, bytes after them, profile fields with no profile;
- recomputed: the file size, the pixel offset, the image size, the profile offset,
  the colour count (0 for a full palette).

Refused by name, never guessed at: OS/2 2.x headers, JPEG/PNG/CMYK/alpha-bitfield
compression, depths other than 1/4/8/16/24/32, truncated pixel data (except a header
with no pixels at all -- Office ships one as a template), RLE that draws outside the
picture or has no end. Every scrub checks that the output draws the same pixels.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from ...errors import ParseError
from ...standards import icc

INFO_SIZES = (40, 52, 56, 108, 124)
OS2_V2_SIZES = (16, 64)
RGB, RLE8, RLE4, BITFIELDS = 0, 1, 2, 3
_REFUSED_COMPRESSION = {4: "JPEG", 5: "PNG", 6: "alpha bitfields (Windows CE)",
                        11: "CMYK", 12: "CMYK RLE8", 13: "CMYK RLE4"}
CS_CALIBRATED, CS_SRGB, CS_WINDOWS = b"\0\0\0\0", b"BGRs", b" niW"
CS_LINKED, CS_EMBEDDED = b"KNIL", b"DEBM"            # 'LINK', 'MBED' little-endian
_CS_NAMES = {CS_CALIBRATED: "calibrated RGB", CS_SRGB: "sRGB",
             CS_WINDOWS: "Windows default", CS_LINKED: "linked profile",
             CS_EMBEDDED: "embedded profile"}

# Offsets inside the info header.
_SIZE_IMAGE, _XPPM, _CLR_USED, _CLR_IMPORTANT = 20, 24, 32, 36
_CS_TYPE, _CS_FIELDS = 56, (60, 108)               # endpoints (36) + gamma (12)
_PROFILE, _V5_RESERVED = 112, 120


def is_bmp(data) -> bool:
    """`BM` and a header size this module knows; a text file that happens to begin
    `BM` has no such size where the header's would be."""
    return (len(data) >= 18 and data[:2] == b"BM"
            and struct.unpack_from("<I", data, 14)[0] in
            (12,) + INFO_SIZES + OS2_V2_SIZES)


@dataclass
class Bitmap:
    hs: int                       # info header size
    width: int
    height: int                   # always positive; `top_down` says the order
    top_down: bool
    bpp: int
    comp: int
    colors: int                   # palette entries a decoder reads (bpp <= 8)
    entry: int                    # 3 (core header) or 4 bytes per palette entry
    off: int                      # where the pixels start in the input
    masks_len: int                # masks after a 40-byte header (BITFIELDS)
    extra_palette: int            # palette entries in a picture above 8 bits
    pixels: tuple[int, int]       # (start, end) of the pixel data in the input
    rle_end: int                  # end of an RLE stream (its end-of-bitmap code)
    template: bool                # a header with no pixels at all
    cs: bytes
    profile: tuple[int, int] | None   # (start, end) in the input


def _stride(bpp: int, width: int) -> int:
    return ((bpp * width + 31) // 32) * 4


def _parse(data) -> Bitmap:
    if not is_bmp(data):
        raise ParseError("not a BMP file")
    n = len(data)
    off = struct.unpack_from("<I", data, 10)[0]
    hs = struct.unpack_from("<I", data, 14)[0]
    if hs in OS2_V2_SIZES:
        raise ParseError("BMP: an OS/2 2.x bitmap header (no producer of one could be "
                         "measured); refused")
    if 14 + hs > n:
        raise ParseError("BMP: the info header runs past the end of the file")
    if hs == 12:
        width, height, planes, bpp = struct.unpack_from("<HHHH", data, 18)
        comp, top_down, entry, clr_used = RGB, False, 3, 0
        if bpp not in (1, 4, 8, 24):
            raise ParseError(f"BMP: a {bpp}-bit core-header bitmap; refused")
    else:
        width, height, planes, bpp, comp = struct.unpack_from("<iiHHI", data, 18)
        clr_used = struct.unpack_from("<I", data, 14 + _CLR_USED)[0]
        top_down, height, entry = height < 0, abs(height), 4
    if planes != 1:
        raise ParseError(f"BMP: {planes} planes (always 1 in a real bitmap); refused")
    if width <= 0 or height <= 0:
        raise ParseError("BMP: the picture has no width or no height")
    if comp in _REFUSED_COMPRESSION:
        raise ParseError(f"BMP: {_REFUSED_COMPRESSION[comp]} compression is not "
                         "handled; refused rather than passed through")
    allowed = {RGB: (1, 4, 8, 16, 24, 32), RLE8: (8,), RLE4: (4,), BITFIELDS: (16, 32)}
    if comp not in allowed:
        raise ParseError(f"BMP: unknown compression {comp}; refused")
    if bpp not in allowed[comp]:
        raise ParseError(f"BMP: {bpp} bits per pixel with compression {comp}; "
                         "refused")
    if comp in (RLE8, RLE4) and top_down:
        raise ParseError("BMP: a top-down RLE bitmap (not allowed by the format)")

    masks_len = 12 if comp == BITFIELDS and hs == 40 else 0
    if bpp <= 8:
        colors = clr_used or 1 << bpp
        if colors > 1 << bpp:
            raise ParseError(f"BMP: {colors} palette entries for a {bpp}-bit picture")
        extra = 0
    else:
        colors, extra = 0, clr_used
    if off < 14 + hs + masks_len:
        raise ParseError("BMP: the pixel offset points inside the headers")
    pal_end = 14 + hs + masks_len + (colors + extra) * entry
    if pal_end > off:
        raise ParseError("BMP: the palette runs into the pixel data")

    cs, profile = CS_CALIBRATED, None
    if hs >= 108:
        cs = bytes(data[14 + _CS_TYPE:14 + _CS_TYPE + 4])
        if cs not in _CS_NAMES:
            raise ParseError(f"BMP: unknown colour-space type {cs[::-1]!r}; refused")
        if cs in (CS_LINKED, CS_EMBEDDED):
            if hs < 124:
                raise ParseError("BMP: a V4 header naming a profile it has no room "
                                 "for; refused")
            at, size = struct.unpack_from("<II", data, 14 + _PROFILE)
            start, end = 14 + at, 14 + at + size
            if size == 0 or end > n:
                raise ParseError("BMP: the colour profile runs past the end of the "
                                 "file")
            if start < pal_end:
                raise ParseError("BMP: the colour profile overlaps the headers")
            profile = (start, end)
            if cs == CS_EMBEDDED and not icc.looks_like_profile(
                    bytes(data[start:end])):
                raise ParseError("BMP: the embedded colour profile is not an ICC "
                                 "profile; refused")

    template = False
    if comp in (RLE8, RLE4):
        rle_end = _walk_rle(data, off, width, height, comp == RLE4)
        size_image = struct.unpack_from("<I", data, 14 + _SIZE_IMAGE)[0]
        end = max(rle_end, min(off + size_image, n))
    else:
        rle_end = 0
        end = off + _stride(bpp, width) * height
        if end > 0xFFFFFFFF:
            raise ParseError("BMP: the declared picture is larger than a BMP can be")
        if end > n:
            if n != off or profile is not None:
                raise ParseError("BMP: the pixel data runs past the end of the file "
                                 "(truncated); refused")
            template = True                       # the header, and no pixels at all
    if profile is not None and profile[0] < end and profile[1] > off:
        raise ParseError("BMP: the colour profile overlaps the pixel data")
    b = Bitmap(hs, width, height, top_down, bpp, comp, colors, entry, off,
               masks_len, extra, (off, end), rle_end, template, cs, profile)
    if bpp <= 8 and colors < 1 << bpp and not template and \
            _highest_index(b, data) >= colors:
        # Decoders disagree about a colour the palette does not have: some read past
        # it, into whatever follows -- which F1 drops. Refused rather than guessed.
        raise ParseError("BMP: the pixels use colours the palette does not have")
    return b


def _highest_index(b: Bitmap, data) -> int:
    if b.comp in (RLE8, RLE4):
        seen: set[int] = set()
        _walk_rle(data, b.off, b.width, b.height, b.comp == RLE4, seen=seen)
    else:
        seen = set(_pixels(b, data))           # padding and spare bits are zeros
    if b.bpp == 4:
        seen = {v >> 4 for v in seen} | {v & 15 for v in seen}
    elif b.bpp == 1:
        seen = {1} if seen - {0} else {0}
    return max(seen, default=0)


def _walk_rle(data, pos: int, width: int, height: int, rle4: bool,
              clean: bytearray | None = None, seen: set | None = None) -> int:
    """Walk an RLE stream from `pos` to the end of its end-of-bitmap code, refusing
    anything that would draw outside the picture. With `clean` (a copy of the
    stream), zero the pad byte that keeps a literal run on a 16-bit boundary -- no
    decoder reads it. An RLE4 literal of ODD length is left exactly as it is: Pillow
    reads such a run one byte short and then takes its last byte and pad as the next
    code, so changing them would change what Pillow draws (`docs/bmp_plan.md` §1).
    With `seen`, collect the palette indices it draws with (whole bytes; RLE4 holds
    two)."""
    start, n, x, y = pos, len(data), 0, 0
    while True:
        if pos + 2 > n:
            raise ParseError("BMP: the RLE data has no end-of-bitmap code")
        count, value = data[pos], data[pos + 1]
        pos += 2
        if count:                                         # an encoded run
            x += count
            if x > width or y >= height:
                raise ParseError("BMP: an RLE run draws outside the picture")
            if seen is not None:
                seen.add(value & 0xF0 if rle4 and count == 1 else value)
            continue
        if value == 0:                                    # end of line
            x, y = 0, y + 1
        elif value == 1:                                  # end of bitmap
            return pos
        elif value == 2:                                  # delta
            if pos + 2 > n:
                raise ParseError("BMP: an RLE delta runs past the end of the file")
            x, y = x + data[pos], y + data[pos + 1]
            pos += 2
            if x > width or y > height:
                raise ParseError("BMP: an RLE delta moves outside the picture")
        else:                                             # a literal run
            length = (value + 1) // 2 if rle4 else value
            padded = length + (length & 1)
            if pos + padded > n:
                raise ParseError("BMP: an RLE literal runs past the end of the file")
            x += value
            if x > width or y >= height:
                raise ParseError("BMP: an RLE literal draws outside the picture")
            if clean is not None and length & 1 and not (rle4 and value & 1):
                clean[pos + length - start] = 0
            if seen is not None:
                seen.update(data[pos:pos + length - (1 if rle4 and value & 1 else 0)])
                if rle4 and value & 1:
                    seen.add(data[pos + length - 1] & 0xF0)
            pos += padded


def _pixels(b: Bitmap, data) -> bytes:
    """The pixel data as F1 writes it: rows with their padding and spare bits zeroed,
    or an RLE stream with its pad bytes zeroed and nothing after its end."""
    if b.template:
        return b""
    start, end = b.pixels
    if b.comp in (RLE8, RLE4):
        clean = bytearray(data[start:b.rle_end])
        _walk_rle(data, start, b.width, b.height, b.comp == RLE4, clean)
        return bytes(clean)
    stride = _stride(b.bpp, b.width)
    used, spare = divmod(b.bpp * b.width, 8)
    out = bytearray(data[start:end])
    for row in range(b.height):
        at = row * stride
        if spare:
            out[at + used] &= (0xFF << (8 - spare)) & 0xFF
            used_bytes = used + 1
        else:
            used_bytes = used
        out[at + used_bytes:at + stride] = bytes(stride - used_bytes)
    return bytes(out)


def _profile(b: Bitmap, data) -> bytes:
    if b.cs != CS_EMBEDDED or b.profile is None:
        return b""
    p = bytes(data[b.profile[0]:b.profile[1]])
    return p if icc.is_standard(p) else icc.sanitize(p)


def _rebuild(b: Bitmap, data) -> bytes:
    pixels, profile = _pixels(b, data), _profile(b, data)
    # A template's sizes describe the bitmap it heads once its pixels follow.
    size_image = _stride(b.bpp, b.width) * b.height if b.template else len(pixels)
    info = bytearray(data[14:14 + b.hs])
    if b.hs > 12:
        struct.pack_into("<IiiII", info, _SIZE_IMAGE, size_image, 0, 0,
                         0 if b.colors == 1 << b.bpp or b.bpp > 8 else b.colors, 0)
    if b.hs >= 108:
        cs = CS_SRGB if b.cs == CS_LINKED else b.cs
        info[_CS_TYPE:_CS_TYPE + 4] = cs
        if cs != CS_CALIBRATED:
            info[_CS_FIELDS[0]:_CS_FIELDS[1]] = bytes(_CS_FIELDS[1] - _CS_FIELDS[0])
    masks = bytes(data[14 + b.hs:14 + b.hs + b.masks_len])
    pal_at = 14 + b.hs + b.masks_len
    palette = bytearray(data[pal_at:pal_at + b.colors * b.entry])
    if b.entry == 4:
        palette[3::4] = bytes(b.colors)
    off = 14 + b.hs + b.masks_len + len(palette)
    size = off + size_image
    if b.hs >= 124:
        prof_at = size - 14 if profile else 0
        struct.pack_into("<III", info, _PROFILE, prof_at, len(profile), 0)
    size += len(profile)
    head = b"BM" + struct.pack("<IHHI", size, 0, 0, off)
    return head + bytes(info) + masks + bytes(palette) + pixels + profile


def _content(b: Bitmap, data) -> tuple:
    """What a decoder draws from -- the drawing fields, masks, colour space (and its
    calibration, if calibrated), rendering intent, palette colours and pixels with
    every bit no decoder reads masked -- compared before and after."""
    head = bytes(data[18:26] if b.hs == 12 else data[18:34])  # size, planes, depth,
    masks = bytes(data[14 + b.hs:14 + b.hs + b.masks_len])    # compression
    if b.hs > 40:
        masks += bytes(data[54:14 + min(b.hs, 56)])
    cs = CS_SRGB if b.cs == CS_LINKED else b.cs
    calibration = bytes(data[14 + _CS_FIELDS[0]:14 + _CS_FIELDS[1]]) \
        if b.hs >= 108 and cs == CS_CALIBRATED else b""
    intent = bytes(data[14 + 108:14 + 112]) if b.hs >= 124 else b""
    pal_at = 14 + b.hs + b.masks_len
    colours = tuple(bytes(data[pal_at + i * b.entry:pal_at + i * b.entry + 3])
                    for i in range(b.colors))
    return (head, masks, cs if b.hs >= 108 else b"", calibration, intent, colours,
            _pixels(b, data))


def _check_content(b: Bitmap, data, out: bytes) -> None:
    again = _parse(out)
    if _content(b, data) != _content(again, out) or _profile(b, data) != (
            bytes(out[again.profile[0]:again.profile[1]]) if again.profile else b""):
        raise ParseError("BMP F1 changed what the picture draws; refused")


def _unaccounted(b: Bitmap, data) -> tuple[int, int]:
    """(bytes before the pixels, bytes after them) that no part of the bitmap uses:
    whatever lies outside the headers, the palette, the pixel data and the
    profile."""
    covered = [(0, 14 + b.hs + b.masks_len + (b.colors + b.extra_palette) * b.entry)]
    if not b.template:
        covered.append(b.pixels)
    if b.profile is not None:
        covered.append(b.profile)
    gaps, pos = [], 0
    for start, end in sorted(covered):
        if start > pos:
            gaps.append((pos, start))
        pos = max(pos, end)
    if pos < len(data):
        gaps.append((pos, len(data)))
    before = sum(min(end, b.off) - start for start, end in gaps if start < b.off)
    after = sum(end - max(start, b.off) for start, end in gaps if end > b.off)
    return before, after


def _findings(b: Bitmap, data) -> list[str]:
    """Everything in the file F1 does not keep as it is, in plain words."""
    out = []
    r1, r2 = struct.unpack_from("<HH", data, 6)
    if r1 or r2:
        out.append("the file header's reserved fields")
    if b.hs > 12:
        x, y = struct.unpack_from("<ii", data, 14 + _XPPM)
        if x or y:
            out.append(f"the resolution ({x} x {y} pixels per metre)")
        if struct.unpack_from("<I", data, 14 + _CLR_IMPORTANT)[0]:
            out.append("the important-colours count")
    if b.extra_palette:
        out.append(f"a {b.extra_palette}-colour palette no decoder draws with")
    pal_at = 14 + b.hs + b.masks_len
    if b.entry == 4 and any(data[pal_at + 3:pal_at + 4 * b.colors:4]):
        out.append("the palette's reserved bytes")
    before, after = _unaccounted(b, data)
    if before:
        out.append(f"{before} bytes before the pixels")
    if not b.template:
        start, end = b.pixels
        stream_end = b.rle_end or end
        if _pixels(b, data) != bytes(data[start:stream_end]):
            out.append("bytes in the row padding" if not b.rle_end
                       else "the pad bytes of the RLE literal runs")
        if end > stream_end:
            out.append(f"{end - stream_end} bytes after the end-of-bitmap code")
    if after:
        out.append(f"{after} bytes after the picture")
    if b.hs >= 108 and b.cs != CS_CALIBRATED and any(
            data[14 + _CS_FIELDS[0]:14 + _CS_FIELDS[1]]):
        out.append("colour-space fields the picture does not use")
    if b.hs >= 124:
        if b.cs == CS_LINKED:
            out.append(f"a linked colour profile (a file name: {_link(b, data)})")
        elif b.cs == CS_EMBEDDED:
            p = bytes(data[b.profile[0]:b.profile[1]])
            if not icc.is_standard(p) and icc.sanitize(p) != p:
                out.append("ICC profile provenance (kept for colour)")
        elif any(data[14 + _PROFILE:14 + _PROFILE + 8]):
            out.append("profile fields on a picture with no profile")
        if any(data[14 + _V5_RESERVED:14 + _V5_RESERVED + 4]):
            out.append("the V5 header's reserved field")
    return out


def _link(b: Bitmap, data) -> str:
    return bytes(data[b.profile[0]:b.profile[1]]).split(b"\0")[0].decode(
        "cp1252", "replace")


def _scrub(data: bytes, report: bool = False):
    b = _parse(data)
    removed = _findings(b, data)
    out = _rebuild(b, data)
    _check_content(b, data, out)
    return (out, removed) if report else out


def scrub(data: bytes) -> bytes:
    return _scrub(data)


def scrub_with_report(data: bytes) -> tuple[bytes, list[str]]:
    return _scrub(data, report=True)


def residuals(data: bytes) -> list[str]:
    b = _parse(data)
    out = [f"{f} survives" for f in _findings(b, data)]
    if not out and _rebuild(b, data) != bytes(data):
        out.append("the file is not in the form F1 writes")
    return out


def describe(data: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        b = _parse(data)
    except ParseError:
        return out
    out["Header"] = {12: "core (OS/2 1.x)", 40: "Windows 3", 52: "Windows V2",
                     56: "Windows V3", 108: "Windows V4", 124: "Windows V5"}[b.hs]
    if b.hs > 12:
        x, y = struct.unpack_from("<ii", data, 14 + _XPPM)
        if x or y:
            out["Resolution"] = f"{x} x {y} pixels per metre"
    if b.hs >= 108:
        out["Colour space"] = _CS_NAMES[b.cs]
    if b.cs == CS_LINKED:
        out["Linked profile"] = _link(b, data)
    if b.cs == CS_EMBEDDED:
        p = bytes(data[b.profile[0]:b.profile[1]])
        out["ICC profile"] = icc.description(p) or f"{len(p)} bytes"
    if any(data[6:10]):
        out["Reserved fields"] = "set"
    before, after = _unaccounted(b, data)
    if before:
        out["Before the pixels"] = f"{before} bytes"
    if after:
        out["After the picture"] = f"{after} bytes"
    if b.template:
        out["Pixels"] = "none (a header on its own)"
    return out
