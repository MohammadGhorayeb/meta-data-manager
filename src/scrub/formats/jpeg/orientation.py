"""EXIF orientation: the one EXIF field that is content.

A phone held upright stores its picture sideways and adds Orientation = 6 ("rotate
90° to display"); every browser and photo viewer applies it. Measured (2026-10-07,
`docs/p6_tail_plan.md` §9.1): all three JPEG tiers dropped the whole EXIF block, so
such a photo came out **displayed sideways** -- 64x32 where the original showed
32x64 -- in every viewer that honours the tag, and `exiftool -all=` does the same;
MAT2 alone kept it upright, by re-encoding with the rotation applied. The pixel
tests could not see it: Pillow decodes the stored pixels and ignores the tag.

So F1 and F2, which may not touch the pixels, keep the orientation as ONE canonical
EXIF segment holding nothing else -- the same bytes for every producer, so it says
how to display the picture and nothing about who made it, declared to the
fingerprint guard as the mark it is. F3, which re-encodes anyway, applies the
rotation to the pixels and keeps no tag, as MAT2 does.
"""
from __future__ import annotations

import struct

from ...standards import tiff_ifd as t
from . import segments as seg

TAG_ORIENTATION = 0x0112


def read(data: bytes) -> int:
    """The Orientation (1-8) of the first EXIF segment that has one; 1 otherwise."""
    try:
        structure = seg.walk(data)
    except Exception:                                     # noqa: BLE001
        return 1
    for s in structure.segments:
        if s.kind != "app1_exif":
            continue
        try:
            tree = t.parse(s.payload[6:], strict=False)
        except Exception:                                 # noqa: BLE001
            continue
        ifd0 = tree.ifd("IFD0")
        e = ifd0.get(TAG_ORIENTATION) if ifd0 is not None else None
        if e is not None:
            value = struct.unpack(tree.byte_order + "H",
                                  struct.pack(tree.byte_order + "I", e.raw_value)[:2])[0]
            return value if 1 <= value <= 8 else 1
    return 1


def segment(value: int) -> bytes:
    """The canonical APP1 carrying only Orientation: big-endian TIFF, one IFD0
    entry, no next IFD. Empty for 1 (the default needs no tag)."""
    if value == 1:
        return b""
    tiff = (b"MM\x00\x2a" + struct.pack(">I", 8) + struct.pack(">H", 1)
            + struct.pack(">HHIHH", TAG_ORIENTATION, 3, 1, value, 0)
            + struct.pack(">I", 0))
    payload = b"Exif\x00\x00" + tiff
    return b"\xff\xe1" + struct.pack(">H", 2 + len(payload)) + payload


SEGMENTS = {segment(v) for v in range(2, 9)}


def insert(jpeg: bytes, value: int) -> bytes:
    """`jpeg` with the canonical orientation segment after SOI and any APP0 (EXIF
    follows JFIF when both are present)."""
    mark = segment(value)
    if not mark:
        return jpeg
    pos = 2
    while jpeg[pos:pos + 2] == b"\xff\xe0":
        pos += 2 + struct.unpack_from(">H", jpeg, pos + 2)[0]
    return jpeg[:pos] + mark + jpeg[pos:]


def is_canonical(data: bytes, s: seg.Segment) -> bool:
    return bytes(data[s.offset:s.end]) in SEGMENTS


def apply(image, value: int):
    """The image as a viewer displays it (EXIF transform 1-8)."""
    from PIL import Image
    ops = {2: [Image.Transpose.FLIP_LEFT_RIGHT], 3: [Image.Transpose.ROTATE_180],
           4: [Image.Transpose.FLIP_TOP_BOTTOM], 5: [Image.Transpose.TRANSPOSE],
           6: [Image.Transpose.ROTATE_270], 7: [Image.Transpose.TRANSVERSE],
           8: [Image.Transpose.ROTATE_90]}
    for op in ops.get(value, []):
        image = image.transpose(op)
    return image
