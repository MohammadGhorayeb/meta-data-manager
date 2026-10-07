"""Plain TIFF at F1 -- scans, exports, conversions -- with nothing moved.

Phase 6 M1 (`docs/p6_tail_plan.md` §1, §4 D1). What a TIFF carries beyond what a
camera raw does, measured: `DocumentName` and `PageName`, which tools fill with the
file's original path (`exiftool -all=` leaves `DocumentName`); Windows Explorer's
XP fields; and, after `sips` converts a JPEG, an IPTC block it invented from the
EXIF. So on top of the shared cleaning (`clean.py`, also RAW's):

- blanked at their own length in every IFD: the dates, software, host computer,
  description and user comment RAW blanks, plus Make, Model, DocumentName,
  PageName, Artist, Copyright, the XP fields, the EXIF owner/serial/lens fields and
  EXIF 3.0's photographer and editing-software fields -- no decoder reads any of
  them in a plain TIFF, unlike a raw's Make and Model;
- removed in place: XMP, IPTC, Photoshop blocks, the maker note (nothing here
  decodes it) and the GPS IFD;
- the ICC profile is KEPT: a TIFF is often the wide-gamut or CMYK file whose
  pixels need it (JPEG F1 drops ICC on its sRGB assumption). A published colour
  space's profile (`icc.is_standard`: sRGB, Display P3, Adobe RGB...) stays byte
  for byte -- the same bytes are in millions of files, and zeroing its header would
  turn a crowd value into a mark of this tool. Any other profile (a display
  calibrated on someone's own screen) is sanitized in place: the provenance header
  zeroed, the profile ID recomputed, the colour tables untouched;
- an embedded JPEG (an EXIF thumbnail) is cleaned within its own extent.

Every IFD of a multi-page file is walked. Before returning, F1 checks that no byte
of any image data changed. BigTIFF (64-bit offsets) is refused.
"""
from __future__ import annotations

import struct

from ...errors import ParseError, UnsupportedFormatError
from ...standards import icc
from ...standards import tiff_ifd as t
from ...standards import tiff_values as tv
from . import clean as tc

MAGIC_BIGTIFF = 43
TAG_MAKERNOTE = 0x927C
TAG_ICC = 0x8773
# Blanked in every IFD, in addition to the shared `clean.BLANK_ANYWHERE`.
PLAIN_BLANK = {
    0x010D: "DocumentName", 0x011D: "PageName", 0x010F: "Make", 0x0110: "Model",
    0x013B: "Artist", 0x8298: "Copyright",
    0x9C9B: "XPTitle", 0x9C9C: "XPComment", 0x9C9D: "XPAuthor",
    0x9C9E: "XPKeywords", 0x9C9F: "XPSubject",
    0xA420: "ImageUniqueID", 0xA430: "CameraOwnerName", 0xA431: "BodySerialNumber",
    0xA432: "LensSpecification", 0xA433: "LensMake", 0xA434: "LensModel",
    0xA435: "LensSerialNumber", 0xA436: "ImageTitle", 0xA437: "Photographer",
    0xA438: "ImageEditor", 0xA439: "CameraFirmware",
    0xA43A: "RAWDevelopingSoftware", 0xA43B: "ImageEditingSoftware",
    0xA43C: "MetadataEditingSoftware",
}
BLANK = {**tc.BLANK_ANYWHERE, **PLAIN_BLANK}
REMOVE = {**tc.REMOVE_ANYWHERE, TAG_MAKERNOTE: "MakerNote"}


def is_tiff(data: bytes) -> bool:
    return data[:4] in (b"II*\x00", b"MM\x00*")


def _parse(data) -> t.IfdTree:
    if data[:4] in (b"II+\x00", b"MM\x00+"):
        raise UnsupportedFormatError("TIFF: BigTIFF (64-bit offsets) is not handled "
                                     "-- not scrubbed (limit #56)")
    return t.parse(data, strict=True, magics=(t.MAGIC_TIFF,))


def _jpegs(buf, tree: t.IfdTree) -> list[tuple[str, int, int]]:
    """Embedded JPEGs: an IFD's JPEGInterchangeFormat block (the EXIF thumbnail)."""
    out = []
    for ifd in tree.ifds:
        offs, lens = ifd.get(0x0201), ifd.get(0x0202)
        if offs is not None and lens is not None:
            at, n = ifd.base + offs.raw_value, lens.raw_value
            if 0 < n and at + n <= len(buf):
                out.append((ifd.name, at, n))
    return out


def _sanitize_icc(buf: bytearray, tree: t.IfdTree, removed: list[str]) -> None:
    for ifd in tree.ifds:
        e = ifd.get(TAG_ICC)
        if e is None:
            continue
        profile = t.value_bytes(buf, e)
        if not icc.looks_like_profile(profile) or icc.is_standard(profile):
            continue
        clean = icc.sanitize(profile)
        if clean != profile:
            buf[e.data_offset:e.data_offset + len(clean)] = clean
            removed.append(f"{ifd.name}: ICC profile provenance (kept for colour)")


def _scrub(data: bytes, report: bool = False):
    tree = _parse(data)
    before = {i.offset: tc.data_regions(data, i, within_file=True) for i in tree.ifds}
    jpegs = _jpegs(data, tree)
    buf = bytearray(data)
    removed: list[str] = []
    for ifd in tree.ifds:
        for tag, name in BLANK.items():
            e = ifd.get(tag)
            if e is not None and t.value_bytes(buf, e).strip(b"\x00"):
                t.blank_value(buf, e)
                removed.append(f"{ifd.name}: {name}")
        for tag, name in REMOVE.items():
            if t.remove_entry(buf, ifd, tag):
                removed.append(f"{ifd.name}: {name}")
    tc.drop_gps(buf, tree, removed)
    _sanitize_icc(buf, _parse(bytes(buf)), removed)
    cleaned = []
    for where, at, n in jpegs:
        if tc.clean_jpeg_in_place(buf, at, n):
            cleaned.append((at, n))
            removed.append(f"{where}: the thumbnail's own metadata")
    tc.check_image_data_untouched(data, buf, before, cleaned, "TIFF")
    out = bytes(buf)
    return (out, removed) if report else out


def scrub(data: bytes) -> bytes:
    try:
        return _scrub(data)
    except (struct.error, IndexError, ValueError) as e:
        raise ParseError(f"TIFF: malformed file ({type(e).__name__}: {e})") from None


def scrub_with_report(data: bytes) -> tuple[bytes, list[str]]:
    try:
        return _scrub(data, report=True)
    except (struct.error, IndexError, ValueError) as e:
        raise ParseError(f"TIFF: malformed file ({type(e).__name__}: {e})") from None


def residuals(data: bytes) -> list[str]:
    """Re-walk the output and read the bytes. Anything below would have shipped."""
    tree = _parse(data)
    out = []
    for ifd in tree.ifds:
        if ifd.name == "GPSIFD" or ifd.get(tc.TAG_GPS) is not None:
            out.append(f"GPS survives in {ifd.name}")
        for tag, name in REMOVE.items():
            if ifd.get(tag) is not None:
                out.append(f"{name} survives in {ifd.name}")
        for tag, name in BLANK.items():
            e = ifd.get(tag)
            if e is not None and t.value_bytes(data, e).strip(b"\x00 "):
                out.append(f"{name} survives in {ifd.name}")
        e = ifd.get(TAG_ICC)
        if e is not None:
            profile = t.value_bytes(data, e)
            if icc.looks_like_profile(profile) and not icc.is_standard(profile) \
                    and icc.sanitize(profile) != profile:
                out.append(f"the ICC profile in {ifd.name} still carries provenance")
    for where, at, n in _jpegs(data, tree):
        if tc.jpeg_has_metadata(bytes(data[at:at + n])):
            out.append(f"{where}: the thumbnail still carries its own metadata")
    return out


def describe(data: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        tree = _parse(data)
    except Exception:                                     # noqa: BLE001
        return out
    names = {**BLANK, **REMOVE}
    for ifd in tree.ifds:
        for e in ifd.entries:
            name = names.get(e.tag)
            if name is None:
                continue
            value = None if e.tag in REMOVE else tv.decode(data, ifd.order, e)
            out[f"{ifd.name}:{name}"] = value or f"({e.data_length} bytes)"
        if ifd.name == "GPSIFD":
            out["GPS"] = f"{len(ifd.entries)} fields"
    return out
