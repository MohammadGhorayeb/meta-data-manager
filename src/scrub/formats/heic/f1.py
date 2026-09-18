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

# The one auxiliary image kept, named by the last component of its `auxC` URN.
#
# An HDR gain map is not a claim about the subject; it is a half-resolution
# luminance map of the very pixels we are preserving, and it is *how the photograph
# renders* on an HDR display. Removing it is a content change under hard constraint
# #1, and — this is the uncomfortable part — one our own oracle cannot see: libheif
# decodes the primary item and ignores the gain map, so a pixel-identity test stays
# green while the picture changes on the device its owner looks at it on (limit #32).
#
# Every other auxiliary image goes. A depth map is the shape of the scene; Apple's
# semantic skin/sky/portrait mattes are machine-made claims about the subject (a map
# of where the people are); a `styledeltamap` is the edit that was applied; a
# `linearthumbnail` is a second picture of the same scene. An unrecognised aux type
# is dropped too — the allowlist is deliberately the small side, so a new Apple
# matte we have never seen fails toward removal.
KEEP_AUX_KINDS = frozenset({"hdrgainmap"})


def _aux_kind(urn: str) -> str:
    """The trailing component of an `auxC` URN.

    Apple rewrites the prefix — `urn:com:apple:photo:2019:aux:` became
    `urn:com:apple:photo:2020:aux:` and then `tag:apple.com,2023:photo:aux:` — while
    keeping the name at the end. Matching the tail rather than the whole string means
    a year bump does not silently turn a kept image into a dropped one.
    """
    return urn.rsplit(":", 1)[-1].strip().lower()


def _policy_drops(layout: w.Layout, drop_auxiliary: bool) -> set[int]:
    """Items removed because of what they are, before the graph has its say."""
    drop = {i.item_id for i in layout.items.values() if i.item_type in DROP_TYPES}
    # The thumbnail goes for the Phase 1 reason: it is a second picture of the same
    # scene, and a thumbnail that outlives an edit shows what the picture used to be.
    drop |= layout.thumbnail_ids()
    if drop_auxiliary:
        drop |= {a for a in layout.auxiliary_ids()
                 if _aux_kind(layout.aux_types.get(a, "")) not in KEEP_AUX_KINDS}
    drop.discard(layout.primary_id)          # never the picture itself
    return drop


def _dropped_ids(layout: w.Layout, drop_auxiliary: bool) -> set[int]:
    """Which items go — removal computed on the `dimg` graph, not per item.

    The first version of this function was the per-item version, and it was wrong in
    a way that only a real photo shows. An auxiliary image on an iPhone is usually a
    **grid**: item 62 is the HDR gain map, and the 8 bytes `iloc` points at are the
    grid descriptor, while the picture itself lives in twelve separate `hvc1` items.
    Dropping item 62 therefore removed the *description* of the auxiliary image and
    left every one of its tiles in `mdat` — 381 KB of a 1.17 MB file on IMG_0502,
    707 KB of 1.78 MB on IMG_2427, and each tile still individually decodable. The
    file said the mattes were gone and carried them anyway.

    So: an item survives if a surviving root still composes it. Roots are the items
    nothing else derives from (the photograph, a `tmap`, a kept auxiliary), minus
    whatever policy removes; removed items are **barriers**, so nothing is reached
    through them, while a tile shared with a surviving image is still reached by that
    other path.
    """
    by_policy = _policy_drops(layout, drop_auxiliary)
    roots = ({layout.primary_id} | (set(layout.items) - layout.dimg_targets()))
    keep = layout.reachable_from(roots - by_policy, blocked=by_policy)
    return set(layout.items) - keep


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
                idat_placements: dict[int, list[tuple[int, int]]]) -> bytes:
    """Emit an `iloc` with the new offsets.

    `placements` is (item_id, offset, length) for file-addressed items;
    `idat_placements` is item id -> [(offset within the REBUILT `idat`, length)].

    The idat half used to be written as `max(extent.offset, 0)`, and the model stored
    -1 there, so every idat-stored item was located at **offset 0**. The primary grid
    survived that because it is written first; a `tmap` beside it did not, and read
    the grid descriptors instead of its own tone-mapping data. It is the `stco` trap
    one level further down — the file parses, and the channel it corrupts is one the
    pixel oracle cannot see.
    """
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

    for item_id, spans in idat_placements.items():
        body = b""
        if fmt.version in (1, 2):
            body += struct.pack(">H", w.CONSTRUCTION_IDAT)
        body += struct.pack(">H", 0)
        body += b"\x00" * fmt.base_offset_size
        body += struct.pack(">H", len(spans))
        for offset, length in spans:
            body += b"\x00" * fmt.index_size
            body += offset.to_bytes(fmt.offset_size, "big")
            body += length.to_bytes(fmt.length_size, "big")
        entries.append((item_id, body))

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


def _rebuild_idat(layout: w.Layout,
                  dropped: set[int]) -> tuple[bytes, dict[int, list[tuple[int, int]]]]:
    """Re-pack `idat` with only the surviving items, and say where they landed.

    `idat` used to be copied through whole. It is small — 86 bytes on a real photo —
    but it is where the *grid descriptors* live, so copying it whole left the
    description of every removed auxiliary grid sitting in the output, addressable by
    anyone who reads `iloc` rather than `iinf`.

    Nothing here depends on file offsets, so the result is computed once and used by
    both offset passes below; `idat` shrinking cannot move `mdat` twice.
    """
    if layout.idat is None:
        return b"", {}
    old = layout.idat.payload
    out = bytearray()
    placements: dict[int, list[tuple[int, int]]] = {}
    survivors = [i for i in layout.items.values()
                 if not i.in_file and i.item_id not in dropped and i.extents]
    # Original order, for the `_keep_order` reason: our own sorting would be our own
    # fingerprint.
    survivors.sort(key=lambda i: min(e.idat_offset for e in i.extents))
    for item in survivors:
        spans = []
        for extent in item.extents:
            start = len(out)
            out += old[extent.idat_offset:extent.idat_offset + extent.length]
            spans.append((start, extent.length))
        placements[item.item_id] = spans
    return bytes(out), placements


def _rebuild_grpl(grpl: isobmff.Box, dropped: set[int]) -> bytes | None:
    """Drop removed items from every entity group.

    `grpl` holds `EntityToGroupBox`es — Apple writes an `altr` group meaning "these
    are alternatives, show one" — and they name items by id, which makes them a
    fourth table that can point at something no longer there. Same failure as a
    dangling `iref`, in a box the rewrite previously copied through verbatim.
    """
    kept: list[bytes] = []
    for box in w.children_of(grpl):
        p = box.payload
        if len(p) < 12:
            continue
        group_id = struct.unpack_from(">I", p, 4)[0]
        count = struct.unpack_from(">I", p, 8)[0]
        ids = [struct.unpack_from(">I", p, 12 + 4 * k)[0]
               for k in range(count) if 12 + 4 * (k + 1) <= len(p)]
        ids = [i for i in ids if i not in dropped]
        if not ids:
            continue
        body = (p[:4] + struct.pack(">II", group_id, len(ids))
                + b"".join(struct.pack(">I", i) for i in ids))
        kept.append(_box(box.type, body))
    return b"".join(kept) if kept else None


def _meta_body(layout: w.Layout, dropped: set[int], iloc_body: bytes,
               new_idat: bytes) -> bytes:
    """Rebuild `meta` once, from one place.

    Written as a single function because it used to be two copies — one for the pass
    that measures and one for the pass that fills in the offsets — and two copies of
    a rewrite that must agree byte for byte is a bug waiting for the first table that
    is only fixed in one of them.
    """
    children: list[bytes] = []
    for child in layout.meta.children:
        if child.type == b"iinf":
            body = _rebuild_iinf(child, dropped)
        elif child.type == b"iref":
            maybe = _rebuild_iref(child, dropped)
            if maybe is None:
                continue
            body = maybe
        elif child.type == b"grpl":
            maybe = _rebuild_grpl(child, dropped)
            if maybe is None:
                continue
            body = maybe
        elif child.type == b"iprp":
            body = _rebuild_iprp(child, dropped)
        elif child.type == b"idat":
            if not new_idat:
                continue
            body = new_idat
        elif child.type == b"iloc":
            body = iloc_body
        else:
            body = child.payload if child.payload else isobmff.serialize(
                child.children)
        children.append(_box(child.type, body))
    head = layout.meta.payload[:4] if layout.meta.payload else b"\x00" * 4
    return head + b"".join(children)


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
    # absolute offsets are, and the two never chase each other. `idat` is re-packed
    # first, once, for the same reason.
    new_idat, idat_placements = _rebuild_idat(layout, dropped)
    meta_box = _box(b"meta", _meta_body(
        layout, dropped,
        _build_iloc(layout.iloc_format, relative, idat_placements), new_idat))
    ftyp_box = _box(b"ftyp", ftyp.payload)

    mdat_start = len(ftyp_box) + len(meta_box) + 8      # + mdat header
    absolute = [(i, mdat_start + off, length) for i, off, length in relative]

    # Re-emit `iloc` with absolute offsets. Same length by construction, so the
    # layout computed above still holds — asserted rather than assumed.
    final_meta = _box(b"meta", _meta_body(
        layout, dropped,
        _build_iloc(layout.iloc_format, absolute, idat_placements), new_idat))
    if len(final_meta) != len(meta_box):
        raise ParseError("HEIC: meta size changed when offsets were filled in")

    return ftyp_box + final_meta + _box(b"mdat", bytes(payload))


# The readers live in the walker, with the rest of the item model; the writers stay
# here. Aliased rather than re-implemented — one copy of `iprp`'s awkward
# not-a-container rule is the whole point.
_children_of = w.children_of
_parse_ipma = w.parse_ipma


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
    """Metadata that should have been removed and was not — a scrub failure.

    The orphan check is here because the interesting failure was not a named tag
    surviving: it was **content with nothing left to name it**. Dropping a tiled
    auxiliary image removed the 8-byte grid descriptor and left its twelve tiles in
    `mdat`, where no `iinf` entry mentions them and every tag-based check reports the
    file clean. So the check is connectivity, not naming — an item nothing in the
    file relates to the photograph is bytes we failed to remove, whatever it is
    called.
    """
    try:
        layout = w.walk(data)
    except ParseError as exc:
        return [f"unparseable: {exc}"]
    out = []
    for item in layout.metadata_items:
        out.append(f"{item.item_type.decode('latin-1').strip()} item "
                   f"{item.item_id} survived ({item.size} bytes)")
    for thumb in sorted(layout.thumbnail_ids()):
        out.append(f"thumbnail item {thumb} survived")
    for aux in sorted(layout.auxiliary_ids()):
        kind = _aux_kind(layout.aux_types.get(aux, ""))
        if kind not in KEEP_AUX_KINDS:
            out.append(f"auxiliary image item {aux} survived "
                       f"({layout.aux_types.get(aux, 'unnamed')})")
    orphans = sorted(set(layout.items) - layout.connected_to(layout.primary_id))
    if orphans:
        total = sum(layout.items[i].size for i in orphans)
        out.append(f"{len(orphans)} orphaned item(s) nothing relates to the "
                   f"picture, {total} bytes: {orphans[:8]}")
    return out
