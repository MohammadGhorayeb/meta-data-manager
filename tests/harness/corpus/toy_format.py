"""TOYF: minimal container that cleanly separates content from metadata.
Layout:  b"TOYF" | content_len:uint32-BE | content | metadata
metadata := zero+ TLV records to EOF:  type:uint8 | length:uint16-BE | value[length]
Content-identity of two TOYF files == identical content bytes (metadata may differ).
"""
from __future__ import annotations

import struct

MAGIC = b"TOYF"


def pack(content: bytes, records: list[tuple[int, bytes]]) -> bytes:
    out = bytearray(MAGIC)
    out += struct.pack(">I", len(content))
    out += content
    for t, v in records:
        if not (0 <= t <= 255): raise ValueError("type out of range")
        if len(v) > 0xFFFF: raise ValueError("value too long")
        out += struct.pack(">BH", t, len(v)) + v
    return bytes(out)


def unpack(blob: bytes) -> tuple[bytes, list[tuple[int, bytes]]]:
    if blob[:4] != MAGIC: raise ValueError("bad magic")
    (clen,) = struct.unpack(">I", blob[4:8])
    content = blob[8:8 + clen]
    rest, i, records = blob[8 + clen:], 0, []
    while i < len(rest):
        t, ln = struct.unpack(">BH", rest[i:i + 3]); i += 3
        records.append((t, rest[i:i + ln])); i += ln
    return content, records


def read(path: str):
    with open(path, "rb") as f: return unpack(f.read())


def write(path: str, content: bytes, records: list[tuple[int, bytes]]) -> None:
    with open(path, "wb") as f: f.write(pack(content, records))
