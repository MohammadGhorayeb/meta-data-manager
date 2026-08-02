"""XMP + ExtendedXMP detection.

XMP is an RDF/XML packet Adobe embeds in almost every format. It is a duplicate
locus by design: it re-states EXIF/IPTC fields (CreateDate, GPS, creator,
software) in XML, so a scrubber that clears EXIF but leaves XMP leaks the same
data (CLAUDE.md hard-constraint #2: redundant copies).

Where it hides, per container:
  JPEG   APP1 whose payload starts with the standard XMP namespace URI + NUL.
         Payloads > ~65 KB overflow one APP1, so Adobe splits the tail into
         **ExtendedXMP**: multiple APP1s keyed by a 32-char GUID, each carrying
         (GUID | full-length u32 | offset u32 | chunk), reassembled by offset.
  PNG    an iTXt chunk with keyword "XML:com.adobe.xmp".
  (PDF / TIFF / etc. reuse the same packet; handlers pass us the raw packet.)

Phase-1 policy is drop the whole XMP payload, so this module *detects and
delimits*; it does not edit XMP XML. Reassembly matters because a scrubber that
only removes the primary XMP APP1 and misses the ExtendedXMP chunks leaves most
of the payload behind — exactly the "named-tag" failure the corpus L5 case
targets.
"""
from __future__ import annotations

from dataclasses import dataclass

# APP1 payload signatures (include the trailing NUL that terminates the id).
XMP_SIG = b"http://ns.adobe.com/xap/1.0/\x00"
EXT_XMP_SIG = b"http://ns.adobe.com/xmp/extension/\x00"

# PNG iTXt keyword that carries XMP.
PNG_XMP_KEYWORD = b"XML:com.adobe.xmp"

# Packet delimiters (XMP spec part 1, §7.3.2). 'begin' may carry a BOM.
_PACKET_BEGIN = b"<?xpacket begin="
_PACKET_END = b"<?xpacket end="
# Bare meta element when no processing-instruction wrapper is present.
_META_OPEN = b"<x:xmpmeta"
_META_CLOSE = b"</x:xmpmeta>"


@dataclass
class ExtendedChunk:
    guid: bytes          # 32 ASCII hex chars
    full_length: int
    offset: int
    data: bytes


@dataclass
class XmpFinding:
    kind: str                          # "standard" | "extended"
    payload: bytes                     # the bytes after the signature
    guid: str | None = None         # extended only
    chunk: ExtendedChunk | None = None  # extended only


def is_standard(app1_payload: bytes) -> bool:
    return app1_payload.startswith(XMP_SIG)


def is_extended(app1_payload: bytes) -> bool:
    return app1_payload.startswith(EXT_XMP_SIG)


def parse_extended_segment(app1_payload: bytes) -> ExtendedChunk:
    """Split one ExtendedXMP APP1 payload into its GUID/length/offset/data.
    Layout after the signature: 32-byte GUID | u32 full-length | u32 offset |
    chunk-data (both integers big-endian, XMP spec part 3 §1.1.3.1)."""
    body = app1_payload[len(EXT_XMP_SIG):]
    if len(body) < 40:
        raise ValueError("ExtendedXMP segment too short for GUID+len+offset")
    guid = body[:32]
    full_length = int.from_bytes(body[32:36], "big")
    offset = int.from_bytes(body[36:40], "big")
    data = body[40:]
    return ExtendedChunk(guid=guid, full_length=full_length, offset=offset, data=data)


def reassemble_extended(chunks: list[ExtendedChunk]) -> dict[bytes, bytes]:
    """Reassemble ExtendedXMP payloads grouped by GUID, ordering fragments by
    their declared offset. Returns {guid: reassembled_bytes}. A gap or overlap
    (fragment offset != running length) marks the payload incomplete but we
    still return what we have — the handler drops all of it regardless."""
    by_guid: dict[bytes, list[ExtendedChunk]] = {}
    for c in chunks:
        by_guid.setdefault(c.guid, []).append(c)
    out: dict[bytes, bytes] = {}
    for guid, cs in by_guid.items():
        cs.sort(key=lambda c: c.offset)
        buf = bytearray()
        for c in cs:
            buf += c.data
        out[guid] = bytes(buf)
    return out


def find_packet_span(buf: bytes) -> tuple[int, int] | None:
    """Locate an XMP packet within an arbitrary buffer (used when a container
    embeds the packet without our signature wrapper). Returns (start, end) of
    the widest recognizable span, or None."""
    begin = buf.find(_PACKET_BEGIN)
    if begin != -1:
        end_pi = buf.find(_PACKET_END, begin)
        if end_pi != -1:
            close = buf.find(b"?>", end_pi)
            if close != -1:
                return begin, close + 2
    # Fall back to the bare meta element.
    mo = buf.find(_META_OPEN)
    if mo != -1:
        mc = buf.find(_META_CLOSE, mo)
        if mc != -1:
            return mo, mc + len(_META_CLOSE)
    return None
