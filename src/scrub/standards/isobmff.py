"""ISOBMFF (ISO base media file format) box walker — a SHARED standard module.

Deliberately not under `formats/m4a/`: M4A, MP4, MOV, HEIC and AVIF are all ISOBMFF,
so Phase 4's video and camera handlers reuse this unchanged. Writing it once is the
point (CLAUDE.md: shared modules are written once and called by every handler,
because a missed copy is a leak).

Structure: a flat sequence of boxes, each `size(4) type(4) payload`, where
  size == 1  -> a 64-bit largesize follows the type
  size == 0  -> the box runs to EOF (legal only for the last box)
and `uuid` boxes carry a further 16-byte extended type. Container boxes hold child
boxes instead of a payload; "full boxes" begin with a 1-byte version + 3-byte flags.

THE OFFSET TRAP, and the reason this module exists rather than a byte-level hack:
the sample tables (`stco` / `co64`) store **absolute file offsets** of the audio
chunks. Deleting any box that sits before `mdat` slides the audio and silently
invalidates every one of those offsets — the file still parses, and plays as noise
or not at all. Any edit here must either preserve those offsets or patch them, and
this module provides the primitives for the latter.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..errors import ParseError, ScrubError

# Boxes whose payload is a sequence of child boxes rather than data. Anything not
# listed is treated as a leaf: unknown boxes are never descended into, so an
# unrecognised container is preserved whole rather than half-parsed.
CONTAINERS = {
    b"moov", b"trak", b"edts", b"mdia", b"minf", b"dinf", b"stbl", b"udta",
    b"mvex", b"moof", b"traf", b"mfra", b"skip", b"strk", b"ilst",
}

# `meta` is a container, but a FULL box: 4 bytes of version/flags precede its
# children. Parsing it as a plain container yields garbage children.
FULL_CONTAINERS = {b"meta"}

HEADER_MIN = 8


@dataclass
class Box:
    type: bytes
    offset: int                     # absolute offset of the box header
    size: int                       # total size including header
    header_len: int                 # bytes before the payload/children
    payload: bytes = b""            # leaf boxes only
    children: list[Box] = field(default_factory=list)
    extended_type: bytes = b""      # uuid boxes
    to_eof: bool = False            # declared with size == 0

    @property
    def end(self) -> int:
        return self.offset + self.size

    @property
    def is_container(self) -> bool:
        return self.type in CONTAINERS or self.type in FULL_CONTAINERS

    def find(self, path: bytes) -> Box | None:
        """First descendant at a slash-separated path, e.g. b"moov/udta/meta"."""
        parts = path.split(b"/")
        node = self
        for p in parts:
            node = next((c for c in node.children if c.type == p), None)
            if node is None:
                return None
        return node

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()


def parse(data: bytes, start: int = 0, end: int | None = None,
          depth: int = 0) -> list[Box]:
    """Parse a box sequence in [start, end). Fails closed on anything malformed."""
    if end is None:
        end = len(data)
    if depth > 32:
        raise ParseError("ISOBMFF: box nesting too deep")

    boxes: list[Box] = []
    pos = start
    while pos < end:
        if pos + HEADER_MIN > end:
            raise ParseError(f"ISOBMFF: truncated box header at {pos}")
        size = int.from_bytes(data[pos:pos + 4], "big")
        btype = data[pos + 4:pos + 8]
        header_len = HEADER_MIN
        to_eof = False

        if size == 1:
            if pos + 16 > end:
                raise ParseError(f"ISOBMFF: truncated largesize at {pos}")
            size = int.from_bytes(data[pos + 8:pos + 16], "big")
            header_len = 16
        elif size == 0:
            size = end - pos          # runs to the end of the enclosing range
            to_eof = True

        ext = b""
        if btype == b"uuid":
            if pos + header_len + 16 > end:
                raise ParseError(f"ISOBMFF: truncated uuid at {pos}")
            ext = data[pos + header_len:pos + header_len + 16]
            header_len += 16

        if size < header_len or pos + size > end:
            raise ParseError(
                f"ISOBMFF: box {btype!r} at {pos} declares size {size}, "
                f"which overruns its container")

        box = Box(type=btype, offset=pos, size=size, header_len=header_len,
                  extended_type=ext, to_eof=to_eof)
        body_start = pos + header_len
        body_end = pos + size

        if btype in FULL_CONTAINERS:
            # version+flags, then children.
            if body_start + 4 > body_end:
                raise ParseError(f"ISOBMFF: short full-box {btype!r} at {pos}")
            box.payload = data[body_start:body_start + 4]
            box.header_len += 4
            box.children = parse(data, body_start + 4, body_end, depth + 1)
        elif btype in CONTAINERS:
            box.children = parse(data, body_start, body_end, depth + 1)
        else:
            box.payload = data[body_start:body_end]

        boxes.append(box)
        pos += size
    return boxes


def serialize(boxes: list[Box]) -> bytes:
    """Rebuild bytes from a (possibly edited) box list, recomputing every size.

    Sizes are always recomputed rather than trusted: an edit that changes a payload
    but leaves a stale size produces a file that parses until it doesn't.
    """
    out = bytearray()
    for b in boxes:
        body = serialize(b.children) if b.children else b""
        if b.type in FULL_CONTAINERS:
            body = b.payload + body           # version/flags precede children
        elif not b.children:
            body = b.payload

        # 8-byte header, plus 16 for a uuid's extended type. Largesize is only
        # emitted when genuinely needed, so output does not gratuitously differ
        # from what a normal muxer writes.
        header_len = HEADER_MIN + (16 if b.type == b"uuid" else 0)
        total = header_len + len(body)
        if total > 0xFFFFFFFF:
            header_len += 8
            total = header_len + len(body)
            out += (1).to_bytes(4, "big") + b.type
            if b.type == b"uuid":
                out += b.extended_type
            out += total.to_bytes(8, "big")
        else:
            out += total.to_bytes(4, "big") + b.type
            if b.type == b"uuid":
                out += b.extended_type
        out += body
    return bytes(out)


def total_size(boxes: list[Box]) -> int:
    return len(serialize(boxes))


def shift_chunk_offsets(boxes: list[Box], delta: int) -> int:
    """Add `delta` to every absolute chunk offset in `stco` / `co64`.

    This is the whole reason the module exists. Those tables point at the audio
    chunks by absolute file offset, so any box removed ahead of `mdat` moves the
    audio and leaves every entry pointing into the wrong place — a file that still
    parses and still reports the right duration, but decodes to garbage. Returns the
    number of tables patched so a caller can assert it did not silently do nothing.
    """
    patched = 0
    for root in boxes:
        for box in root.walk():
            if box.type == b"stco":
                box.payload = _shift_table(box.payload, delta, 4)
                patched += 1
            elif box.type == b"co64":
                box.payload = _shift_table(box.payload, delta, 8)
                patched += 1
    return patched


def _shift_table(payload: bytes, delta: int, width: int) -> bytes:
    # Full box: version(1) + flags(3) + entry_count(4), then entry_count offsets.
    if len(payload) < 8:
        raise ParseError("ISOBMFF: short chunk-offset table")
    count = int.from_bytes(payload[4:8], "big")
    need = 8 + count * width
    if need > len(payload):
        raise ParseError(
            f"ISOBMFF: chunk-offset table claims {count} entries, payload holds "
            f"{(len(payload) - 8) // width}")
    out = bytearray(payload[:8])
    for i in range(count):
        at = 8 + i * width
        value = int.from_bytes(payload[at:at + width], "big")
        shifted = value + delta
        if shifted < 0:
            raise ParseError("ISOBMFF: chunk offset would go negative")
        out += shifted.to_bytes(width, "big")
    out += payload[need:]                      # trailing bytes, if any
    return bytes(out)


# --------------------------------------------------------------------------- #
# The shared F1 engine
# --------------------------------------------------------------------------- #
# M4A, MP4 and HEIC are the same container, and a bit-preserving strip of any of
# them is the same five steps: drop the metadata boxes, blank the fields that are
# metadata living inside structural boxes, measure how far the media moved,
# patch every absolute chunk offset by that delta, and refuse unless the media
# bytes come out identical.
#
# This lives here rather than in a format package because CLAUDE.md's rule is that
# a shared module is written once and called by every handler -- "a missed copy is
# a leak". That was not hypothetical: M4A F1 was written before MP4's M0 measured
# `hdlr` names, so it dropped every tag and left `Core Media Audio` sitting in an
# AVFoundation-muxed file. Copy-pasting it into MP4 would have reproduced the leak
# in a second format and left the first one broken.

# Boxes whose first fields after version+flags are creation/modification times.
# Easy to miss precisely because they are structural boxes rather than tags: a
# tag-oriented scrubber leaves all three and the file still says when it was made.
# There are THREE, not two -- `mdhd` carries a per-track media time that ExifTool
# reports as MediaCreateDate, and a two-track file therefore stamps the same second
# in ten separate fields.
TIMESTAMP_BOXES = {b"mvhd", b"tkhd", b"mdhd"}

# Offset of a handler box's human-readable name: version+flags(4), predefined(4),
# handler type(4), reserved(12).
HDLR_NAME_AT = 24


def zero_timestamps(payload: bytes, btype: bytes = b"") -> bytes:
    """Zero creation_time and modification_time, keeping every other field.

    Version 0 stores them as 32-bit and version 1 as 64-bit, both immediately after
    the 4-byte version+flags word — so the width depends on a byte that must be
    read rather than assumed. Reading a version-1 box as version 0 does not fail;
    it silently rewrites half of one timestamp and the top half of another.
    """
    if len(payload) < 4:
        raise ParseError(f"ISOBMFF: short {btype!r}")
    width = 8 if payload[0] == 1 else 4
    if len(payload) < 4 + width * 2:
        raise ParseError(f"ISOBMFF: {btype!r} too short for its timestamps")
    return payload[:4] + b"\x00" * (width * 2) + payload[4 + width * 2:]


def blank_handler_name(payload: bytes) -> bytes:
    """Empty a `hdlr` box's trailing name, keeping the handler TYPE intact.

    The name is defined by the spec as human-readable text "for debugging and
    inspection"; nothing decodes with it. What it actually carries is the writing
    framework: ffmpeg writes `VideoHandler`/`SoundHandler`, AVFoundation writes
    `Core Media Video`/`Core Media Audio` (measured, docs/p4_media_plan.md §5). It
    is not a tag, so no tag-oriented tool removes it — `exiftool -all=` leaves it
    untouched, and so did this project's own M4A F1 until MP4 went looking.

    The handler type at [8:12] is NOT touched: that is what tells a reader whether
    the track is video or sound.
    """
    if len(payload) < HDLR_NAME_AT:
        return payload
    return payload[:HDLR_NAME_AT] + b"\x00"


def mdat_payload_offset(boxes: list[Box]) -> int | None:
    """Where the `mdat` PAYLOAD lands once this tree is serialized.

    This PREDICTS the output layout, so it reconstructs the header the serializer
    will write. It must never be used to measure the INPUT: a box's header length
    is a property of the file, not of its size, and the two disagree exactly when
    it matters most — see `mdat_payload_offset_of_input()`.
    """
    pos = 0
    for b in boxes:
        if b.type == b"mdat":
            header = HEADER_MIN
            if header + len(b.payload) > 0xFFFFFFFF:
                header += 8                      # largesize form
            return pos + header
        pos += len(serialize([b]))
    return None


def mdat_payload_offset_of_input(boxes: list[Box]) -> int | None:
    """Where the `mdat` payload ACTUALLY sits in the file we parsed.

    Read from the box's own `header_len` rather than reconstructed, because a
    writer may use the 64-bit largesize form for a box that would fit in 32 bits
    and nothing in the format forbids it. **AVFoundation does exactly that**: it
    writes `mdat` with `size == 1` and a 64-bit length for a 17 KB box, so the
    payload begins 16 bytes after the header start, not 8.

    Reconstructing the header here instead of reading it is a bug that hides
    perfectly. The predicted "before" and the predicted "after" agree, the delta
    comes out zero, no chunk offset is patched, the media bytes are byte-identical,
    the container parses, the duration is right — and every chunk offset now points
    8 bytes into the wrong place, so the file decodes to noise. That was shipped in
    M4A F1 from Phase 2 until MP4's M3 decoded an Apple-muxed file for the first
    time.
    """
    for b in boxes:
        if b.type == b"mdat":
            return b.offset + b.header_len
    return None


def strip_tree(boxes: list[Box], drop_types: set[bytes], *,
               blank_handler_names: bool = False) -> list[Box]:
    """Drop the metadata boxes and blank metadata fields inside structural ones."""
    kept = []
    for b in boxes:
        if b.type in drop_types:
            continue
        if b.children:
            b.children = strip_tree(b.children, drop_types,
                                    blank_handler_names=blank_handler_names)
        if b.type in TIMESTAMP_BOXES:
            b.payload = zero_timestamps(b.payload, b.type)
        elif blank_handler_names and b.type == b"hdlr":
            b.payload = blank_handler_name(b.payload)
        kept.append(b)
    return kept


# The top level of a non-fragmented file, on the KEEP side. `drop_types` is a
# denylist, and a denylist passes whatever nobody thought to name: WhatsApp writes
# a proprietary 24-byte `beam` box beside `moov` in every video it sends, and it
# came through F1 untouched while the scrub reported success. So the top level is
# decided the way HEIC's auxiliary images are — by what is kept — and anything else
# goes, which is the direction an unseen box should fail in.
TOP_LEVEL_KEEP = frozenset({b"ftyp", b"moov", b"mdat"})

# Except these, which are refused rather than dropped: each carries or indexes
# media OUTSIDE `moov`/`mdat` (fragment runs and their indexes), so dropping one
# deletes picture or sound while every check below — which reads `stco`/`co64` —
# still passes. That is the one outcome worse than a refusal.
TOP_LEVEL_REFUSE = frozenset({b"moof", b"mfra", b"sidx", b"ssix", b"styp",
                              b"emsg", b"prft"})


def strip_and_repack(data: bytes, drop_types: set[bytes], *, label: str,
                     blank_handler_names: bool = False) -> bytes:
    """A bit-preserving metadata strip of any ISOBMFF file.

    THE TRAP, stated once for every format that calls this: `stco`/`co64` hold
    **absolute file offsets** into `mdat`. Removing any box ahead of `mdat` slides
    the media, and a file with stale offsets still parses, still reports the right
    duration, and decodes to garbage. So the media bytes are captured first, the
    move is measured rather than predicted, and the result is refused unless those
    exact bytes come back out.

    `drop_types` applies at every depth; the top level is additionally held to
    `TOP_LEVEL_KEEP`, so a box outside both lists is dropped there, not kept.
    """
    boxes = parse(data)
    if not any(b.type == b"ftyp" for b in boxes):
        raise ParseError(f"{label}: no ftyp box")
    refused = sorted({b.type for b in boxes} & TOP_LEVEL_REFUSE)
    if refused:
        raise ParseError(
            f"{label}: top-level {', '.join(t.decode('latin-1') for t in refused)} "
            "carries or indexes media outside moov/mdat (a fragmented file) -- "
            "refusing rather than dropping media no chunk table accounts for")
    boxes = [b for b in boxes if b.type in TOP_LEVEL_KEEP]
    mdat = next((b for b in boxes if b.type == b"mdat"), None)
    if mdat is None:
        raise ParseError(f"{label}: no mdat box (no media to preserve)")
    media = mdat.payload
    # Measured from the parsed box, never reconstructed. See the docstring there.
    before = mdat_payload_offset_of_input(boxes)
    # Captured BEFORE stripping, because `strip_tree` edits these boxes in place:
    # reading them afterwards would compare the patched tables against themselves
    # and agree no matter what the patch did.
    chunks_before = chunk_offset_tables(boxes)

    stripped = strip_tree(boxes, drop_types,
                          blank_handler_names=blank_handler_names)
    after = mdat_payload_offset(stripped)
    if after is None:
        raise ScrubError(f"{label}: mdat vanished during strip")

    delta = after - before
    if delta:
        patched = shift_chunk_offsets(stripped, delta)
        if patched == 0:
            # The media moved and nothing was patched. We cannot know there were
            # no tables -- only that we found none -- so every offset that does
            # exist is now wrong. Refuse rather than emit it.
            raise ScrubError(
                f"{label}: media moved by {delta} bytes but no stco/co64 table "
                "was found to patch — refusing to emit a file whose sample "
                "tables may point at the wrong bytes")
        # Patching cannot change a table's size (same entry count, same width), so
        # the offset measured above still holds. Assert rather than assume.
        if mdat_payload_offset(stripped) != after:
            raise ScrubError(f"{label}: patching chunk offsets moved mdat again")

    out = serialize(stripped)
    check = parse(out)
    out_mdat = next((b for b in check if b.type == b"mdat"), None)
    if out_mdat is None or out_mdat.payload != media:
        raise ScrubError(f"{label} F1 altered the media samples")

    # The check that would have caught the largesize bug on the day it was
    # written. Preserving the media BYTES is not the promise -- the promise is
    # that the sample tables still point AT them. So: every chunk offset in the
    # input is followed into the output and the bytes there must match. A file
    # that keeps its media and loses its pointers parses, plays, reports the right
    # duration, and decodes to noise, which is indistinguishable from success by
    # every check that does not do this one.
    _verify_chunks_still_point_at_the_media(
        data, chunks_before, out, chunk_offset_tables(check), label)
    return out


def chunk_offset_tables(boxes: list[Box]) -> list[list[int]]:
    """Every `stco`/`co64` table's offsets, in tree order."""
    found = []
    for root in boxes:
        for b in root.walk():
            if b.type in (b"stco", b"co64"):
                width = 4 if b.type == b"stco" else 8
                count = int.from_bytes(b.payload[4:8], "big")
                found.append([
                    int.from_bytes(b.payload[8 + i * width:
                                             8 + (i + 1) * width], "big")
                    for i in range(count)])
    return found


def _verify_chunks_still_point_at_the_media(src: bytes, before: list[list[int]],
                                            out: bytes, after: list[list[int]],
                                            label: str, probe: int = 64) -> None:
    if len(before) != len(after):
        raise ScrubError(f"{label}: chunk tables appeared or vanished")
    # strict: a silently truncating zip would let a table-count mismatch
    # through the very check that exists to catch pointer damage.
    for old_offsets, new_offsets in zip(before, after, strict=True):
        if len(old_offsets) != len(new_offsets):
            raise ScrubError(f"{label}: a chunk table changed length")
        for old, new in zip(old_offsets, new_offsets, strict=True):
            if src[old:old + probe] != out[new:new + probe]:
                raise ScrubError(
                    f"{label}: chunk offset {old} -> {new} no longer points at "
                    "the same media -- the file would parse and decode to noise")


def canonical_handler_boxes(handlers: tuple[bytes, ...] = (b"soun", b"vide",
                                                           b"mdir", b"mdta")
                            ) -> list[bytes]:
    """The exact `hdlr` boxes a blanked-name strip emits, one per handler type.

    Declared to the scrubber-fingerprint guard as a mandatory constant, and
    **generated here rather than transcribed** so it cannot drift from what the
    strip actually writes — the same discipline DOCX's empty-`_rels` declaration
    follows.

    Why a declaration is the right answer rather than a fix. Blanking the handler
    name removes a producer fingerprint (`Core Media Video` vs `VideoHandler`),
    and everything else in a `hdlr` box is fixed by the format, so every file we
    emit ends up with a byte-identical box. The guard sees a constant this tool
    introduced and says so, correctly. FLAC's rule is to omit a constant rather
    than normalize it, but there is nothing to omit here: `hdlr` is required and
    the remaining fields are the format's, not ours. So this is limit #9 again —
    the output is marked as canonically rewritten, never as rewritten *from what*
    — and the alternative is strictly worse: keeping the name tells an adversary
    the file came off a Mac.
    """
    out = []
    for handler in handlers:
        payload = b"\x00" * 4 + b"\x00" * 4 + handler + b"\x00" * 12 + b"NAME"
        blanked = blank_handler_name(payload)
        box = serialize([Box(type=b"hdlr", offset=0,
                             size=HEADER_MIN + len(blanked),
                             header_len=HEADER_MIN, payload=blanked)])
        # Padded by four zero bytes each side: `mdhd`'s `predefined` field before
        # it, and the high bytes of the following box's size word after it, both
        # zero by the spec rather than by a producer's choice, so no corpus can
        # vary them away. Four rather than exactly-as-many-as-observed because
        # the count differs by format -- M4A's run carries two trailing zeros and
        # MP4's three -- and tuning a pad per format would be a constant that
        # drifts. The guard reports MAXIMAL runs, so the declaration has to cover
        # the run rather than just the box. The guard reports
        # MAXIMAL runs, so without them the declaration does not cover the run
        # the box actually sits in. Everything on the OTHER side is a producer's
        # choice and is broken by corpus diversity instead of declared -- see
        # `gen_matrix_m4a._diverse`, where duration varies specifically to stop
        # the following `minf` size from being common too. The bound on this
        # declaration is the test asserting it carries no locus at all.
        out.append(b"\x00" * 4 + box + b"\x00" * 4)
    return out
