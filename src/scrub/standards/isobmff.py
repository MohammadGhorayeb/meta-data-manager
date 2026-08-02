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

from ..errors import ParseError

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
