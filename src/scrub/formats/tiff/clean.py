"""In-place TIFF cleaning shared by plain TIFF and camera RAW.

A TIFF records where its pixels are as absolute offsets, so cleaning one never moves
a byte: values are blanked at their own length, directory entries removed in place,
whole IFDs zeroed, and a JPEG stored inside the file (a thumbnail, a raw's preview)
rewritten within its own extent. Moved here from `formats/raw/f1.py` when plain TIFF
needed the same machinery (Phase 6 M1), so the two handlers share one copy -- a
missed copy is a leak.
"""
from __future__ import annotations

import struct

from ...errors import ParseError, ScrubError
from ...standards import tiff_ifd as t
from ..jpeg import f1 as jpeg_f1
from ..jpeg import segments as jseg

# Blanked in EVERY IFD of the file (IFD0, IFD1..., SubIFDs, ExifIFD).
BLANK_ANYWHERE = {
    0x010E: "ImageDescription", 0x0131: "Software", 0x0132: "DateTime",
    0x013C: "HostComputer", 0x9003: "DateTimeOriginal", 0x9004: "DateTimeDigitized",
    0x9010: "OffsetTime", 0x9011: "OffsetTimeOriginal",
    0x9012: "OffsetTimeDigitized", 0x9290: "SubSecTime",
    0x9291: "SubSecTimeOriginal", 0x9292: "SubSecTimeDigitized",
    0x9286: "UserComment", 0xC62F: "CameraSerialNumber",
    0xC68B: "OriginalRawFileName",
}
# Removed (entry deleted in place, value zeroed) from every IFD.
REMOVE_ANYWHERE = {0x02BC: "XMP", 0x83BB: "IPTC", 0x8649: "Photoshop"}
TAG_GPS = 0x8825
# Image-data locators: (offsets tag, byte-counts tag).
DATA_TAGS = ((0x0111, 0x0117), (0x0144, 0x0145), (0x0201, 0x0202))
JPEG_FRAMES = {"sof0", "sof1", "sof2"}

# Marks a cleaned embedded JPEG leaves, declared to the fingerprint guard: EOI then
# the zero padding that keeps its length, and SOI followed directly by a kept ICC
# segment once EXIF is gone; and the zeros a blanked value or dropped block is.
JPEG_PAD_MARK = b"\xff\xd9" + bytes(254)
JPEG_ICC_FIRST = b"\xff\xd8\xff\xe2"
BLANKED_RUN = bytes(1024)


def short(entry: t.IfdEntry, order: str) -> int:
    return struct.unpack(order + "H", struct.pack(order + "I", entry.raw_value)[:2])[0]


def ints(buf, entry: t.IfdEntry, order: str) -> list[int]:
    raw = t.value_bytes(buf, entry)
    code = {3: "H", 4: "I", 13: "I"}.get(entry.type)
    if code is None:
        raise ParseError(f"TIFF: tag {entry.tag:#06x} is not an integer array")
    return list(struct.unpack(f"{order}{entry.count}{code}", raw))


def data_regions(buf, ifd: t.Ifd, *, within_file: bool = False
                 ) -> list[tuple[int, int]]:
    """Where an IFD's image bytes are: strips, tiles or an embedded JPEG.

    `within_file` refuses image data that runs past the end of the file: a damaged
    or truncated TIFF, not one to clean (comparing slices there compares two short
    slices and passes -- the TIFF corpus's truncation test found it). Off for RAW,
    because real files need it off: a Panasonic RW2 declares more sensor data than
    it holds, and LibRaw decodes it."""
    out = []
    for off_tag, len_tag in DATA_TAGS:
        offs, lens = ifd.get(off_tag), ifd.get(len_tag)
        if offs is None or lens is None:
            continue
        o, n = ints(buf, offs, ifd.order), ints(buf, lens, ifd.order)
        if len(o) != len(n):
            raise ParseError(f"TIFF: {ifd.name} offsets and counts disagree")
        regions = [(ifd.base + a if off_tag == 0x0201 else a, b)
                   for a, b in zip(o, n, strict=True)]
        if within_file and any(a + b > len(buf) for a, b in regions):
            raise ParseError(f"TIFF: {ifd.name}'s image data runs past the end of "
                             "the file")
        out += regions
    return out


def jpeg_has_metadata(blob: bytes) -> bool:
    """True when the JPEG F1 keep-list would change this embedded JPEG: a metadata
    segment, a non-canonical JFIF, or anything but zeros after EOI. Decided by the
    same keep-list the JPEG format uses, so the two cannot disagree."""
    if blob[:2] != b"\xff\xd8":
        return False
    structure = jseg.walk(blob)
    if not any(s.kind in JPEG_FRAMES for s in structure.segments):
        return False                               # not a picture JPEG: never touched
    if structure.trailer.strip(b"\x00"):
        return True
    body = blob[:len(blob) - len(structure.trailer)]
    return jpeg_f1.scrub(body, keep_icc=True) != body


def clean_jpeg_in_place(buf: bytearray, at: int, length: int) -> bool:
    """Strip an embedded JPEG's metadata, keeping its picture and colour profile,
    inside its own extent: the shorter result is padded with zeros after EOI, so
    nothing after it moves. Returns whether it changed anything."""
    blob = bytes(buf[at:at + length])
    if not jpeg_has_metadata(blob):
        return False
    cleaned = jpeg_f1.scrub(blob, keep_icc=True)
    if len(cleaned) > length:
        raise ScrubError("TIFF: a cleaned embedded JPEG came out larger than the "
                         "original")
    buf[at:at + length] = cleaned + bytes(length - len(cleaned))
    return True


def drop_gps(buf: bytearray, tree: t.IfdTree, removed: list[str]) -> None:
    gps = tree.ifd("GPSIFD")
    if gps is None:
        return
    for ifd in tree.ifds:
        if ifd.get(TAG_GPS) is not None:
            t.remove_entry(buf, ifd, TAG_GPS)
    for e in gps.entries:
        if not e.inline:
            t.zero(buf, e.data_offset, e.data_length)
    t.zero(buf, gps.offset, 2 + 12 * len(gps.entries) + 4)
    removed.append(f"GPS ({len(gps.entries)} fields)")


def check_image_data_untouched(data: bytes, buf: bytearray, before: dict,
                               allowed: list[tuple[int, int]], label: str) -> None:
    """The tier's promise, checked on the bytes: every image region in the input is
    byte-identical in the output, except the embedded JPEGs F1 cleaned and anything
    it dropped on purpose."""
    if len(buf) != len(data):
        raise ScrubError(f"{label} F1 changed the file's length")
    view = memoryview(buf)
    for regions in before.values():
        for a, n in regions:
            if any(x <= a and a + n <= x + m for x, m in allowed):
                continue
            if view[a:a + n] != data[a:a + n]:
                raise ScrubError(f"{label} F1 altered image data at {a} ({n} bytes)")
