"""EXIF / TIFF-IFD parser + locus enumeration.

Scope for Phase 1 is **verification and locus annotation**, not surgical tag
editing: the JPEG/PNG handlers drop the whole EXIF container (APP1 / eXIf), so
this module's job is to *prove* what was in there and to let the harness plugin
name a byte offset ("this leak is in the GPS IFD"). The surgical path (rewrite an
IFD keeping some tags) is what RAW needs in P4; the API is shaped for both, but
only the read/enumerate half is implemented now.

Structure (TIFF 6.0 / EXIF 2.3):
  header:  byte-order ("II"|"MM") | 0x002A | uint32 offset-to-IFD0   (8 bytes)
  IFD:     uint16 count | count x 12-byte entry | uint32 next-IFD-offset
  entry:   uint16 tag | uint16 type | uint32 count | 4-byte value-or-offset
           value is inline iff type_size*count <= 4, else the 4 bytes are an
           offset (from TIFF start) to the value.

Loci we care about beyond the named IFDs:
  - sub-IFDs via ExifIFD (0x8769) / GPS (0x8825) / Interop (0xA005) pointers,
  - **IFD1** via IFD0's next-IFD pointer — the embedded thumbnail, which carries
    its *own* full tag set including its own GPS IFD (a classic missed locus),
  - the thumbnail image bytes: JPEGInterchangeFormat (0x0201) +
    JPEGInterchangeFormatLength (0x0202), or StripOffsets/StripByteCounts.

All offsets in the returned structures are relative to the TIFF start (the
byte-order marker = offset 0), NOT to the enclosing file.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Optional

from ..errors import ParseError

# EXIF APP1 payloads are prefixed with this before the TIFF header.
EXIF_PREFIX = b"Exif\x00\x00"

# Type -> byte size (TIFF 6.0 table 4 + EXIF additions).
_TYPE_SIZE = {
    1: 1,   # BYTE
    2: 1,   # ASCII
    3: 2,   # SHORT
    4: 4,   # LONG
    5: 8,   # RATIONAL
    6: 1,   # SBYTE
    7: 1,   # UNDEFINED
    8: 2,   # SSHORT
    9: 4,   # SLONG
    10: 8,  # SRATIONAL
    11: 4,  # FLOAT
    12: 8,  # DOUBLE
    13: 4,  # IFD (== LONG pointer)
}

# Tags whose value is a pointer to a nested IFD.
_SUBIFD_TAGS = {
    0x8769: "ExifIFD",
    0x8825: "GPSIFD",
    0xA005: "InteropIFD",
}

# Thumbnail locators (live in IFD1, occasionally elsewhere).
TAG_JPEG_THUMB_OFFSET = 0x0201  # JPEGInterchangeFormat
TAG_JPEG_THUMB_LENGTH = 0x0202  # JPEGInterchangeFormatLength
TAG_STRIP_OFFSETS = 0x0111
TAG_STRIP_BYTECOUNTS = 0x0117

_MAX_ENTRIES = 4096      # sanity bound; real IFDs have < a few hundred
_MAX_IFD_CHAIN = 16      # IFD0->IFD1->... ; guards against loops/abuse


@dataclass
class IfdEntry:
    tag: int
    type: int
    count: int
    raw_value: int              # the 4 value/offset bytes as an int
    inline: bool                # value stored in the entry itself
    data_offset: Optional[int]  # abs offset (from TIFF start) if out-of-line
    data_length: int            # type_size * count


@dataclass
class Ifd:
    name: str                   # "IFD0" | "ExifIFD" | "GPSIFD" | "IFD1" | ...
    offset: int                 # from TIFF start
    entries: list[IfdEntry]
    next_offset: int            # 0 == end of chain


@dataclass
class Thumbnail:
    offset: int                 # from TIFF start
    length: int
    in_ifd: str


@dataclass
class IfdTree:
    byte_order: str             # "<" (II) or ">" (MM)
    tiff_len: int
    ifds: list[Ifd] = field(default_factory=list)
    thumbnails: list[Thumbnail] = field(default_factory=list)
    truncated: bool = False     # non-strict parse stopped early on a bad pointer


@dataclass
class Locus:
    offset: int                 # from TIFF start
    length: int
    name: str


# --------------------------------------------------------------------------- #
def has_exif_prefix(buf: bytes) -> bool:
    return buf[:6] == EXIF_PREFIX


def strip_exif_prefix(buf: bytes) -> bytes:
    """Return the TIFF block from an APP1-Exif payload (drops 'Exif\\0\\0')."""
    return buf[6:] if has_exif_prefix(buf) else buf


def _read_header(tiff: bytes) -> tuple[str, int]:
    if len(tiff) < 8:
        raise ParseError("TIFF block shorter than 8-byte header")
    bo = tiff[:2]
    if bo == b"II":
        order = "<"
    elif bo == b"MM":
        order = ">"
    else:
        raise ParseError(f"bad TIFF byte order {bo!r}")
    (magic,) = struct.unpack(order + "H", tiff[2:4])
    if magic != 42:
        raise ParseError(f"bad TIFF magic {magic} (expected 42)")
    (ifd0,) = struct.unpack(order + "I", tiff[4:8])
    return order, ifd0


def _parse_ifd(tiff: bytes, order: str, offset: int, name: str) -> Ifd:
    n = len(tiff)
    if offset + 2 > n:
        raise ParseError(f"{name}: entry-count runs past end of TIFF")
    (count,) = struct.unpack_from(order + "H", tiff, offset)
    if count > _MAX_ENTRIES:
        raise ParseError(f"{name}: implausible entry count {count}")
    end = offset + 2 + count * 12 + 4
    if end > n:
        raise ParseError(f"{name}: {count} entries run past end of TIFF")

    entries: list[IfdEntry] = []
    for i in range(count):
        eoff = offset + 2 + i * 12
        tag, typ, cnt = struct.unpack_from(order + "HHI", tiff, eoff)
        raw = tiff[eoff + 8: eoff + 12]
        (raw_value,) = struct.unpack(order + "I", raw)
        size = _TYPE_SIZE.get(typ, 0)
        data_len = size * cnt
        inline = data_len <= 4
        data_off = None if inline else raw_value
        if not inline and data_off is not None and data_off + data_len > n:
            # Out-of-line value points past EOF: structurally invalid.
            raise ParseError(
                f"{name}: tag {tag:#06x} value @ {data_off} len {data_len} "
                f"exceeds TIFF length {n}")
        entries.append(IfdEntry(tag, typ, cnt, raw_value, inline, data_off, data_len))

    (next_off,) = struct.unpack_from(order + "I", tiff, offset + 2 + count * 12)
    return Ifd(name=name, offset=offset, entries=entries, next_offset=next_off)


def _thumbnail_from(ifd: Ifd, order: str, tiff_len: int) -> Optional[Thumbnail]:
    tags = {e.tag: e for e in ifd.entries}
    off_e = tags.get(TAG_JPEG_THUMB_OFFSET)
    len_e = tags.get(TAG_JPEG_THUMB_LENGTH)
    if off_e is not None and len_e is not None:
        # Both are LONG count-1, value inline in raw_value.
        off, length = off_e.raw_value, len_e.raw_value
        if off + length <= tiff_len:
            return Thumbnail(offset=off, length=length, in_ifd=ifd.name)
    return None


def parse(tiff: bytes, *, strict: bool = False) -> IfdTree:
    """Walk IFD0 -> sub-IFDs -> IFD1 chain. `strict` raises ParseError on any
    structural violation; non-strict stops the offending branch and sets
    `truncated` (used by annotate() where robustness beats completeness)."""
    order, ifd0_off = _read_header(tiff)
    tree = IfdTree(byte_order=order, tiff_len=len(tiff))
    visited: set[int] = set()

    def walk_subifds(ifd: Ifd) -> None:
        for e in ifd.entries:
            sub = _SUBIFD_TAGS.get(e.tag)
            if sub is None:
                continue
            sub_off = e.raw_value
            if sub_off in visited or sub_off + 2 > len(tiff):
                if strict and sub_off + 2 > len(tiff):
                    raise ParseError(f"{sub} pointer {sub_off} past end")
                tree.truncated = True
                continue
            try:
                child = _parse_ifd(tiff, order, sub_off, sub)
            except ParseError:
                if strict:
                    raise
                tree.truncated = True
                continue
            visited.add(sub_off)
            tree.ifds.append(child)
            walk_subifds(child)  # e.g. IFD1's own GPS IFD

    # Main IFD chain: IFD0, IFD1, IFD2, ...
    chain_off, idx = ifd0_off, 0
    while chain_off and idx < _MAX_IFD_CHAIN:
        if chain_off in visited or chain_off + 2 > len(tiff):
            if strict and chain_off + 2 > len(tiff):
                raise ParseError(f"IFD{idx} pointer {chain_off} past end")
            tree.truncated = True
            break
        try:
            ifd = _parse_ifd(tiff, order, chain_off, f"IFD{idx}")
        except ParseError:
            if strict:
                raise
            tree.truncated = True
            break
        visited.add(chain_off)
        tree.ifds.append(ifd)
        walk_subifds(ifd)
        thumb = _thumbnail_from(ifd, order, len(tiff))
        if thumb is not None:
            tree.thumbnails.append(thumb)
        chain_off = ifd.next_offset
        idx += 1

    return tree


def loci(tree: IfdTree) -> list[Locus]:
    """Every metadata byte-region in the TIFF block, from TIFF start: each IFD's
    directory bytes, each out-of-line value, and each thumbnail image blob. Used
    by verification tests ('did we find the thumbnail and the GPS values?') and
    by the harness plugin's annotate()."""
    out: list[Locus] = []
    for ifd in tree.ifds:
        dir_len = 2 + len(ifd.entries) * 12 + 4
        out.append(Locus(ifd.offset, dir_len, f"{ifd.name}:directory"))
        for e in ifd.entries:
            if not e.inline and e.data_offset is not None and e.data_length:
                out.append(Locus(e.data_offset, e.data_length,
                                 f"{ifd.name}:tag{e.tag:#06x}:value"))
    for t in tree.thumbnails:
        out.append(Locus(t.offset, t.length, f"{t.in_ifd}:thumbnail"))
    out.sort(key=lambda l: l.offset)
    return out


def summarize(tree: IfdTree) -> dict:
    """Compact, JSON-friendly summary for evidence dumps and tests."""
    return {
        "byte_order": "little" if tree.byte_order == "<" else "big",
        "ifds": {ifd.name: [e.tag for e in ifd.entries] for ifd in tree.ifds},
        "thumbnails": [{"in": t.in_ifd, "offset": t.offset, "length": t.length}
                       for t in tree.thumbnails],
        "truncated": tree.truncated,
    }
