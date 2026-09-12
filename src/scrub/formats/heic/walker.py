"""HEIC structure walker: the *item* model.

M4A taught this project the ISOBMFF box layer. HEIC adds a second layer on top of it
that M4A never needed, and everything hard about the format lives there: a HEIC is
not one image in a box, it is a **table of items** — 61 to 95 HEVC tiles that compose
the photograph, plus auxiliary images, metadata blobs and a thumbnail, all addressed
by absolute offset out of one `iloc` table (measured: `docs/p4_media_plan.md` §0).

So the walker's job is to turn `meta`'s children into that table:

    pitm   which item is the photograph
    iinf   item id -> type (`hvc1`, `Exif`, `mime`, `uri `, `grid`) and name
    iloc   item id -> the byte extents holding it
    iref   how items relate: `dimg` derived-from, `auxl` auxiliary-for,
           `cdsc` describes, `thmb` thumbnail-of
    ipma   which properties apply to which item

Reading is accounting, not interpretation — the same contract as the PDF and ZIP
walkers. Anything we cannot fully account for is refused rather than guessed at,
because an item we mis-locate is an item we either fail to remove or remove from the
middle of the photograph.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from ...errors import ParseError
from ...standards import isobmff

MAGIC_OFFSET = 4                     # `ftyp` follows the 4-byte box size
FTYP = b"ftyp"

# Brands that mean "this is a HEIF still image we handle". `mif1` is the generic
# image-file brand; `heic`/`heix` are the HEVC profiles Apple writes. Deliberately
# NOT `isom`/`mp41`/`mp42` (an MP4) or `M4A ` (audio, already handled elsewhere).
HEIC_BRANDS = {b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"mif1", b"msf1"}

# Item types that carry metadata rather than picture. `uri ` is the general
# "identified by a URI" item; Apple uses it for a binary plist of semantic
# segmentation data, which is 3-5% of a typical file.
METADATA_ITEM_TYPES = {b"Exif", b"mime", b"uri "}

# Construction methods from the ISO spec.
#   0  a plain file offset
#   1  an offset into the `meta`/`idat` box -- which Apple writes for the `grid`
#      item that composes the tiles, INCLUDING the primary item, so refusing it
#      refuses every real iPhone photo. Measured, not assumed: the first version of
#      this walker refused method 1 and rejected all six corpus files.
#   2  stored inside another item; unseen in the corpus and refused untested.
CONSTRUCTION_FILE = 0
CONSTRUCTION_IDAT = 1
CONSTRUCTION_ITEM = 2


@dataclass
class Extent:
    offset: int
    length: int

    @property
    def end(self) -> int:
        return self.offset + self.length


@dataclass
class Item:
    item_id: int
    item_type: bytes = b""
    name: str = ""
    # Extents with `offset == -1` are `idat`-relative: they are NOT regions of the
    # file, and anything that moves bytes must skip them.
    extents: list[Extent] = field(default_factory=list)
    construction: int = CONSTRUCTION_FILE
    base_offset: int = 0

    @property
    def is_metadata(self) -> bool:
        return self.item_type in METADATA_ITEM_TYPES

    @property
    def size(self) -> int:
        return sum(e.length for e in self.extents)

    @property
    def in_file(self) -> bool:
        """Whether this item's bytes are addressed as file offsets, and so can be
        moved by rewriting `iloc`."""
        return self.construction == CONSTRUCTION_FILE

    @property
    def file_extents(self) -> list[Extent]:
        return [e for e in self.extents if e.offset >= 0]


@dataclass
class Reference:
    kind: bytes                      # `dimg` | `auxl` | `cdsc` | `thmb` | ...
    from_id: int
    to_ids: list[int]


@dataclass
class IlocFormat:
    """The per-file field widths. They are not constant across producers, which is
    why rewriting `iloc` cannot use a fixed struct format."""
    version: int
    offset_size: int
    length_size: int
    base_offset_size: int
    index_size: int


@dataclass
class Layout:
    boxes: list[isobmff.Box]
    meta: isobmff.Box
    primary_id: int
    items: dict[int, Item]
    references: list[Reference]
    iloc_format: IlocFormat

    def by_type(self, item_type: bytes) -> list[Item]:
        return [i for i in self.items.values() if i.item_type == item_type]

    @property
    def metadata_items(self) -> list[Item]:
        return [i for i in self.items.values() if i.is_metadata]

    def referenced_by(self, kind: bytes) -> set[int]:
        """Items on the *from* side of a reference — e.g. every auxiliary image."""
        return {r.from_id for r in self.references if r.kind == kind}

    def thumbnail_ids(self) -> set[int]:
        return self.referenced_by(b"thmb")

    def auxiliary_ids(self) -> set[int]:
        return self.referenced_by(b"auxl")


def _full_box_children(box: isobmff.Box) -> list[isobmff.Box]:
    """Children of a FULL box, skipping its version+flags word."""
    return isobmff.parse(box.payload[4:]) if len(box.payload) > 4 else []


def brand(data: bytes) -> bytes:
    if len(data) < 12 or data[4:8] != FTYP:
        return b""
    return data[8:12]


def looks_like_heic(data: bytes) -> bool:
    """Cheap identification. Like a ZIP's `PK`, an `ftyp` prefix is shared by every
    ISOBMFF file, so the brand is what decides."""
    major = brand(data)
    if major in HEIC_BRANDS:
        return True
    # A file may declare a generic major brand and list the real one in the
    # compatible-brands list that follows.
    if not major:
        return False
    try:
        size = struct.unpack_from(">I", data, 0)[0]
    except struct.error:
        return False
    compatible = data[16:min(size, len(data))]
    return any(compatible[i:i + 4] in HEIC_BRANDS
               for i in range(0, max(0, len(compatible) - 3), 4))


def _parse_iinf(meta: isobmff.Box, items: dict[int, Item]) -> None:
    iinf = next((c for c in meta.children if c.type == b"iinf"), None)
    if iinf is None:
        raise ParseError("HEIC: no iinf box (cannot enumerate items)")
    version = iinf.payload[0]
    # The entry count is 16-bit in version 0 and 32-bit from version 1.
    skip = 6 if version == 0 else 8
    for box in isobmff.parse(iinf.payload[skip:]):
        if box.type != b"infe":
            continue
        p = box.payload
        if not p:
            continue
        infe_version = p[0]
        if infe_version < 2:
            # Versions 0 and 1 describe items by a different layout that Apple does
            # not write and this project has never seen. Refuse rather than model
            # a shape we cannot test against a real file.
            raise ParseError(f"HEIC: infe version {infe_version} not modelled")
        if infe_version == 2:
            item_id = struct.unpack_from(">H", p, 4)[0]
            rest = 6
        else:
            item_id = struct.unpack_from(">I", p, 4)[0]
            rest = 8
        item_type = p[rest + 2:rest + 6]
        name = p[rest + 6:].split(b"\x00")[0].decode("utf-8", "replace")
        entry = items.setdefault(item_id, Item(item_id))
        entry.item_type = item_type
        entry.name = name


def _parse_iloc(meta: isobmff.Box, items: dict[int, Item],
                total: int) -> IlocFormat:
    iloc = next((c for c in meta.children if c.type == b"iloc"), None)
    if iloc is None:
        raise ParseError("HEIC: no iloc box (cannot locate items)")
    p = iloc.payload
    if len(p) < 8:
        raise ParseError("HEIC: iloc truncated")
    version = p[0]
    fmt = IlocFormat(version=version, offset_size=p[4] >> 4, length_size=p[4] & 0xF,
                     base_offset_size=p[5] >> 4, index_size=p[5] & 0xF)
    if version > 2:
        raise ParseError(f"HEIC: iloc version {version} not modelled")
    for width in (fmt.offset_size, fmt.length_size, fmt.base_offset_size):
        if width not in (0, 4, 8):
            raise ParseError(f"HEIC: iloc field width {width} not modelled")

    if version < 2:
        count = struct.unpack_from(">H", p, 6)[0]
        i = 8
    else:
        count = struct.unpack_from(">I", p, 6)[0]
        i = 10

    for _ in range(count):
        if version < 2:
            item_id = struct.unpack_from(">H", p, i)[0]
            i += 2
        else:
            item_id = struct.unpack_from(">I", p, i)[0]
            i += 4
        construction = CONSTRUCTION_FILE
        if version in (1, 2):
            construction = struct.unpack_from(">H", p, i)[0] & 0x0F
            i += 2
        i += 2                                          # data_reference_index
        base_offset = int.from_bytes(p[i:i + fmt.base_offset_size], "big") \
            if fmt.base_offset_size else 0
        i += fmt.base_offset_size
        extent_count = struct.unpack_from(">H", p, i)[0]
        i += 2

        entry = items.setdefault(item_id, Item(item_id))
        entry.construction = construction
        entry.base_offset = base_offset
        for _e in range(extent_count):
            i += fmt.index_size
            offset = int.from_bytes(p[i:i + fmt.offset_size], "big")
            i += fmt.offset_size
            length = int.from_bytes(p[i:i + fmt.length_size], "big")
            i += fmt.length_size
            if construction == CONSTRUCTION_FILE:
                absolute = base_offset + offset
                if absolute + length > total:
                    raise ParseError(
                        f"HEIC: item {item_id} extent runs past EOF "
                        f"({absolute + length} > {total})")
                entry.extents.append(Extent(absolute, length))
            else:
                # `idat`-relative (method 1): the offset indexes into the `idat` box
                # rather than the file. Recorded rather than resolved, and never
                # given a file Extent, so nothing downstream can mistake it for a
                # region of the file to move.
                entry.extents.append(Extent(-1, length))
        if construction == CONSTRUCTION_ITEM:
            # Item-relative storage nests one item's bytes inside another's, so
            # moving either moves both. Not written by any producer measured here,
            # and refused rather than modelled untested.
            raise ParseError(
                f"HEIC: item {item_id} uses construction method 2 "
                f"(stored inside another item), which is not modelled")
    return fmt


def _parse_iref(meta: isobmff.Box) -> list[Reference]:
    iref = next((c for c in meta.children if c.type == b"iref"), None)
    if iref is None:
        return []
    version = iref.payload[0] if iref.payload else 0
    wide = version >= 1                                  # 32-bit ids from version 1
    out: list[Reference] = []
    for box in _full_box_children(iref):
        p = box.payload
        if len(p) < 4:
            continue
        if wide:
            from_id = struct.unpack_from(">I", p, 0)[0]
            count = struct.unpack_from(">H", p, 4)[0]
            base, step = 6, 4
        else:
            from_id = struct.unpack_from(">H", p, 0)[0]
            count = struct.unpack_from(">H", p, 2)[0]
            base, step = 4, 2
        to_ids = []
        for k in range(count):
            at = base + k * step
            if at + step > len(p):
                break
            to_ids.append(int.from_bytes(p[at:at + step], "big"))
        out.append(Reference(box.type, from_id, to_ids))
    return out


def walk(data: bytes) -> Layout:
    """Parse a HEIC into its item table. Fail closed on anything unaccounted for."""
    if not looks_like_heic(data):
        raise ParseError("HEIC: not a HEIF still-image brand")
    boxes = isobmff.parse(data)
    meta = next((b for b in boxes if b.type == b"meta"), None)
    if meta is None:
        raise ParseError("HEIC: no meta box")

    pitm = next((c for c in meta.children if c.type == b"pitm"), None)
    if pitm is None or len(pitm.payload) < 6:
        raise ParseError("HEIC: no pitm box (cannot tell which item is the picture)")
    primary = (struct.unpack_from(">H", pitm.payload, 4)[0]
               if pitm.payload[0] == 0
               else struct.unpack_from(">I", pitm.payload, 4)[0])

    items: dict[int, Item] = {}
    _parse_iinf(meta, items)
    fmt = _parse_iloc(meta, items, len(data))
    if primary not in items:
        raise ParseError(f"HEIC: primary item {primary} is not in the item table")

    return Layout(boxes=boxes, meta=meta, primary_id=primary, items=items,
                  references=_parse_iref(meta), iloc_format=fmt)
