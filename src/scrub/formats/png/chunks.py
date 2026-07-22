"""PNG chunk walker (spec-parse depth) with per-chunk CRC verification.

One honest walk of the chunk stream, shared by the F1/F2 handlers and (later) the
harness plugin. PNG is a length-prefixed chunked format, so parsing is cleaner
than JPEG's marker stream — but it carries a real integrity field (CRC-32 per
chunk), the first in the project. We VERIFY every input CRC and fail closed on a
mismatch: a bad CRC means the file is malformed or tampered, and guessing could
smuggle metadata past us.

Structure (PNG spec / RFC 2083):
  - 8-byte signature: 89 50 4E 47 0D 0A 1A 0A.
  - Then chunks, each: length(4 BE, of DATA only) | type(4 ASCII) | data | crc(4 BE).
  - CRC-32 (ISO 3309) is computed over type+data (NOT length).
  - Type case carries semantics: bit 5 of byte 0 set (lowercase) => ancillary
    (safe to drop); uppercase => critical. IHDR is first, IEND last.
  - Bytes after IEND are a trailer — a first-class metadata locus.
"""
from __future__ import annotations

import zlib
from dataclasses import dataclass

from ...errors import ParseError

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@dataclass
class Chunk:
    ctype: str          # 4-char ASCII type, e.g. "IHDR", "tEXt"
    offset: int         # index of the chunk's length field in the file
    length: int         # total bytes consumed (4 + 4 + datalen + 4)
    data: bytes         # payload only (no length/type/crc)
    crc: int            # the CRC-32 stored in the file
    crc_ok: bool        # whether it matches CRC-32 over type+data

    @property
    def end(self) -> int:
        return self.offset + self.length

    @property
    def is_critical(self) -> bool:
        # Critical chunks have an uppercase first letter (ancillary bit clear).
        return self.ctype[:1].isupper()


@dataclass
class PngStructure:
    chunks: list[Chunk]
    trailer: bytes          # bytes after the IEND chunk
    trailer_offset: int

    def by_type(self, ctype: str) -> list[Chunk]:
        return [c for c in self.chunks if c.ctype == ctype]

    def types(self) -> list[str]:
        return [c.ctype for c in self.chunks]


def walk(data: bytes) -> PngStructure:
    """Parse the whole PNG into ordered chunks + trailer, verifying each CRC.
    Fails closed (ParseError) on any structural violation — an unaccounted region
    could carry metadata."""
    n = len(data)
    if n < 8 or data[:8] != PNG_SIGNATURE:
        raise ParseError("not a PNG (missing 8-byte signature)")

    chunks: list[Chunk] = []
    pos = 8
    saw_iend = False
    while pos < n:
        if saw_iend:
            break  # anything after IEND is trailer, handled below
        if pos + 8 > n:
            raise ParseError(f"truncated chunk header at offset {pos}")
        length = int.from_bytes(data[pos:pos + 4], "big")
        ctype_bytes = data[pos + 4:pos + 8]
        try:
            ctype = ctype_bytes.decode("ascii")
        except UnicodeDecodeError:
            raise ParseError(f"non-ASCII chunk type at offset {pos}")
        if not ctype.isalpha():
            raise ParseError(f"invalid chunk type {ctype!r} at offset {pos}")
        data_start = pos + 8
        data_end = data_start + length
        crc_end = data_end + 4
        if crc_end > n:
            raise ParseError(
                f"chunk {ctype} at {pos} runs past EOF ({crc_end} > {n})")
        payload = data[data_start:data_end]
        stored_crc = int.from_bytes(data[data_end:crc_end], "big")
        computed = zlib.crc32(ctype_bytes + payload) & 0xFFFFFFFF
        chunk = Chunk(ctype=ctype, offset=pos, length=crc_end - pos,
                      data=payload, crc=stored_crc, crc_ok=(stored_crc == computed))
        if not chunk.crc_ok:
            raise ParseError(
                f"CRC mismatch on {ctype} chunk at {pos}: "
                f"stored {stored_crc:#010x} != computed {computed:#010x}")
        chunks.append(chunk)
        pos = crc_end
        if ctype == "IEND":
            saw_iend = True

    if not chunks or chunks[0].ctype != "IHDR":
        raise ParseError("first chunk is not IHDR")
    if not saw_iend:
        raise ParseError("reached EOF without an IEND chunk")

    iend = chunks[-1]
    return PngStructure(chunks=chunks, trailer=data[iend.end:],
                        trailer_offset=iend.end)


def assemble(chunks: list[Chunk]) -> bytes:
    """Rebuild a PNG (signature + chunks) from a chunk list, recomputing each
    CRC so a modified/synthesized chunk is always valid. Emits length | type |
    data | crc for each chunk."""
    out = bytearray(PNG_SIGNATURE)
    for c in chunks:
        ctype = c.ctype.encode("ascii")
        out += len(c.data).to_bytes(4, "big")
        out += ctype
        out += c.data
        out += (zlib.crc32(ctype + c.data) & 0xFFFFFFFF).to_bytes(4, "big")
    return bytes(out)
