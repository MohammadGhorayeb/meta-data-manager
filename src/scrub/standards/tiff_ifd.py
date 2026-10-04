"""EXIF / TIFF-IFD parser + locus enumeration.

Phase 1 used this for **verification and locus annotation**: the JPEG/PNG handlers
drop the whole EXIF container (APP1 / eXIf), so its job was to *prove* what was in
there and to name a byte offset ("this leak is in the GPS IFD").

Phase 4 (M16) adds what camera RAW needs, and RAW is the case where nothing may
move: the sensor data sits at absolute offsets that the file's own tables record,
and the maker note cannot be dropped -- zeroed whole, a Nikon file stops decoding
and a Canon one loses its colour balance (docs/p4_media_plan.md §7.4). So:

  * the camera makers' own magic numbers (Olympus `IIRO`, Panasonic `IIU`), opt-in
    via `magics=` so an EXIF block inside a JPEG is still held to TIFF's 42;
  * `SubIFDs` (0x014A) and IFD-typed pointers, where DNG keeps its raw image;
  * `makernote()`: the maker note located and parsed per maker -- each one writes a
    different header and counts its offsets from a different place;
  * size-preserving WRITES on a mutable buffer: blank a value, remove an entry from
    its directory, zero a region. Each changes only the bytes it names.

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

# TIFF's magic number, and the ones camera makers substitute for it in raw files.
MAGIC_TIFF = 42
RAW_MAGICS = (MAGIC_TIFF,
              0x4F52,           # Olympus ORF, "IIRO"
              0x5352,           # Olympus ORF on older bodies, "IIRS"
              0x0055)           # Panasonic RW2, "IIU\0"

# Tags whose value is a pointer to a nested IFD.
_SUBIFD_TAGS = {
    0x8769: "ExifIFD",
    0x8825: "GPSIFD",
    0xA005: "InteropIFD",
}
TAG_SUBIFDS = 0x014A    # an ARRAY of IFD pointers: DNG's raw image and previews
TAG_MAKERNOTE = 0x927C
TAG_MAKE = 0x010F
TYPE_IFD = 13

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
    data_offset: int | None  # abs offset (from TIFF start) if out-of-line
    data_length: int            # type_size * count
    entry_offset: int = -1      # where this 12-byte entry itself sits


@dataclass
class Ifd:
    name: str                   # "IFD0" | "ExifIFD" | "GPSIFD" | "IFD1" | ...
    offset: int                 # from TIFF start
    entries: list[IfdEntry]
    next_offset: int            # 0 == end of chain
    order: str = ""             # "<" or ">"; a maker note may differ from the file
    base: int = 0               # its out-of-line offsets count from here

    def get(self, tag: int) -> IfdEntry | None:
        return next((e for e in self.entries if e.tag == tag), None)


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
    magic: int = MAGIC_TIFF

    def ifd(self, name: str) -> Ifd | None:
        return next((i for i in self.ifds if i.name == name), None)


@dataclass
class MakerNote:
    """A maker note, located and parsed. Every maker writes a different header and
    counts offsets from a different place, measured on real files (M16):

      canon    plain IFD at the start, offsets from the TIFF start
      sony     plain IFD (or after "SONY DSC "), offsets from the TIFF start
      nikon    "Nikon\0" + version, then its OWN TIFF header at +10, offsets from it
      olympus  "OLYMPUS\0II\3\0", IFD at +12, offsets from the maker note, sub-IFDs
      apple    "Apple iOS\0\0\1MM", big-endian IFD at +14, offsets from the note
    """
    vendor: str
    offset: int                 # of the maker-note value, from TIFF start
    length: int
    ifds: list[Ifd] = field(default_factory=list)   # the note's IFD, then sub-IFDs


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


def _read_header(tiff: bytes, magics: tuple[int, ...] = (MAGIC_TIFF,)
                 ) -> tuple[str, int, int]:
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
    if magic not in magics:
        raise ParseError(f"bad TIFF magic {magic} (expected 42)")
    (ifd0,) = struct.unpack(order + "I", tiff[4:8])
    return order, ifd0, magic


def _parse_ifd(tiff: bytes, order: str, offset: int, name: str,
               base: int = 0) -> Ifd:
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
        data_off = None if inline else base + raw_value
        if not inline and data_off is not None and data_off + data_len > n:
            # Out-of-line value points past EOF: structurally invalid.
            raise ParseError(
                f"{name}: tag {tag:#06x} value @ {data_off} len {data_len} "
                f"exceeds TIFF length {n}")
        entries.append(IfdEntry(tag, typ, cnt, raw_value, inline, data_off, data_len,
                                eoff))

    (next_off,) = struct.unpack_from(order + "I", tiff, offset + 2 + count * 12)
    return Ifd(name=name, offset=offset, entries=entries, next_offset=next_off,
               order=order, base=base)


def value_bytes(tiff: bytes, entry: IfdEntry) -> bytes:
    """The raw bytes of an entry's value, wherever they live."""
    if entry.inline:
        return bytes(tiff[entry.entry_offset + 8:entry.entry_offset + 8
                          + entry.data_length])
    return bytes(tiff[entry.data_offset:entry.data_offset + entry.data_length])


def _pointers(tiff: bytes, entry: IfdEntry, order: str, base: int = 0) -> list[int]:
    """The IFD offsets an entry points at: one, or an array (SubIFDs)."""
    if entry.type not in (4, TYPE_IFD) or entry.count == 0:
        return []
    raw = value_bytes(tiff, entry)
    return [base + v for v in struct.unpack(f"{order}{entry.count}I", raw)]


def _thumbnail_from(ifd: Ifd, order: str, tiff_len: int) -> Thumbnail | None:
    tags = {e.tag: e for e in ifd.entries}
    off_e = tags.get(TAG_JPEG_THUMB_OFFSET)
    len_e = tags.get(TAG_JPEG_THUMB_LENGTH)
    if off_e is not None and len_e is not None:
        # Both are LONG count-1, value inline in raw_value.
        off, length = off_e.raw_value, len_e.raw_value
        if off + length <= tiff_len:
            return Thumbnail(offset=off, length=length, in_ifd=ifd.name)
    return None


def parse(tiff: bytes, *, strict: bool = False,
          magics: tuple[int, ...] = (MAGIC_TIFF,)) -> IfdTree:
    """Walk IFD0 -> sub-IFDs -> IFD1 chain. `strict` raises ParseError on any
    structural violation; non-strict stops the offending branch and sets
    `truncated` (used by annotate() where robustness beats completeness).

    `magics=RAW_MAGICS` accepts the camera makers' substitutes for 42."""
    order, ifd0_off, magic = _read_header(tiff, magics)
    tree = IfdTree(byte_order=order, tiff_len=len(tiff), magic=magic)
    visited: set[int] = set()
    sub_count = [0]

    def children(ifd: Ifd) -> list[tuple[str, int]]:
        out = []
        for e in ifd.entries:
            sub = _SUBIFD_TAGS.get(e.tag)
            if sub is not None:
                out.append((sub, e.raw_value))
            elif e.tag == TAG_SUBIFDS or e.type == TYPE_IFD:
                try:
                    ptrs = _pointers(tiff, e, order)
                except struct.error:
                    ptrs = []
                for p in ptrs:
                    out.append((f"SubIFD{sub_count[0]}", p))
                    sub_count[0] += 1
        return out

    def walk_subifds(ifd: Ifd) -> None:
        for sub, sub_off in children(ifd):
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


# --------------------------------------------------------------------------- #
# Maker notes
# --------------------------------------------------------------------------- #
# Olympus keeps its serials one level down, in sub-IFDs the note points at with an
# UNDEFINED or IFD-typed entry counted from the note's own start.
_OLYMPUS_SUBIFDS = {0x2010: "Equipment", 0x2020: "CameraSettings",
                    0x2030: "RawDevelopment", 0x2031: "RawDevelopment2",
                    0x2040: "ImageProcessing", 0x2050: "FocusInfo",
                    0x3000: "RawInfo"}


def _order_of(mark: bytes) -> str | None:
    return {b"II": "<", b"MM": ">"}.get(bytes(mark))


def makernote(tiff: bytes, tree: IfdTree) -> MakerNote | None:
    """Locate the maker note and parse it for the makers M16 models.

    None when there is no maker note. A maker note that is present but in a layout
    not modelled here comes back with vendor "unknown" and no IFDs: the caller knows
    exactly which bytes it cannot account for, and decides -- never this function --
    whether that is a refusal.
    """
    exif = tree.ifd("ExifIFD")
    entry = exif.get(TAG_MAKERNOTE) if exif is not None else None
    if entry is None or entry.inline or not entry.data_length:
        return None
    off, length = entry.data_offset, entry.data_length
    head = bytes(tiff[off:off + 16])
    ifd0 = tree.ifd("IFD0")
    make_entry = ifd0.get(TAG_MAKE) if ifd0 is not None else None
    make = (value_bytes(tiff, make_entry).rstrip(b"\x00").strip().upper()
            if make_entry is not None else b"")

    def note(vendor: str, order: str, ifd_at: int, base: int) -> MakerNote:
        mn = MakerNote(vendor=vendor, offset=off, length=length)
        root = _parse_ifd(tiff, order, ifd_at, "MakerNote", base=base)
        mn.ifds.append(root)
        if vendor == "olympus":
            for e in root.entries:
                name = _OLYMPUS_SUBIFDS.get(e.tag)
                if name is None or e.type not in (4, 7, TYPE_IFD):
                    continue
                # A pointer (IFD/LONG) counts from the note's base; an UNDEFINED
                # block IS the IFD, at its data offset (already base-adjusted).
                sub_at = base + e.raw_value if e.inline else e.data_offset
                mn.ifds.append(_parse_ifd(tiff, order, sub_at,
                                          f"MakerNote/{name}", base=base))
        return mn

    if head.startswith(b"Nikon\x00") and _order_of(head[10:12]):
        order = _order_of(head[10:12])
        (inner,) = struct.unpack_from(order + "I", tiff, off + 14)
        return note("nikon", order, off + 10 + inner, off + 10)
    if head.startswith(b"OLYMPUS\x00") and _order_of(head[8:10]):
        return note("olympus", _order_of(head[8:10]), off + 12, off)
    if head.startswith(b"Apple iOS\x00") and _order_of(head[12:14]):
        return note("apple", _order_of(head[12:14]), off + 14, off)
    if head.startswith((b"SONY DSC ", b"SONY CAM ", b"SONY MOBILE")):
        return note("sony", tree.byte_order, off + 12, 0)
    if make.startswith(b"SONY"):
        return note("sony", tree.byte_order, off, 0)
    if make.startswith(b"CANON"):
        return note("canon", tree.byte_order, off, 0)
    return MakerNote(vendor="unknown", offset=off, length=length)


# --------------------------------------------------------------------------- #
# Size-preserving writes
# --------------------------------------------------------------------------- #
# Each takes a MUTABLE buffer (a bytearray, or a writable memoryview over one, so a
# TIFF block inside a larger container can be edited where it sits) and changes
# only the bytes it names. Nothing moves, so every offset in the file -- including
# the ones pointing at the sensor data -- stays true.

def zero(buf, offset: int, length: int) -> None:
    """Zero a region in place."""
    if offset < 0 or offset + length > len(buf):
        raise ParseError(f"zero: {offset}+{length} is outside the buffer")
    buf[offset:offset + length] = bytes(length)


def blank_value(buf, entry: IfdEntry) -> None:
    """Zero an entry's value, wherever it lives. Tag, type and count are kept, so
    the directory is unchanged and a reader still finds the field -- empty."""
    if entry.inline:
        zero(buf, entry.entry_offset + 8, 4)
    else:
        zero(buf, entry.data_offset, entry.data_length)


def remove_entry(buf, ifd: Ifd, tag: int) -> bool:
    """Delete one entry from its directory without moving anything else.

    The directory is rewritten in place one entry shorter -- entries still in
    ascending tag order, then the next-IFD pointer -- and the 12 bytes it no longer
    uses are zeroed, as is the removed entry's out-of-line value. Returns False if
    the tag was not there. `ifd` is updated to match.
    """
    drop = [e for e in ifd.entries if e.tag == tag]
    if not drop:
        return False
    for e in drop:
        if not e.inline:
            zero(buf, e.data_offset, e.data_length)
    keep = [e for e in ifd.entries if e.tag != tag]
    raw = [bytes(buf[e.entry_offset:e.entry_offset + 12]) for e in keep]
    order = ifd.order or "<"
    old_len = 2 + len(ifd.entries) * 12 + 4
    body = (struct.pack(order + "H", len(keep)) + b"".join(raw)
            + struct.pack(order + "I", ifd.next_offset))
    buf[ifd.offset:ifd.offset + old_len] = body + bytes(old_len - len(body))
    for i, e in enumerate(keep):
        e.entry_offset = ifd.offset + 2 + i * 12
    ifd.entries = keep
    return True


def blank_span(buf, entry: IfdEntry, start: int, length: int) -> None:
    """Zero part of an entry's value -- for a field packed inside a binary block,
    such as the lens serial in the first five bytes of Canon's `LensInfo`."""
    if start < 0 or start + length > entry.data_length:
        raise ParseError(f"tag {entry.tag:#06x}: span {start}+{length} is outside "
                         f"its {entry.data_length}-byte value")
    at = entry.entry_offset + 8 if entry.inline else entry.data_offset
    zero(buf, at + start, length)
