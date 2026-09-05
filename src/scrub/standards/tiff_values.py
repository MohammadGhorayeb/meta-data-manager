"""Decoding EXIF values for the scrub report — and only for it.

`tiff_ifd.py` is deliberately offset-oriented: scrubbing needs to know *where* every
metadata byte is, not what it says, and a stripper that interpreted values would be
doing work it cannot use. The report needs the opposite, so the decoding lives here
rather than complicating the parser every tier depends on.

Only the tags a person would recognise are named. An exhaustive EXIF table would be
hundreds of entries and would make the report unreadable; the point is to show the
user *the camera, the time and the place*, which is what they came to remove.
Anything unnamed is still counted, so the report never implies a tag was missed.
"""
from __future__ import annotations

import struct

# type -> (bytes per component, struct code)
_TYPES = {1: (1, "B"), 2: (1, "s"), 3: (2, "H"), 4: (4, "I"), 5: (8, None),
          7: (1, "s"), 9: (4, "i"), 10: (8, None), 11: (4, "f"), 12: (8, "d")}

NAMES = {
    0x010F: "Make", 0x0110: "Model", 0x0131: "Software", 0x013B: "Artist",
    0x8298: "Copyright", 0x0132: "DateTime", 0x010E: "ImageDescription",
    0x9003: "DateTimeOriginal", 0x9004: "DateTimeDigitized",
    0xA430: "CameraOwnerName", 0xA431: "BodySerialNumber",
    0xA433: "LensMake", 0xA434: "LensModel", 0xA435: "LensSerialNumber",
    0x927C: "MakerNote", 0x9286: "UserComment", 0xC614: "UniqueCameraModel",
    0xA420: "ImageUniqueID", 0x001D: "GPSDateStamp", 0x0001: "GPSLatitudeRef",
    0x0002: "GPSLatitude", 0x0003: "GPSLongitudeRef", 0x0004: "GPSLongitude",
    0x0006: "GPSAltitude", 0x0007: "GPSTimeStamp", 0x001B: "GPSProcessingMethod",
}

# Tags whose *name* collides across IFDs (GPS tag 1 vs IFD0 tag 1), so the GPS
# names above are only applied inside the GPS IFD.
_GPS_ONLY = {0x0001, 0x0002, 0x0003, 0x0004, 0x0006, 0x0007, 0x001B, 0x001D}


def tag_name(ifd_name: str, tag: int) -> str | None:
    is_gps = ifd_name.startswith("GPS")
    if tag in _GPS_ONLY and not is_gps:
        return None
    if tag not in _GPS_ONLY and is_gps:
        return None
    return NAMES.get(tag)


def _rationals(tiff: bytes, order: str, offset: int, count: int,
               signed: bool) -> list[float]:
    out = []
    code = order + ("ii" if signed else "II")
    for i in range(count):
        at = offset + i * 8
        if at + 8 > len(tiff):
            break
        num, den = struct.unpack_from(code, tiff, at)
        out.append(num / den if den else 0.0)
    return out


def decode(tiff: bytes, order: str, entry) -> str | None:
    """A short printable rendering of one entry's value, or None if we do not
    decode that type. Never raises: a malformed value is a `None`, not a crash in a
    reporting path."""
    try:
        size, code = _TYPES.get(entry.type, (0, None))
        if not size:
            return None
        length = size * entry.count
        if entry.inline:
            raw = struct.pack(order + "I", entry.raw_value)[:length]
        else:
            if entry.data_offset is None:
                return None
            raw = tiff[entry.data_offset:entry.data_offset + length]
        if len(raw) < length:
            return None

        if entry.type in (2, 7):                      # ASCII / UNDEFINED
            text = raw.split(b"\x00", 1)[0]
            if entry.type == 7 and not text.isascii():
                return f"({length} bytes)"
            return text.decode("utf-8", "replace") or None
        if entry.type in (5, 10):                     # RATIONAL
            vals = _rationals(tiff, order, entry.data_offset or 0, entry.count,
                              entry.type == 10)
            if len(vals) == 3:                        # a GPS coordinate triple
                return f"{vals[0]:.0f}° {vals[1]:.0f}' {vals[2]:.2f}\""
            return ", ".join(f"{v:g}" for v in vals) or None
        vals = struct.unpack(order + code * entry.count, raw)
        return ", ".join(str(v) for v in vals)
    except Exception:                                 # noqa: BLE001
        return None
