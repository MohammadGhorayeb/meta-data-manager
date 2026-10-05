"""Camera RAW F1 (TIFF family: DNG, CR2, NEF, ARW, ORF) — nothing moves.

The sensor data sits at offsets the file's own tables record, so F1 never shifts a
byte: it blanks values, removes directory entries in place, zeroes regions, and
rewrites embedded preview JPEGs inside their own extent. Measured on five real files
before writing it (docs/p4_media_plan.md §7, §8.9, §10):

  * **identity** -- serials, owners, counters, per maker (`identity.py`, M16);
  * **when and where** -- every EXIF date and its sub-second and UTC-offset
    companions, in whichever IFD a maker put them (Nikon puts one in IFD0, Sony
    repeats IFD0 in IFD1), the GPS IFD whole, and the dates and time zones maker
    notes keep beside the serials (Canon's time-zone CITY, Nikon's power-up time,
    Olympus's UTC date);
  * **what wrote it** -- Software (the iOS version on an iPhone), HostComputer,
    ImageDescription, XMP, IPTC, Photoshop blocks; in a DNG also the original raw's
    file name, an embedded copy of the original raw file, and Adobe's private block,
    which carries the original maker note and its serials;
  * **the previews' own metadata** -- an iPhone DNG's 5 MB preview has its own EXIF
    with the full GPS, written separately from the main copy (the altitude differs
    in the last digit), so a value search for removed bytes would miss it. Each
    preview goes through the Phase 1 JPEG F1 with its colour profile kept, and is
    padded back to its own length;
  * **subject-derived images** -- a DNG 1.6 semantic mask (ProRAW's sky matte) is
    dropped from SubIFDs and zeroed, the HEIC decision carried over (limit #31).

Kept on purpose, because the decoder needs them or they are content: Make and
Model (LibRaw selects the camera profile by them), the lens model, colour and
calibration tags, every other maker-note field, and the previews themselves (a
viewer that shows the preview would otherwise show nothing -- hard constraint 1).

Before returning, F1 checks that no byte changed inside any image data except the
previews it cleaned and the masks it dropped. The decode oracle (LibRaw sensor data
and a camera-white-balance render) lives in the tests: `rawpy` is test-only.
"""
from __future__ import annotations

import struct

from ...errors import ParseError, ScrubError
from ...standards import tiff_ifd as t
from ..jpeg import f1 as jpeg_f1
from ..jpeg import segments as jseg
from . import identity
from .identity import Field

MAGIC_RW2 = 0x0055

# Blanked in EVERY IFD of the file (IFD0, IFD1..., SubIFDs, ExifIFD).
_BLANK_ANYWHERE = {
    0x010E: "ImageDescription", 0x0131: "Software", 0x0132: "DateTime",
    0x013C: "HostComputer", 0x9003: "DateTimeOriginal", 0x9004: "DateTimeDigitized",
    0x9010: "OffsetTime", 0x9011: "OffsetTimeOriginal",
    0x9012: "OffsetTimeDigitized", 0x9290: "SubSecTime",
    0x9291: "SubSecTimeOriginal", 0x9292: "SubSecTimeDigitized",
    0x9286: "UserComment", 0xC62F: "CameraSerialNumber",
    0xC68B: "OriginalRawFileName",
}
# Removed (entry deleted in place, value zeroed) from every IFD.
_REMOVE_ANYWHERE = {0x02BC: "XMP", 0x83BB: "IPTC", 0x8649: "Photoshop"}
# Removed in a DNG only. 0xC634 is Adobe's private block there, carrying the
# ORIGINAL maker note; in a Sony ARW the same tag points at the enciphered white
# balance the decoder needs, so it is never touched outside DNG.
_REMOVE_IN_DNG = {0xC68C: "OriginalRawFileData", 0xC634: "DNGPrivateData"}
TAG_DNG_VERSION = 0xC612
TAG_GPS = 0x8825
TAG_SEMANTIC_NAME = 0xCD2E

# Dates and time zones inside maker notes, beside the serials.
MAKERNOTE_DATES = {
    "canon": (Field("MakerNote", 0x0035, "TimeZone/TimeZoneCity/DST", (4, 12)),),
    "nikon": (Field("MakerNote", 0x0024, "WorldTime (time zone)"),
              Field("MakerNote", 0x00B6, "PowerUpTime")),
    "olympus": (Field("MakerNote/CameraSettings", 0x0908, "DateTimeUTC"),),
}

# Image-data locators: (offsets tag, byte-counts tag).
_DATA_TAGS = ((0x0111, 0x0117), (0x0144, 0x0145), (0x0201, 0x0202))
# A preview is a baseline or progressive JPEG. The raw image itself can be a JPEG
# too -- Canon's is lossless (SOF3) -- and must never reach the preview cleaner,
# whose rewrites the image-data check is told to allow.
_PREVIEW_FRAMES = {"sof0", "sof1", "sof2"}
_RAW_PHOTOMETRIC = {32803, 34892}          # CFA, LinearRaw
TAG_CR2_SLICE = 0xC640


def _short(entry: t.IfdEntry, order: str) -> int:
    return struct.unpack(order + "H", struct.pack(order + "I", entry.raw_value)[:2])[0]


def _ints(buf, entry: t.IfdEntry, order: str) -> list[int]:
    raw = t.value_bytes(buf, entry)
    code = {3: "H", 4: "I", 13: "I"}.get(entry.type)
    if code is None:
        raise ParseError(f"RAW: tag {entry.tag:#06x} is not an integer array")
    return list(struct.unpack(f"{order}{entry.count}{code}", raw))


def _data_regions(buf, ifd: t.Ifd) -> list[tuple[int, int]]:
    """Where an IFD's image bytes are: strips, tiles or an embedded JPEG."""
    out = []
    for off_tag, len_tag in _DATA_TAGS:
        offs, lens = ifd.get(off_tag), ifd.get(len_tag)
        if offs is None or lens is None:
            continue
        o, n = _ints(buf, offs, ifd.order), _ints(buf, lens, ifd.order)
        if len(o) != len(n):
            raise ParseError(f"RAW: {ifd.name} offsets and counts disagree")
        out += [(ifd.base + a if off_tag == 0x0201 else a, b) for a, b in zip(o, n, strict=True)]
    return out


def _is_raw_ifd(ifd: t.Ifd, is_dng: bool) -> bool:
    phot, sub = ifd.get(0x0106), ifd.get(0x00FE)
    return (ifd.get(TAG_CR2_SLICE) is not None
            or (phot is not None and _short(phot, ifd.order) in _RAW_PHOTOMETRIC)
            or (is_dng and sub is not None and sub.raw_value == 0))


def _previews(buf, tree: t.IfdTree, note: t.MakerNote | None
              ) -> list[tuple[str, int, int]]:
    """Every embedded JPEG that could carry its own metadata, as (where, at, len).

    Only locations whose meaning is certain: JPEGInterchangeFormat in a standard
    IFD or Nikon's PreviewIFD; a single-strip JPEG-compressed IFD; Olympus's
    thumbnail and preview. Olympus reuses 0x0201/0x0202 for other things inside its
    sub-IFDs, so those are never read as a JPEG location.
    """
    out = []
    ifd0 = tree.ifd("IFD0")
    is_dng = ifd0 is not None and ifd0.get(TAG_DNG_VERSION) is not None
    for ifd in tree.ifds:
        if _is_raw_ifd(ifd, is_dng):
            continue
        jif, jlen = ifd.get(0x0201), ifd.get(0x0202)
        if jif is not None and jlen is not None:
            out.append((f"{ifd.name} preview", jif.raw_value, jlen.raw_value))
        comp, strips = ifd.get(0x0103), ifd.get(0x0111)
        if (comp is not None and _short(comp, ifd.order) in (6, 7)
                and strips is not None and strips.count == 1
                and ifd.get(0x0117) is not None):
            out.append((f"{ifd.name} JPEG strip", strips.raw_value,
                        ifd.get(0x0117).raw_value))
    for ifd in note.ifds if note is not None else ():
        if note.vendor == "nikon" and ifd.name == "MakerNote/PreviewIFD":
            jif, jlen = ifd.get(0x0201), ifd.get(0x0202)
            if jif is not None and jlen is not None:
                out.append(("Nikon preview", ifd.base + jif.raw_value, jlen.raw_value))
        if note.vendor == "olympus":
            if ifd.name == "MakerNote":
                thumb = ifd.get(0x0100)
                if thumb is not None and not thumb.inline:
                    out.append(("Olympus thumbnail", thumb.data_offset,
                                thumb.data_length))
            if ifd.name == "MakerNote/CameraSettings":
                start, length = ifd.get(0x0101), ifd.get(0x0102)
                if start is not None and length is not None:
                    out.append(("Olympus preview", ifd.base + start.raw_value,
                                length.raw_value))
    return [(w, a, n) for w, a, n in out if 0 <= a and a + n <= len(buf) and n > 0]


def _has_preview_metadata(blob: bytes) -> bool:
    """True when the JPEG F1 keep-list would change this preview: a metadata
    segment, a non-canonical JFIF, or anything but zeros after EOI. Decided by the
    same keep-list the JPEG format uses, so the two cannot disagree."""
    if blob[:2] != b"\xff\xd8":
        return False
    structure = jseg.walk(blob)
    if not any(s.kind in _PREVIEW_FRAMES for s in structure.segments):
        return False                               # not a preview: never touched
    if structure.trailer.strip(b"\x00"):
        return True
    body = blob[:len(blob) - len(structure.trailer)]
    return jpeg_f1.scrub(body, keep_icc=True) != body


def _clean_preview(buf: bytearray, at: int, length: int) -> bool:
    """Strip a preview JPEG's metadata, keeping its picture and colour profile,
    inside its own extent: the shorter result is padded with zeros after EOI, so
    nothing after it moves. Returns whether it changed anything."""
    blob = bytes(buf[at:at + length])
    if not _has_preview_metadata(blob):
        return False
    cleaned = jpeg_f1.scrub(blob, keep_icc=True)
    if len(cleaned) > length:
        raise ScrubError("RAW: a cleaned preview came out larger than the original")
    buf[at:at + length] = cleaned + bytes(length - len(cleaned))
    return True


def _drop_gps(buf: bytearray, tree: t.IfdTree, removed: list[str]) -> None:
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


def _drop_semantic_masks(buf: bytearray, tree: t.IfdTree, removed: list[str]
                         ) -> list[tuple[int, int]]:
    """Drop DNG 1.6 semantic masks: out of SubIFDs, and their bytes zeroed."""
    dropped = []
    for mask in [i for i in tree.ifds if i.get(TAG_SEMANTIC_NAME) is not None]:
        for parent in tree.ifds:
            if parent.get(t.TAG_SUBIFDS) is not None:
                t.remove_from_array(buf, parent, t.TAG_SUBIFDS, mask.offset)
        regions = _data_regions(buf, mask)
        for e in mask.entries:
            if not e.inline:
                t.zero(buf, e.data_offset, e.data_length)
        for a, n in regions:
            t.zero(buf, a, n)
        t.zero(buf, mask.offset, 2 + 12 * len(mask.entries) + 4)
        dropped += regions
        removed.append("semantic mask (a map of the scene's content)")
    return dropped


def scrub(data: bytes) -> bytes:
    try:
        return _scrub(data)
    except (struct.error, IndexError, ValueError) as e:
        raise ParseError(f"RAW: malformed file ({type(e).__name__}: {e})") from None


def scrub_with_report(data: bytes) -> tuple[bytes, list[str]]:
    try:
        return _scrub(data, report=True)
    except (struct.error, IndexError, ValueError) as e:
        raise ParseError(f"RAW: malformed file ({type(e).__name__}: {e})") from None


def _scrub(data: bytes, report: bool = False):
    tree = t.parse(data, strict=True, magics=t.RAW_MAGICS)
    if tree.magic == MAGIC_RW2:
        raise ParseError("RAW: Panasonic RW2 is not supported yet -- its serials sit "
                         "in the preview's own maker note (Phase 4 M18)")
    is_dng = tree.ifd("IFD0") is not None and \
        tree.ifd("IFD0").get(TAG_DNG_VERSION) is not None
    note = t.makernote(data, tree)
    before = {i.offset: _data_regions(data, i) for i in tree.ifds}
    previews = _previews(data, tree, note)

    buf = bytearray(data)
    removed = identity.blank_identity(buf)

    # Re-read: the identity pass may have removed a maker note.
    tree = t.parse(bytes(buf), strict=True, magics=t.RAW_MAGICS)
    note = t.makernote(bytes(buf), tree)
    ifds = {i.name: i for i in tree.ifds}
    fields = [Field(i.name, tag, name) for i in tree.ifds
              for tag, name in _BLANK_ANYWHERE.items() if i.get(tag) is not None]
    if note is not None and note.vendor in MAKERNOTE_DATES:
        ifds.update({i.name: i for i in note.ifds})
        fields += MAKERNOTE_DATES[note.vendor]
    texts = identity.apply_fields(buf, ifds, fields, removed)
    if note is not None and note.vendor in identity.MAKERNOTE_FIELDS:
        copies = identity.blank_copies(buf, note.offset, note.length, texts)
        if copies:
            removed.append(f"{copies} further cop{'y' if copies == 1 else 'ies'} of a "
                           "removed date or name inside the maker note")

    remove = dict(_REMOVE_ANYWHERE)
    if is_dng:
        remove.update(_REMOVE_IN_DNG)
    for ifd in tree.ifds:
        for tag, name in remove.items():
            if t.remove_entry(buf, ifd, tag):
                removed.append(name)

    _drop_gps(buf, tree, removed)
    tree = t.parse(bytes(buf), strict=True, magics=t.RAW_MAGICS)
    dropped = _drop_semantic_masks(buf, tree, removed)

    cleaned = []
    for where, at, length in previews:
        if any(a <= at < a + n for a, n in dropped):
            continue
        if _clean_preview(buf, at, length):
            cleaned.append((at, length))
            removed.append(f"{where}: its own metadata")

    _check_image_data_untouched(data, buf, before, cleaned + dropped)
    out = bytes(buf)
    return (out, removed) if report else out


def _check_image_data_untouched(data: bytes, buf: bytearray,
                                before: dict, allowed: list[tuple[int, int]]) -> None:
    """The tier's promise, checked on the bytes: every image region in the input --
    sensor data above all -- is byte-identical in the output, except the previews
    F1 cleaned and the masks it dropped."""
    if len(buf) != len(data):
        raise ScrubError("RAW F1 changed the file's length")
    view = memoryview(buf)
    for regions in before.values():
        for a, n in regions:
            if any(x <= a and a + n <= x + m for x, m in allowed):
                continue
            if view[a:a + n] != data[a:a + n]:
                raise ScrubError(f"RAW F1 altered image data at {a} ({n} bytes)")


def residuals(data: bytes) -> list[str]:
    """Re-walk the output. Anything below is a leak we would have shipped."""
    out: list[str] = []
    tree = t.parse(data, strict=True, magics=t.RAW_MAGICS)
    note = t.makernote(data, tree)
    for ifd in tree.ifds:
        if ifd.name == "GPSIFD" or ifd.get(TAG_GPS) is not None:
            out.append(f"GPS survives in {ifd.name}")
        for tag, name in {**_REMOVE_ANYWHERE, 0xC68C: "OriginalRawFileData"}.items():
            if ifd.get(tag) is not None:
                out.append(f"{name} survives in {ifd.name}")
        if ifd.get(TAG_SEMANTIC_NAME) is not None:
            out.append(f"a semantic mask survives in {ifd.name}")
        for tag, name in _BLANK_ANYWHERE.items():
            e = ifd.get(tag)
            if e is not None and t.value_bytes(data, e).strip(b"\x00 "):
                out.append(f"{name} survives in {ifd.name}")
    ifds = {i.name: i for i in tree.ifds}
    if note is not None:
        ifds.update({i.name: i for i in note.ifds})
        if note.vendor in identity.REMOVE_MAKERNOTE:
            out.append(f"a {note.vendor} maker note survives")
    for f in list(identity.COMMON) + list(identity.MAKERNOTE_FIELDS.get(
            note.vendor if note else "", ())):
        ifd = ifds.get(f.ifd)
        e = ifd.get(f.tag) if ifd is not None else None
        if e is None:
            continue
        value = t.value_bytes(data, e)
        if f.span is not None:
            value = value[f.span[0]:f.span[0] + f.span[1]]
        if value.strip(b"\x00"):
            out.append(f"{f.name} survives in {f.ifd}")
    for where, at, length in _previews(data, tree, note):
        if _has_preview_metadata(bytes(data[at:at + length])):
            out.append(f"{where} still carries its own metadata")
    return out
