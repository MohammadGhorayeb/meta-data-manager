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
from itertools import pairwise

from ..errors import ParseError

# Boxes whose payload is a sequence of child boxes rather than data. Anything not
# listed is treated as a leaf: unknown boxes are never descended into, so an
# unrecognised container is preserved whole rather than half-parsed.
CONTAINERS = {
    b"moov", b"trak", b"edts", b"mdia", b"minf", b"dinf", b"stbl", b"udta",
    b"mvex", b"moof", b"traf", b"mfra", b"skip", b"strk", b"ilst",
}

# `meta` is a container, but in ISO 14496-12 a FULL box: 4 bytes of version/flags
# precede its children. Parsing it as a plain container yields garbage children.
#
# QuickTime disagrees. There `moov/meta` and `trak/meta` are PLAIN containers, and
# reading one as a full box skips 4 bytes into the first child's header: every iPhone
# video failed with "declares size 1751411826", which is ASCII `hdlr` read as a size.
# Neither layout is tied to a brand, so the parser decides per box from the bytes
# (`_quicktime_meta`) and records the answer as an EMPTY payload -- an ISO `meta`
# always carries its 4 version/flags bytes there -- so the serializer writes back the
# dialect it read.
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

    @property
    def quicktime_meta(self) -> bool:
        """A `meta` read in the QuickTime dialect: no version/flags before its
        children. Only meaningful for boxes that came out of `parse()`."""
        return self.type in FULL_CONTAINERS and not self.payload

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

        if btype in FULL_CONTAINERS and _quicktime_meta(data, body_start, body_end):
            # QuickTime: children start at once; the payload stays empty.
            box.children = parse(data, body_start, body_end, depth + 1)
        elif btype in FULL_CONTAINERS:
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


def _quicktime_meta(data: bytes, body_start: int, body_end: int) -> bool:
    """Is this `meta` body QuickTime's plain-container form?

    ISO writes version 0 and flags 0, so its first 4 bytes are zero. QuickTime has
    none, so those 4 bytes are the first child's size -- non-zero, within the body --
    followed by a printable four-character type. Anything that is neither takes the
    ISO path, which parses it or fails closed; it is never guessed into shape.
    """
    if body_end - body_start < HEADER_MIN:
        return False
    first = int.from_bytes(data[body_start:body_start + 4], "big")
    if first == 0:
        return False
    fourcc = data[body_start + 4:body_start + 8]
    return (HEADER_MIN <= first <= body_end - body_start
            and all(0x20 <= c <= 0x7E or c == 0xA9 for c in fourcc))


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


def scan(data: bytes) -> list[Box]:
    """Top-level box HEADERS only: type, offset, size, header length. No payloads.

    `parse()` copies every leaf's payload, and at the top level of a video that leaf
    is `mdat` -- 3.37 GB on the largest real file measured (p4 plan §5.6). A handler
    that only needs to know where the boxes are, or to parse `moov` alone, scans
    first and slices what it needs.
    """
    boxes: list[Box] = []
    pos, end = 0, len(data)
    while pos < end:
        if pos + HEADER_MIN > end:
            raise ParseError(f"ISOBMFF: truncated box header at {pos}")
        size = int.from_bytes(data[pos:pos + 4], "big")
        btype = data[pos + 4:pos + 8]
        header_len, to_eof = HEADER_MIN, False
        if size == 1:
            if pos + 16 > end:
                raise ParseError(f"ISOBMFF: truncated largesize at {pos}")
            size = int.from_bytes(data[pos + 8:pos + 16], "big")
            header_len = 16
        elif size == 0:
            size, to_eof = end - pos, True
        ext = b""
        if btype == b"uuid":
            ext = data[pos + header_len:pos + header_len + 16]
            header_len += 16
        if size < header_len or pos + size > end:
            raise ParseError(
                f"ISOBMFF: box {btype!r} at {pos} declares size {size}, "
                f"which overruns the file")
        boxes.append(Box(type=btype, offset=pos, size=size, header_len=header_len,
                         extended_type=ext, to_eof=to_eof))
        pos += size
    return boxes


@dataclass(frozen=True)
class Chunk:
    """One entry of a track's chunk-offset table, resolved to the bytes it covers."""
    index: int                 # position in the track's stco/co64 table
    offset: int                # absolute file offset
    sample_sizes: tuple[int, ...]

    @property
    def size(self) -> int:
        return sum(self.sample_sizes)

    @property
    def end(self) -> int:
        return self.offset + self.size


def chunks(stbl: Box, limit: int | None = None) -> list[Chunk]:
    """Resolve a sample table into chunks: `stco`/`co64` say where each chunk starts,
    `stsc` says how many samples each holds, `stsz` how big each sample is.

    This is what lets a scrubber say which bytes of `mdat` a track actually uses --
    and therefore which bytes NO track uses. On an iPhone those unreferenced bytes
    hold a stale copy of the metadata, GPS included (p4 plan §5.4).

    Fails closed on anything it does not model: `stz2` (compact sample sizes), a
    table that does not account for every sample, or a `stsc` run that points at a
    chunk that does not exist.

    `limit` is the size of the file the table came from. A fixed-size `stsz` states
    its sample count as a bare 32-bit number with nothing behind it, so a damaged
    one can ask for four billion samples; the count is checked against the bytes
    that could possibly hold them before anything is allocated. Callers holding the
    file pass its length -- a damaged file must be refused, never allocated for.
    """
    kids = {c.type: c for c in stbl.children}
    if b"stz2" in kids:
        raise ParseError("ISOBMFF: compact sample sizes (stz2) are not modelled")
    for need in (b"stsz", b"stsc"):
        if need not in kids:
            raise ParseError(f"ISOBMFF: sample table has no {need.decode()}")
    offsets = _chunk_offsets(kids)

    stsz = kids[b"stsz"].payload
    if len(stsz) < 12:
        raise ParseError("ISOBMFF: short stsz")
    fixed = int.from_bytes(stsz[4:8], "big")
    count = int.from_bytes(stsz[8:12], "big")
    if fixed:
        if limit is not None and fixed * count > limit:
            raise ParseError(
                f"ISOBMFF: stsz declares {count} samples of {fixed} bytes, more "
                f"than the {limit}-byte file can hold")
        sizes = [fixed] * count
    else:
        if len(stsz) < 12 + 4 * count:
            raise ParseError("ISOBMFF: stsz holds fewer sizes than it declares")
        sizes = [int.from_bytes(stsz[12 + 4 * i:16 + 4 * i], "big")
                 for i in range(count)]

    stsc = kids[b"stsc"].payload
    if len(stsc) < 8:
        raise ParseError("ISOBMFF: short stsc")
    runs_n = int.from_bytes(stsc[4:8], "big")
    if len(stsc) < 8 + 12 * runs_n:
        raise ParseError("ISOBMFF: stsc holds fewer runs than it declares")
    runs = [(int.from_bytes(stsc[8 + 12 * i:12 + 12 * i], "big"),
             int.from_bytes(stsc[12 + 12 * i:16 + 12 * i], "big"))
            for i in range(runs_n)]
    if runs and (runs[0][0] != 1 or any(b[0] <= a[0] for a, b in pairwise(runs))):
        raise ParseError("ISOBMFF: stsc runs are not ascending from chunk 1")

    out: list[Chunk] = []
    s, r = 0, 0
    for ci, off in enumerate(offsets):
        while r + 1 < len(runs) and runs[r + 1][0] <= ci + 1:
            r += 1
        per = runs[r][1] if runs else 0
        take = sizes[s:s + per]
        s += len(take)
        out.append(Chunk(index=ci, offset=off, sample_sizes=tuple(take)))
    if s != count:
        raise ParseError(
            f"ISOBMFF: chunk tables account for {s} of {count} samples")
    return out


def _chunk_offsets(kids: dict[bytes, Box]) -> list[int]:
    if (b"stco" in kids) == (b"co64" in kids):
        raise ParseError("ISOBMFF: a sample table needs exactly one of stco/co64")
    box = kids[b"stco"] if b"stco" in kids else kids[b"co64"]
    width = 4 if box.type == b"stco" else 8
    p = box.payload
    if len(p) < 8:
        raise ParseError("ISOBMFF: short chunk-offset table")
    n = int.from_bytes(p[4:8], "big")
    if len(p) < 8 + n * width:
        raise ParseError("ISOBMFF: chunk-offset table holds fewer entries than it "
                         "declares")
    return [int.from_bytes(p[8 + i * width:8 + (i + 1) * width], "big")
            for i in range(n)]


def set_chunk_offsets(stbl: Box, offsets: list[int]) -> None:
    """Rewrite a track's chunk offsets entry by entry.

    The per-chunk sibling of `shift_chunk_offsets()`. A single delta is enough when
    boxes ahead of `mdat` shrink; it is not enough when `mdat` itself is rebuilt from
    only the chunks a scrubber keeps, because each chunk then moves by a different
    amount. Same entry count, same width, so the table's size never changes -- which
    is what lets a caller size `moov` before it knows the offsets.
    """
    box = next((c for c in stbl.children if c.type in (b"stco", b"co64")), None)
    if box is None:
        raise ParseError("ISOBMFF: no chunk-offset table to rewrite")
    width = 4 if box.type == b"stco" else 8
    n = int.from_bytes(box.payload[4:8], "big")
    if n != len(offsets):
        raise ParseError(f"ISOBMFF: {len(offsets)} offsets for a {n}-entry table")
    limit = 1 << (8 * width)
    body = bytearray(box.payload[:8])
    for value in offsets:
        if not 0 <= value < limit:
            raise ParseError(
                f"ISOBMFF: chunk offset {value} does not fit {box.type.decode()}")
        body += value.to_bytes(width, "big")
    body += box.payload[8 + n * width:]
    box.payload = bytes(body)


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
