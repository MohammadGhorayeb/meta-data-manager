"""HEIC F1 — drop the metadata items and rebuild the tables that locate everything.

The shape of the problem, measured before any of this was written
(`docs/p4_media_plan.md` §0):

A HEIC's picture is a *grid* of 61-95 HEVC tiles, and sitting beside it in the same
`mdat` are the metadata items — `Exif`, XMP (`mime`), Apple's semantic-segmentation
plist (`uri `) — plus a thumbnail and several auxiliary images. Every one of them is
addressed by **absolute file offset** out of a single `iloc` table.

So removing a metadata item is not a deletion, it is a **relocation of everything
after it**. This is the `stco`/`co64` trap M4A taught this project, in its second
guise, and M4A also taught what failure looks like: a file that still parses, still
reports the right dimensions, and decodes to garbage. That is why the acceptance test
decodes the image rather than merely walking it.

Two measurements make the rebuild tractable and are worth stating because they were
checked rather than hoped for:

- **`mdat` is 100% covered by item extents**, on every synthetic and real file
  measured — zero gaps. So the new `mdat` is exactly the concatenation of the kept
  items' bytes, with nothing unaccounted for to preserve or to lose.
- **`iloc` field widths are fixed per file**, so rewriting offsets does not change the
  size of the `iloc` box — which means the box layout and the offsets it contains do
  not chase each other. One pass settles it.
"""
from __future__ import annotations

import struct

from ...errors import ContentError, ParseError
from ...standards import icc, isobmff
from . import walker as w

# Items whose whole purpose is to describe, not to depict.
DROP_TYPES = {b"Exif", b"mime", b"uri "}


def _dropped_ids(layout: w.Layout, drop_auxiliary: bool) -> set[int]:
    """Which items go.

    The thumbnail goes for the Phase 1 reason: it is a second picture of the same
    scene, and a thumbnail that outlives an edit shows what the picture used to be.

    Auxiliary images — depth maps and Apple's semantic skin/person mattes — are the
    genuinely arguable ones, and the argument is recorded rather than settled by
    taste. They are not the photograph and removing them does not change how it
    looks, but they *are* derived content: a person-segmentation matte is a map of
    where the people in the frame are. A privacy tool should not ship that quietly,
    so the default is to drop them and say so in the report.
    """
    drop = {i.item_id for i in layout.items.values() if i.item_type in DROP_TYPES}
    drop |= layout.thumbnail_ids()
    if drop_auxiliary:
        drop |= layout.auxiliary_ids()
    drop.discard(layout.primary_id)          # never the picture itself
    return drop


def _keep_order(layout: w.Layout, dropped: set[int]) -> list[w.Item]:
    """Kept file-addressed items, in their original offset order.

    Order is preserved rather than sorted by id: the tiles of a grid are laid out
    contiguously, and reordering them would not corrupt the file but would make our
    output's layout a fingerprint of our own sorting rather than of the format.
    """
    items = [i for i in layout.items.values()
             if i.item_id not in dropped and i.in_file and i.file_extents]
    return sorted(items, key=lambda i: i.file_extents[0].offset)


# --------------------------------------------------------------------------- #
# Rebuilding the tables
# --------------------------------------------------------------------------- #
def _rebuild_iinf(iinf: isobmff.Box, dropped: set[int]) -> bytes:
    """Drop the `infe` entry for every removed item and fix the entry count."""
    version = iinf.payload[0]
    head_len = 6 if version == 0 else 8
    head = bytearray(iinf.payload[:head_len])
    kept: list[bytes] = []
    for box in isobmff.parse(iinf.payload[head_len:]):
        if box.type != b"infe":
            kept.append(isobmff.serialize([box]))
            continue
        p = box.payload
        item_id = (struct.unpack_from(">H", p, 4)[0] if p[0] == 2
                   else struct.unpack_from(">I", p, 4)[0])
        if item_id not in dropped:
            kept.append(isobmff.serialize([box]))
    if version == 0:
        struct.pack_into(">H", head, 4, len(kept))
    else:
        struct.pack_into(">I", head, 4, len(kept))
    return bytes(head) + b"".join(kept)


def _rebuild_iref(iref: isobmff.Box, dropped: set[int]) -> bytes | None:
    """Drop references to or from removed items.

    A reference pointing at an item that no longer exists is the HEIC equivalent of
    DOCX's dangling relationship: the file still opens, and a reader following the
    link finds nothing. Returns None when nothing is left, so an empty box is not
    shipped — an emptied table is a tell that something was taken out of it.
    """
    version = iref.payload[0]
    wide = version >= 1
    kept: list[bytes] = []
    for box in isobmff.parse(iref.payload[4:]):
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
        if from_id in dropped:
            continue
        to_ids = [int.from_bytes(p[base + k * step: base + (k + 1) * step], "big")
                  for k in range(count)]
        to_ids = [t for t in to_ids if t not in dropped]
        if not to_ids:
            continue
        fmt = ">I" if wide else ">H"
        payload = struct.pack(fmt, from_id) + struct.pack(">H", len(to_ids)) \
            + b"".join(struct.pack(fmt, t) for t in to_ids)
        kept.append(isobmff.serialize([isobmff.Box(
            type=box.type, offset=0, size=8 + len(payload), header_len=8,
            payload=payload)]))
    if not kept:
        return None
    return iref.payload[:4] + b"".join(kept)


def _build_iloc(fmt: w.IlocFormat, placements: list[tuple[int, int, int]],
                idat_items: list[w.Item]) -> bytes:
    """Emit an `iloc` with the new offsets.

    `placements` is (item_id, offset, length) for file-addressed items;
    `idat_items` keep their original payloads because their bytes live in `idat`,
    which this tier does not move.
    """
    if fmt.version < 2:
        head = struct.pack(">BBBB", fmt.version, 0, 0, 0)
    else:
        head = struct.pack(">BBBB", fmt.version, 0, 0, 0)
    sizes = bytes([(fmt.offset_size << 4) | fmt.length_size,
                   (fmt.base_offset_size << 4) | fmt.index_size])

    entries: list[tuple[int, bytes]] = []
    for item_id, offset, length in placements:
        body = b""
        if fmt.version in (1, 2):
            body += struct.pack(">H", w.CONSTRUCTION_FILE)
        body += struct.pack(">H", 0)                       # data_reference_index
        body += b"\x00" * fmt.base_offset_size
        body += struct.pack(">H", 1)                       # one extent
        body += b"\x00" * fmt.index_size
        body += offset.to_bytes(fmt.offset_size, "big")
        body += length.to_bytes(fmt.length_size, "big")
        entries.append((item_id, body))

    for item in idat_items:
        body = b""
        if fmt.version in (1, 2):
            body += struct.pack(">H", w.CONSTRUCTION_IDAT)
        body += struct.pack(">H", 0)
        body += b"\x00" * fmt.base_offset_size
        body += struct.pack(">H", len(item.extents))
        for extent in item.extents:
            body += b"\x00" * fmt.index_size
            # An idat-relative offset is preserved verbatim: `idat` is untouched.
            body += max(extent.offset, 0).to_bytes(fmt.offset_size, "big")
            body += extent.length.to_bytes(fmt.length_size, "big")
        entries.append((item.item_id, body))

    entries.sort(key=lambda e: e[0])
    out = bytearray(head + sizes)
    if fmt.version < 2:
        out += struct.pack(">H", len(entries))
    else:
        out += struct.pack(">I", len(entries))
    for item_id, body in entries:
        out += (struct.pack(">I", item_id) if fmt.version >= 2
                else struct.pack(">H", item_id))
        out += body
    return bytes(out)


def _idat_offsets_preserved(layout: w.Layout) -> list[w.Item]:
    return [i for i in layout.items.values() if not i.in_file]


def scrub(data: bytes, *, drop_auxiliary: bool = True) -> bytes:
    """Remove the metadata items and rebuild `iinf`, `iloc`, `iref` and `ipma`."""
    layout = w.walk(data)
    dropped = _dropped_ids(layout, drop_auxiliary)

    kept = _keep_order(layout, dropped)
    if not kept and layout.items[layout.primary_id].in_file:
        raise ContentError("HEIC: nothing left to keep — refusing to write")

    ftyp = next((b for b in layout.boxes if b.type == b"ftyp"), None)
    mdat = next((b for b in layout.boxes if b.type == b"mdat"), None)
    if ftyp is None or mdat is None:
        raise ParseError("HEIC: missing ftyp or mdat")

    # Pass 1: lay the kept items out back-to-back, offsets relative to mdat's body.
    relative: list[tuple[int, int, int]] = []
    cursor = 0
    payload = bytearray()
    for item in kept:
        start = cursor
        for extent in item.file_extents:
            payload += data[extent.offset:extent.end]
            cursor += extent.length
        relative.append((item.item_id, start, cursor - start))

    # Pass 2: rebuild `meta`. `iloc` entry sizes do not depend on the offset VALUES
    # (the field widths are fixed per file), so meta's size is known before the
    # absolute offsets are, and the two never chase each other.
    idat_items = _idat_offsets_preserved(layout)
    children: list[bytes] = []
    for child in layout.meta.children:
        if child.type == b"iinf":
            body = _rebuild_iinf(child, dropped)
        elif child.type == b"iref":
            body = _rebuild_iref(child, dropped)
            if body is None:
                continue
        elif child.type == b"iloc":
            body = _build_iloc(layout.iloc_format, relative, idat_items)
        elif child.type == b"iprp":
            body = _rebuild_iprp(child, dropped)
        else:
            body = child.payload if child.payload else isobmff.serialize(
                child.children)
        children.append(_box(child.type, body))

    meta_body = layout.meta.payload[:4] if layout.meta.payload else b"\x00" * 4
    meta_box = _box(b"meta", meta_body + b"".join(children))
    ftyp_box = _box(b"ftyp", ftyp.payload)

    mdat_start = len(ftyp_box) + len(meta_box) + 8      # + mdat header
    absolute = [(i, mdat_start + off, length) for i, off, length in relative]

    # Re-emit `iloc` with absolute offsets. Same length by construction, so the
    # layout computed above still holds — asserted rather than assumed.
    final_children: list[bytes] = []
    for child in layout.meta.children:
        if child.type == b"iloc":
            body = _build_iloc(layout.iloc_format, absolute, idat_items)
        elif child.type == b"iinf":
            body = _rebuild_iinf(child, dropped)
        elif child.type == b"iref":
            body = _rebuild_iref(child, dropped)
            if body is None:
                continue
        elif child.type == b"iprp":
            body = _rebuild_iprp(child, dropped)
        else:
            body = child.payload if child.payload else isobmff.serialize(
                child.children)
        final_children.append(_box(child.type, body))
    final_meta = _box(b"meta", meta_body + b"".join(final_children))
    if len(final_meta) != len(meta_box):
        raise ParseError("HEIC: meta size changed when offsets were filled in")

    return ftyp_box + final_meta + _box(b"mdat", bytes(payload))


def _children_of(box: isobmff.Box) -> list[isobmff.Box]:
    """A container's children, whether or not the shared walker knows it is one.

    `standards/isobmff.CONTAINERS` is the set M4A needed, and `iprp` is not in it —
    so `iprp.children` is empty and its payload holds the raw child boxes. Rebuilding
    from `.children` therefore emitted an **empty `iprp`**, and libheif answered
    `Invalid input: No 'ipco' box`: a file that walked perfectly and decoded to
    nothing, which is exactly the failure mode this tier's decode test exists for.

    Handled here rather than by adding `iprp` to the shared set, because that set is
    load-bearing for M4A's box surgery and widening it to suit a different format is
    how a shared module acquires a format-specific bug.
    """
    if box.children:
        return box.children
    try:
        return isobmff.parse(box.payload)
    except Exception:                                     # noqa: BLE001
        return []


def _parse_ipma(payload: bytes) -> tuple[int, int, list[tuple[int, list[tuple[int, bool]]]]]:
    """`(version, flags, [(item_id, [(property_index, essential)])])`."""
    version, flags = payload[0], int.from_bytes(payload[1:4], "big")
    wide_id = version >= 1
    wide_index = bool(flags & 1)
    count = struct.unpack_from(">I", payload, 4)[0]
    i = 8
    out = []
    for _ in range(count):
        item_id = (struct.unpack_from(">I", payload, i)[0] if wide_id
                   else struct.unpack_from(">H", payload, i)[0])
        i += 4 if wide_id else 2
        n = payload[i]
        i += 1
        assoc = []
        for _k in range(n):
            if wide_index:
                raw = struct.unpack_from(">H", payload, i)[0]
                essential, index = bool(raw & 0x8000), raw & 0x7FFF
                i += 2
            else:
                raw = payload[i]
                essential, index = bool(raw & 0x80), raw & 0x7F
                i += 1
            assoc.append((index, essential))
        out.append((item_id, assoc))
    return version, flags, out


def _emit_ipma(version: int, flags: int,
               entries: list[tuple[int, list[tuple[int, bool]]]]) -> bytes:
    wide_id = version >= 1
    wide_index = bool(flags & 1)
    out = bytearray(struct.pack(">B", version) + flags.to_bytes(3, "big"))
    out += struct.pack(">I", len(entries))
    for item_id, assoc in entries:
        out += (struct.pack(">I", item_id) if wide_id
                else struct.pack(">H", item_id))
        out += bytes([len(assoc)])
        for index, essential in assoc:
            if wide_index:
                out += struct.pack(">H", (0x8000 if essential else 0) | index)
            else:
                out += bytes([(0x80 if essential else 0) | index])
    return bytes(out)


def _rebuild_iprp(iprp: isobmff.Box, dropped: set[int]) -> bytes:
    """`iprp` holds `ipco` (the properties) and `ipma` (which item uses which).

    Properties are shared **by index**, so pruning `ipco` means renumbering every
    surviving association. Doing it is not optional tidiness. Keeping `ipco` whole
    left a scrubbed iPhone photo still advertising

        AuxiliaryImageType: urn:com:apple:photo:2019:aux:semanticskinmatte

    after the matte itself had been removed — the picture no longer carried Apple's
    person-segmentation output, but it still announced that the segmentation had run
    and produced one, which weakly implies what it found. Measured with ExifTool on a
    real photo, which is the whole reason the report cross-checks with a second
    implementation.

    So: keep only the properties some surviving item still points at, and remap the
    indices.
    """
    children = _children_of(iprp)
    ipco = next((c for c in children if c.type == b"ipco"), None)
    ipma = next((c for c in children if c.type == b"ipma"), None)
    if ipco is None:
        raise ParseError("HEIC: iprp has no ipco box")
    if ipma is None:
        return b"".join(_box(c.type, c.payload if c.payload
                             else isobmff.serialize(c.children)) for c in children)

    properties = _children_of(ipco)
    version, flags, entries = _parse_ipma(ipma.payload)
    kept_entries = [(iid, assoc) for iid, assoc in entries if iid not in dropped]

    # `ipma` indices are 1-based; 0 means "no property".
    still_used = sorted({idx for _iid, assoc in kept_entries for idx, _e in assoc
                         if 1 <= idx <= len(properties)})
    remap = {old: new for new, old in enumerate(still_used, start=1)}

    new_ipco = b"".join(
        _box(properties[idx - 1].type, _property_body(properties[idx - 1]))
        for idx in still_used)
    remapped = [(iid, [(remap.get(idx, 0), essential) for idx, essential in assoc
                       if idx in remap])
                for iid, assoc in kept_entries]

    out: list[bytes] = []
    for child in children:
        if child.type == b"ipco":
            out.append(_box(b"ipco", new_ipco))
        elif child.type == b"ipma":
            out.append(_box(b"ipma", _emit_ipma(version, flags, remapped)))
        else:
            out.append(_box(child.type, child.payload if child.payload
                            else isobmff.serialize(child.children)))
    return b"".join(out)


def _property_body(prop: isobmff.Box) -> bytes:
    """One `ipco` property's bytes, with a colour profile's provenance removed.

    A `colr` property of type `prof`/`rICC` wraps an ICC profile, and a real iPhone
    photo's says `DeviceManufacturer: Apple Computer Inc.`, `ProfileCreator: Apple
    Computer Inc.` and a profile date. `standards/icc.sanitize()` zeroes exactly
    those header fields and recomputes the profile id, leaving the tag data — the
    actual colour transform — untouched, so rendering does not change.

    Same treatment JPEG and PNG already give an embedded profile, and the same
    documented residual (limit #14): what survives narrows to the platform, not the
    person, because the colour data itself still describes Display P3.
    """
    body = prop.payload if prop.payload else isobmff.serialize(prop.children)
    if prop.type != b"colr" or len(body) < 4:
        return body
    colour_type = body[:4]
    if colour_type not in (b"prof", b"rICC"):
        return body                      # `nclx` is three integers, nothing to strip
    try:
        return colour_type + icc.sanitize(body[4:])
    except Exception:                                     # noqa: BLE001
        return body


def _box(box_type: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body) + 8) + box_type + body


def residuals(data: bytes) -> list[str]:
    """Metadata that should have been removed and was not — a scrub failure."""
    try:
        layout = w.walk(data)
    except ParseError as exc:
        return [f"unparseable: {exc}"]
    out = []
    for item in layout.metadata_items:
        out.append(f"{item.item_type.decode('latin-1').strip()} item "
                   f"{item.item_id} survived ({item.size} bytes)")
    for thumb in layout.thumbnail_ids():
        out.append(f"thumbnail item {thumb} survived")
    return out
