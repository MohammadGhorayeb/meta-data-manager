"""What identifies the camera and the person in a TIFF-family raw file, and how each
maker's copy is removed without moving a byte.

Measured on eight real files before any of this was written (docs/p4_media_plan.md
§7, §8.9): serial numbers -- body, internal, lens, extender, flash -- usage counters,
and owner names, one of them an email address, mostly inside the MAKER NOTE. Two
strategies, each chosen by a measurement rather than by taste:

  * **blank in place** (Canon, Nikon, Olympus). Their maker notes carry what the
    decoder needs: zeroed whole, a Nikon file stops decoding and a Canon one loses
    its white balance. Blanking only the fields below keeps every render
    pixel-identical, Nikon included, whose colour block is encrypted under exactly
    its serial and shutter count.
  * **remove the maker note** (Sony, Apple). Measured decode-irrelevant: zeroed
    whole, sensor data and rendering are identical. Sony's counters also sit inside
    enciphered sub-blocks, where a field cannot be blanked without re-enciphering.

Each field is (IFD name, tag, optional (start, length) inside the value). Tag IDs
are ExifTool's reading of the real files, not a transcription of its tables.

**Copies ExifTool does not name.** Blanking those fields satisfied ExifTool on all
five files, and a search of the BYTES still found the Canon 80D's owner name: a
second copy inside `CameraInfo` (0x000D), a 1,536-byte model-specific block whose
layout ExifTool does not decode for that body, so it reported the name gone. Its
offset differs per model, so a table cannot keep up. Instead, every text value
blanked above is searched for across the whole maker note afterwards and each
further copy is blanked too -- text only, at least `MIN_COPY_LEN` characters, so a
coincidental match in binary data is not plausible, and never outside the maker
note, so the sensor data is never in reach. The decode checks then hold it to
account like everything else.
"""
from __future__ import annotations

from dataclasses import dataclass

from ...errors import ParseError
from ...standards import tiff_ifd as t


@dataclass(frozen=True)
class Field:
    ifd: str
    tag: int
    name: str
    span: tuple[int, int] | None = None     # (start, length) inside the value


# Wherever the standard EXIF places them, for every maker.
COMMON = (
    Field("IFD0", 0x013B, "Artist"),
    Field("IFD0", 0x8298, "Copyright"),
    Field("ExifIFD", 0xA430, "CameraOwnerName"),
    Field("ExifIFD", 0xA431, "BodySerialNumber"),
    Field("ExifIFD", 0xA435, "LensSerialNumber"),
    Field("ExifIFD", 0xA420, "ImageUniqueID"),
)

MAKERNOTE_FIELDS = {
    "canon": (
        Field("MakerNote", 0x0009, "OwnerName"),
        Field("MakerNote", 0x000C, "SerialNumber"),
        Field("MakerNote", 0x0096, "InternalSerialNumber"),
        Field("MakerNote", 0x4019, "LensSerialNumber", (0, 5)),     # LensInfo
    ),
    "nikon": (
        Field("MakerNote", 0x001D, "SerialNumber"),
        Field("MakerNote", 0x00A7, "ShutterCount"),
    ),
    "olympus": (
        Field("MakerNote/Equipment", 0x0101, "SerialNumber"),
        Field("MakerNote/Equipment", 0x0102, "InternalSerialNumber"),
        Field("MakerNote/Equipment", 0x0202, "LensSerialNumber"),
        Field("MakerNote/Equipment", 0x0302, "ExtenderSerialNumber"),
        Field("MakerNote/Equipment", 0x1003, "FlashSerialNumber"),
    ),
}
REMOVE_MAKERNOTE = frozenset({"sony", "apple"})

MIN_COPY_LEN = 4


def blank_identity(buf: bytearray, magics=t.RAW_MAGICS) -> list[str]:
    """Remove every identity field M16 models from a TIFF-family raw file, in place.

    Returns what was removed, by name. Raises ParseError for a maker note whose
    layout is not modelled: its serials cannot be found, so the file cannot be
    called clean, and that is a refusal for the handler to make (M17), not a guess.
    Nothing moves; `buf` keeps its length and every offset stays true.
    """
    tree = t.parse(bytes(buf), strict=True, magics=magics)
    note = t.makernote(bytes(buf), tree)
    ifds = {i.name: i for i in tree.ifds}
    fields = list(COMMON)
    removed: list[str] = []

    if note is not None:
        if note.vendor in REMOVE_MAKERNOTE:
            t.remove_entry(buf, ifds["ExifIFD"], t.TAG_MAKERNOTE)
            removed.append(f"MakerNote ({note.vendor}, {note.length} bytes)")
        elif note.vendor in MAKERNOTE_FIELDS:
            ifds.update({i.name: i for i in note.ifds})
            fields += MAKERNOTE_FIELDS[note.vendor]
        else:
            raise ParseError(f"RAW: maker note in an unmodelled layout "
                             f"({note.length} bytes) -- its serials cannot be found")

    texts: set[bytes] = set()
    for f in fields:
        ifd = ifds.get(f.ifd)
        entry = ifd.get(f.tag) if ifd is not None else None
        if entry is None:
            continue
        if entry.type == 2:                                       # ASCII
            text = t.value_bytes(buf, entry).split(b"\x00")[0].strip()
            if len(text) >= MIN_COPY_LEN:
                texts.add(text)
        if f.span is None:
            t.blank_value(buf, entry)
        else:
            t.blank_span(buf, entry, *f.span)
        removed.append(f.name)

    if note is not None and note.vendor in MAKERNOTE_FIELDS:
        copies = _blank_copies(buf, note.offset, note.length, texts)
        if copies:
            removed.append(f"{copies} further cop{'y' if copies == 1 else 'ies'} "
                           "of a removed value inside the maker note")
    return removed


def _blank_copies(buf: bytearray, offset: int, length: int,
                  texts: set[bytes]) -> int:
    """Zero every remaining occurrence of each text inside [offset, offset+length)."""
    region = memoryview(buf)[offset:offset + length]
    found = 0
    for text in sorted(texts, key=len, reverse=True):
        at = bytes(region).find(text)
        while at != -1:
            t.zero(region, at, len(text))
            found += 1
            at = bytes(region).find(text, at + len(text))
    return found
