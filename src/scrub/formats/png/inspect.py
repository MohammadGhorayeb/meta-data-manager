"""What a PNG's metadata says — for the scrub report.

PNG is the easy case and worth contrasting with JPEG: its metadata lives in named,
length-delimited ancillary chunks rather than being threaded through the same
segment space as the picture, which is the same separability that makes PNG's A2
defense free at F2.
"""
from __future__ import annotations

import zlib

from . import chunks as ck

# Chunks that decide how the image renders. Present in the report as kept, never
# removed: dropping one changes the picture.
RENDER_CRITICAL = {"IHDR", "PLTE", "IDAT", "IEND", "tRNS", "gAMA", "cHRM", "sRGB",
                   "sBIT", "bKGD", "pHYs", "acTL", "fcTL", "fdAT"}

_LABEL = {"tIME": "last-modified time", "eXIf": "EXIF block",
          "iCCP": "ICC profile", "sPLT": "suggested palette",
          "hIST": "palette histogram"}


def _text(chunk: ck.Chunk) -> tuple[str, str] | None:
    """A PNG text chunk as (keyword, value). The three spellings differ only in
    compression and encoding, so they are decoded into one shape."""
    data = chunk.data
    try:
        if chunk.ctype == "tEXt":
            key, _, val = data.partition(b"\x00")
            return key.decode("latin-1"), val.decode("latin-1", "replace")
        if chunk.ctype == "zTXt":
            key, _, rest = data.partition(b"\x00")
            return (key.decode("latin-1"),
                    zlib.decompress(rest[1:]).decode("latin-1", "replace"))
        if chunk.ctype == "iTXt":
            key, _, rest = data.partition(b"\x00")
            compressed = rest[:1] == b"\x01"
            body = rest[2:]
            for _ in range(2):                    # language tag, translated keyword
                _, _, body = body.partition(b"\x00")
            text = zlib.decompress(body) if compressed else body
            return key.decode("latin-1"), text.decode("utf-8", "replace")
    except Exception:                                     # noqa: BLE001
        return chunk.ctype, f"({len(data)} bytes, undecodable)"
    return None


def describe(data: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    structure = ck.walk(data)
    seen: dict[str, int] = {}

    for chunk in structure.chunks:
        if chunk.ctype in RENDER_CRITICAL:
            continue
        pair = _text(chunk)
        if pair is not None:
            key, value = pair
            # Two chunks can share a keyword; number them rather than losing one.
            seen[key] = seen.get(key, 0) + 1
            suffix = f" #{seen[key]}" if seen[key] > 1 else ""
            out[f"{chunk.ctype}:{key}{suffix}"] = value
            continue
        if chunk.ctype == "tIME" and len(chunk.data) == 7:
            y = int.from_bytes(chunk.data[:2], "big")
            m, d, hh, mm, ss = chunk.data[2:7]
            out["tIME"] = f"{y:04d}-{m:02d}-{d:02d} {hh:02d}:{mm:02d}:{ss:02d}"
            continue
        if chunk.ctype == "iCCP":
            name, _, _ = chunk.data.partition(b"\x00")
            out["iCCP"] = name.decode("latin-1", "replace") or "(unnamed profile)"
            continue
        out[_LABEL.get(chunk.ctype, chunk.ctype)] = f"({len(chunk.data)} bytes)"

    if structure.trailer:
        out["trailing bytes"] = f"({len(structure.trailer)} bytes after IEND)"
    return out
