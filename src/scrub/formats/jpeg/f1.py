"""JPEG F1 — bit-preserving metadata strip via segment surgery.

F1 keeps the entropy-coded image bitstream byte-for-byte and rebuilds the marker
structure from a **keep-list** (the benchmark whitelist approach, done right):
copy only the segments that define the image, drop everything else. A keep-list
fails safe — an APPn marker we've never heard of is dropped, not passed through
(a drop-list would leak it). JPEG has no checksums, so F1 is pure byte surgery
over big-endian length fields; no integrity recomputation (that arrives with PNG
CRCs).

Keep / drop / rewrite decision (p1 plan W4):
  KEEP verbatim   content markers — SOI, EOI, DQT, DHT, DRI, DAC, all SOF*, and
                  SOS + its entropy data (F1 = coefficients untouched).
  REWRITE canonical
    app0_jfif     replace with a fixed no-thumbnail JFIF (drops any JFIF/JFXX
                  thumbnail and any density-based source fingerprint).
    app14_adobe   keep the colour-transform byte (dropping it would misrender
                  YCCK/CMYK — a content violation), canonicalize the rest.
  DROP            everything else — APP1 Exif/XMP, APP2 ICC/MPF, APP13
                  Photoshop/IPTC, COM, all other APPn, and the post-EOI trailer.

ICC note: default policy is strip (drop APP2 ICC). Correct for the sRGB-ish
common case; wide-gamut images whose pixels need the profile are the E7
sanitize-vs-strip exception, not yet wired here.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import segments as seg

# Canonical no-thumbnail JFIF APP0 (full segment incl. marker + length):
#   FF E0 | len=0x0010 | "JFIF\0" | ver 1.01 | units=0 (aspect) | Xd=1 Yd=1 |
#   Xthumb=0 Ythumb=0 (no embedded thumbnail).
CANONICAL_JFIF = (
    b"\xff\xe0"          # APP0 marker
    b"\x00\x10"          # length = 16
    b"JFIF\x00"          # identifier
    b"\x01\x01"          # version 1.01
    b"\x00"              # density units = 0 (no units, aspect ratio)
    b"\x00\x01\x00\x01"  # Xdensity=1, Ydensity=1
    b"\x00\x00"          # Xthumbnail=0, Ythumbnail=0
)
assert len(CANONICAL_JFIF) == 18

# Adobe APP14 payload template: "Adobe" | ver=100 | flags0=0 | flags1=0 | <T>
_ADOBE_PREFIX = b"Adobe\x00\x64\x00\x00\x00\x00"  # 11 bytes; transform appended


@dataclass
class SegmentAction:
    segment: seg.Segment
    action: str                 # "keep" | "rewrite" | "drop"
    reason: str
    replacement: bytes | None = None   # full segment bytes for "rewrite"


def _canonical_adobe(payload: bytes) -> bytes:
    """Full canonical APP14 segment preserving only the transform byte."""
    transform = payload[11:12] if len(payload) >= 12 else b"\x00"
    body = _ADOBE_PREFIX + transform            # 12 bytes
    length = (2 + len(body)).to_bytes(2, "big")
    return b"\xff\xee" + length + body


def plan(structure: seg.JpegStructure) -> list[SegmentAction]:
    """Decide keep/rewrite/drop for every segment. Separated from emission so
    tests and the harness annotate() can inspect the decision."""
    actions: list[SegmentAction] = []
    for s in structure.segments:
        if s.is_content:
            actions.append(SegmentAction(s, "keep", f"content:{s.kind}"))
        elif s.kind == "app0_jfif":
            actions.append(SegmentAction(s, "rewrite", "canonical_jfif_no_thumbnail",
                                        replacement=CANONICAL_JFIF))
        elif s.kind == "app0_jfxx":
            actions.append(SegmentAction(s, "drop", "jfif_thumbnail_extension"))
        elif s.kind == "app14_adobe":
            actions.append(SegmentAction(s, "rewrite", "canonical_adobe_transform",
                                        replacement=_canonical_adobe(s.payload)))
        else:
            actions.append(SegmentAction(s, "drop", f"metadata:{s.kind}"))
    return actions


def scrub(data: bytes) -> bytes:
    """Rebuild the JPEG keeping only image-defining bytes. Trailer is dropped by
    construction (we stop after EOI)."""
    structure = seg.walk(data)
    out = bytearray()
    for act in plan(structure):
        if act.action == "keep":
            out += data[act.segment.offset:act.segment.end]
        elif act.action == "rewrite":
            out += act.replacement
        # "drop" contributes nothing
    return bytes(out)


# Segment kinds F1 output is allowed to contain (everything else is a residual).
_ALLOWED_KINDS = seg.CONTENT_MARKERS  # matched by marker, not kind string


def residuals(data: bytes) -> list[str]:
    """Re-walk scrubbed output and report anything that shouldn't survive F1: a
    metadata segment, a non-canonical JFIF/APP14, or trailer bytes. Empty list =
    clean. Called by the CLI as a fail-closed post-condition."""
    out: list[str] = []
    structure = seg.walk(data)
    for s in structure.segments:
        if s.marker in _ALLOWED_KINDS:
            continue
        if s.kind == "app0_jfif":
            if data[s.offset:s.end] != CANONICAL_JFIF:
                out.append(f"non-canonical JFIF at {s.offset}")
            continue
        if s.kind == "app14_adobe":
            if data[s.offset:s.end] != _canonical_adobe(s.payload):
                out.append(f"non-canonical APP14 at {s.offset}")
            continue
        out.append(f"residual {s.kind} segment at {s.offset}")
    if structure.trailer:
        out.append(f"{len(structure.trailer)} trailer byte(s) after EOI")
    return out
