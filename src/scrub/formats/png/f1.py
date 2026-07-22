"""PNG F1 — bit-preserving metadata strip via chunk keep-list surgery.

F1 keeps the compressed image bitstream (IDAT) byte-for-byte and rebuilds the
file from a keep-list: copy only the chunks that define the image or affect how
it renders; drop everything else. A keep-list fails safe — an unknown/private
chunk we've never heard of is dropped, not passed through (a drop-list would leak
it). Kept chunks are copied verbatim; PNG CRCs are per-chunk, so dropping a chunk
never invalidates another and nothing is recomputed (p1 plan W7).

Keep / drop decision (p1 plan W7):
  KEEP  IHDR, PLTE, IDAT, IEND               — structure + pixels (critical).
        tRNS, gAMA, cHRM, sRGB, sBIT, bKGD   — render-affecting ancillary.
        acTL, fcTL, fdAT                      — APNG animation = content.
  DROP  tEXt, zTXt, iTXt, tIME, eXIf, iCCP, pHYs, sPLT, hIST, oFFs, sCAL, sTER,
        every private/unknown chunk, and any post-IEND trailer.

ICC note: default policy is strip (drop iCCP), matching the JPEG ICC policy —
correct for the sRGB-ish common case; wide-gamut sanitize-vs-strip is the E7
exception, not wired here.
"""
from __future__ import annotations

from . import chunks as ck

# Chunks that define the image or affect rendering — everything else is metadata.
KEEP = frozenset({
    "IHDR", "PLTE", "IDAT", "IEND",              # critical
    "tRNS", "gAMA", "cHRM", "sRGB", "sBIT", "bKGD",  # render-affecting
    "acTL", "fcTL", "fdAT",                      # APNG animation (content)
})


def scrub(data: bytes) -> bytes:
    """Rebuild the PNG keeping only image-defining / render-affecting chunks,
    each copied verbatim. Trailer is dropped by construction."""
    structure = ck.walk(data)
    out = bytearray(ck.PNG_SIGNATURE)
    for c in structure.chunks:
        if c.ctype in KEEP:
            out += data[c.offset:c.end]   # verbatim, CRC included
    return bytes(out)


def residuals(data: bytes) -> list[str]:
    """Re-walk scrubbed output and report anything that shouldn't survive F1: a
    non-keep (metadata) chunk or trailer bytes. Empty list = clean. Called by the
    CLI as a fail-closed post-condition."""
    out: list[str] = []
    structure = ck.walk(data)
    for c in structure.chunks:
        if c.ctype not in KEEP:
            out.append(f"residual {c.ctype} chunk at {c.offset}")
    if structure.trailer:
        out.append(f"{len(structure.trailer)} trailer byte(s) after IEND")
    return out
