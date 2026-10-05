"""Camera-RAW corpus for CI: TIFF-family files written byte by byte.

The eight real raw files (`tests/corpus/raw/manifest.txt`) never enter the
repository, so CI needs files that carry what they carry: identity fields in IFD0
and ExifIFD, a maker note in each layout M16 models, a hidden second copy of a value
inside a maker-note binary block (the Canon 80D finding), and -- for the decode
checks -- a DNG that LibRaw actually decodes.

Imports nothing from `src` (a test walks the imports), so a shared misreading of
TIFF cannot cancel itself out between the corpus and the scrubber.
"""
from __future__ import annotations

import io
import struct
from collections.abc import Callable

from PIL import Image

# Planted identity values. Each is distinctive, so finding it in a file's bytes
# means exactly one thing.
ARTIST = b"ARTIST-SENTINEL"
COPYRIGHT = b"COPYRIGHT-SENTINEL"
OWNER = b"OWNER-SENTINEL"
BODY_SERIAL = b"BODYSERIAL-SENTINEL"
LENS_SERIAL = b"LENSSERIAL-SENTINEL"
UNIQUE_ID = b"UNIQUEID-SENTINEL-0123456789AB"
MN_SERIAL = b"MNSERIAL-SENTINEL"
MN_INTERNAL = b"MNINTERNAL-SENTINEL"
SHUTTER_COUNT = 0x5EC12E7
SECRETS = (ARTIST, COPYRIGHT, OWNER, BODY_SERIAL, LENS_SERIAL, UNIQUE_ID, MN_SERIAL,
           MN_INTERNAL)

# What F1 removes beyond identity (M17): when, where, and what wrote it.
DATE = b"2001:02:03 04:05:06"
SUBSEC, OFFSET = b"789", b"+05:45"
SOFTWARE = b"SOFTWARE-SENTINEL"
HOST = b"HOST-SENTINEL"
DESCRIPTION = b"DESCRIPTION-SENTINEL"
XMP = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF>XMP-SENTINEL</rdf:RDF>'
       b"</x:xmpmeta>")
GPS_DATE = b"GPSDATE-SENT"
RAW_NAME = b"RAWFILENAME-SENTINEL.CR2"
RAW_DATA = b"ORIGINAL-RAW-FILE-SENTINEL" * 4
PRIVATE = b"Adobe\x00MakN" + b"PRIVATE-MAKERNOTE-SENTINEL"
PREVIEW_EXIF = b"PREVIEW-EXIF-GPS-SENTINEL"
PREVIEW_TRAILER = b"PREVIEW-TRAILER-SENTINEL"
MASK = b"SEMANTIC-MASK-SENTINEL"
MN_DATE = b"MNDATE-SENTINEL"
F1_SECRETS = (DATE, SOFTWARE, HOST, DESCRIPTION, b"XMP-SENTINEL", GPS_DATE,
              PREVIEW_EXIF, PREVIEW_TRAILER, MASK)
DNG_SECRETS = (RAW_NAME, RAW_DATA, PRIVATE)
ICC = b"ICC_PROFILE\x00\x01\x01" + b"ICC-PROFILE-BODY" * 8

_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 8: 2, 9: 4, 10: 8, 13: 4}
_CODE = {1: "B", 3: "H", 4: "I", 8: "h", 9: "i", 13: "I"}


class Ref:
    """A LONG pointer, resolved at layout time to a named IFD's or blob's offset."""

    def __init__(self, name: str) -> None:
        self.name = name


class Span:
    """An UNDEFINED value whose bytes ARE a named blob laid out elsewhere: written
    as the blob's length and offset, nothing copied. How a maker note is stored."""

    def __init__(self, name: str) -> None:
        self.name = name


class Tiff:
    """A tiny TIFF writer: named IFDs and blobs laid out in order, pointers resolved
    in a second pass (every size is independent of every offset). `header` and
    `start` let it write the body of a maker note whose offsets count from somewhere
    other than a TIFF header."""

    def __init__(self, order: str = "<", magic: int = 42, header: bool = True,
                 start: int = 0) -> None:
        self.order, self.magic, self.header, self.start = order, magic, header, start
        self.items: list[tuple[str, str, object]] = []
        self.first_ifd: str | None = None
        self.where: dict[str, int] = {}
        self.size: dict[str, int] = {}

    def ifd(self, name: str, entries: list, next_ifd: str | None = None) -> Tiff:
        if self.first_ifd is None:
            self.first_ifd = name
        self.items.append(("ifd", name, (entries, next_ifd)))
        return self

    def blob(self, name: str, data: bytes | Callable[[int], bytes]) -> Tiff:
        """`data` may be a function of its own offset, for a maker note whose
        internal offsets count from the TIFF start (Canon, Sony)."""
        self.items.append(("blob", name, data))
        return self

    def _value(self, typ: int, value) -> tuple[int, bytes, bool]:
        """(count, bytes, is_pointer_already)."""
        o = self.order
        if isinstance(value, Span):
            return (self.size.get(value.name, 0),
                    struct.pack(o + "I", self.where.get(value.name, 0)), True)
        if isinstance(value, Ref):
            return 1, struct.pack(o + "I", self.where.get(value.name, 0)), False
        if isinstance(value, list) and value and isinstance(value[0], Ref):
            return len(value), b"".join(struct.pack(o + "I", self.where.get(r.name, 0))
                                        for r in value), False
        if isinstance(value, bytes):
            return len(value) // _SIZE[typ], value, False
        if typ in (5, 10):
            pairs = value if isinstance(value, list) else [value]
            code = "I" if typ == 5 else "i"
            return len(pairs), b"".join(struct.pack(o + code * 2, n, d)
                                        for n, d in pairs), False
        vals = value if isinstance(value, list) else [value]
        return len(vals), struct.pack(f"{o}{len(vals)}{_CODE[typ]}", *vals), False

    def _ifd_bytes(self, at: int, entries: list, nxt: int) -> bytes:
        o = self.order
        entries = sorted(entries, key=lambda e: e[0])
        data_at = at + 2 + 12 * len(entries) + 4
        head, tail = struct.pack(o + "H", len(entries)), b""
        for tag, typ, value in entries:
            count, raw, pointer = self._value(typ, value)
            if pointer or len(raw) <= 4:
                field = raw + bytes(4 - len(raw))
            else:
                if (data_at + len(tail)) % 2:
                    tail += b"\x00"
                field = struct.pack(o + "I", data_at + len(tail))
                tail += raw
            head += struct.pack(o + "HHI", tag, typ, count) + field
        return head + struct.pack(o + "I", nxt) + tail

    def build(self) -> bytes:
        for _ in range(2):
            pos = self.start + (8 if self.header else 0)
            chunks = []
            for kind, name, payload in self.items:
                pos += pos % 2
                self.where[name] = pos
                if kind == "ifd":
                    entries, nxt = payload
                    raw = self._ifd_bytes(pos, entries,
                                          self.where.get(nxt, 0) if nxt else 0)
                else:
                    raw = payload(pos) if callable(payload) else payload
                self.size[name] = len(raw)
                chunks.append((pos, raw))
                pos += len(raw)
        out = bytearray()
        if self.header:
            out += b"II" if self.order == "<" else b"MM"
            out += struct.pack(self.order + "HI", self.magic,
                               self.where[self.first_ifd] if self.first_ifd else 0)
        for at, raw in chunks:
            out += bytes(at - self.start - len(out))
            out += raw
        return bytes(out)


def ascii_(text: bytes) -> tuple[int, bytes]:
    return 2, text + b"\x00"


# --------------------------------------------------------------------------- #
# Maker notes, one per layout M16 models
# --------------------------------------------------------------------------- #
def preview_jpeg(colour=(200, 120, 40)) -> bytes:
    """A small real JPEG carrying what an iPhone DNG's preview carries: its own
    EXIF (here a sentinel standing in for the GPS), a colour profile that must
    SURVIVE, and a trailing secondary image after EOI."""
    img = Image.new("RGB", (16, 12), colour)
    for x in range(16):
        img.putpixel((x, x % 12), (x * 15, 255 - x * 15, 90))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    jpg = buf.getvalue()
    exif = b"Exif\x00\x00" + PREVIEW_EXIF
    app1 = b"\xff\xe1" + struct.pack(">H", 2 + len(exif)) + exif
    app2 = b"\xff\xe2" + struct.pack(">H", 2 + len(ICC)) + ICC
    return jpg[:2] + app1 + app2 + jpg[2:] + PREVIEW_TRAILER


def camera_info_block() -> bytes:
    """A model-specific binary block carrying a copy of the owner's name at an
    offset no table names -- the shape found in the Canon 80D's CameraInfo. Planted
    twice: in CameraInfo (0x000D, now blanked whole) and in a block no table names
    (0x0099), which only the value search can find."""
    return bytes(64) + OWNER + bytes(32 - len(OWNER)) + bytes(range(64))


def canon_note(order: str) -> Callable[[int], bytes]:
    """A plain IFD whose offsets count from the TIFF start, so it is written once
    its own position is known."""
    def at(pos: int) -> bytes:
        return Tiff(order, header=False, start=pos).ifd("canon", [
            (0x0009, *ascii_(OWNER)),
            (0x000C, 4, 123456789),                     # SerialNumber (LONG)
            (0x000D, 7, camera_info_block()),           # CameraInfo
            (0x0035, 4, [16, 120, 24, 1]),              # TimeInfo: zone, city, DST
            (0x0096, *ascii_(MN_INTERNAL)),
            (0x0099, 7, camera_info_block()),           # a block NO table names
            (0x4001, 7, bytes(range(200))),             # colour data: must survive
            (0x4019, 7, LENS_SERIAL[:5] + bytes(25)),   # LensInfo: serial in 0..5
        ]).build()
    return at


def nikon_note() -> bytes:
    """"Nikon\\0" + version, then a complete TIFF of its own."""
    inner = Tiff("<").ifd("nikon", [
        (0x0001, 7, b"0211"),
        (0x0011, 4, Ref("preview_ifd")),                # PreviewIFD
        (0x001D, *ascii_(MN_SERIAL)),
        (0x0024, 7, b"\x00\x3c\x01\x00"),               # WorldTime: +60 min, DST
        (0x0097, 7, bytes(range(140))),                 # enciphered colour: survives
        (0x00A7, 4, SHUTTER_COUNT),
        (0x00B6, 7, MN_DATE[:8]),                       # PowerUpTime
    ])
    inner.ifd("preview_ifd", [(0x0201, 4, Ref("preview")),
                              (0x0202, 4, len(preview_jpeg((10, 200, 10))))])
    inner.blob("preview", preview_jpeg((10, 200, 10)))
    inner = inner.build()
    return b"Nikon\x00\x02\x11\x00\x00" + inner


def olympus_note() -> bytes:
    """IFD at +12, offsets from the note's start, serials one level down."""
    body = Tiff("<", header=False, start=12)
    body.ifd("olympus", [(0x0000, 7, b"0100"), (0x2010, 13, Ref("equipment")),
                         (0x2020, 13, Ref("settings"))])
    body.ifd("equipment", [
        (0x0101, *ascii_(MN_SERIAL)),
        (0x0102, *ascii_(MN_INTERNAL)),
        (0x0202, *ascii_(LENS_SERIAL)),
    ])
    preview = preview_jpeg((40, 40, 220))
    body.ifd("settings", [(0x0100, 4, 1), (0x0101, 4, Ref("preview")),
                          (0x0102, 4, len(preview)), (0x0908, *ascii_(MN_DATE))])
    body.blob("preview", preview)
    return b"OLYMPUS\x00II\x03\x00" + body.build()


def apple_note() -> bytes:
    body = Tiff(">", header=False, start=14).ifd("apple", [
        (0x0001, 9, 14), (0x000B, *ascii_(UNIQUE_ID)),
    ]).build()
    return b"Apple iOS\x00\x00\x01MM" + body


def sony_note(order: str) -> Callable[[int], bytes]:
    def at(pos: int) -> bytes:
        return Tiff(order, header=False, start=pos).ifd("sony", [
            (0x2010, 7, bytes(range(256)) + MN_SERIAL),  # an "enciphered" block
        ]).build()
    return at


NOTES = ("canon", "nikon", "olympus", "apple", "sony", "unknown")
MAKES = {"canon": b"Canon", "nikon": b"NIKON CORPORATION", "olympus": b"OLYMPUS",
         "apple": b"Apple", "sony": b"SONY", "unknown": b"Acme",
         "panasonic": b"Panasonic"}


# --------------------------------------------------------------------------- #
# The file
# --------------------------------------------------------------------------- #
W, H = 32, 24                                       # raw CFA size


def raw_pixels(order: str) -> bytes:
    """A deterministic 16-bit RGGB mosaic with structure in it, so a decode that
    went wrong shows up as different pixels, not as the same flat grey."""
    return b"".join(struct.pack(order + "H", 600 + ((x * 37 + y * 91) % 2400))
                    for y in range(H) for x in range(W))


def build(note: str = "canon", order: str = "<", magic: int = 42,
          dng: bool = True) -> bytes:
    """A DNG LibRaw decodes: IFD0 an 8x8 RGB thumbnail carrying the DNG colour tags
    and IFD0's identity fields; SubIFD0 the raw mosaic; SubIFD1 a JPEG preview with
    its own EXIF; SubIFD2 a semantic mask; ExifIFD the EXIF identity fields, dates
    and a maker note in `note`'s layout; a GPS IFD.

    `dng=False` drops DNGVersion and the DNG-only blocks but keeps 0xC634 -- which
    in a Sony ARW is the enciphered white balance the decoder needs, and must
    survive. LibRaw cannot decode that variant (no camera it knows), so it is for
    structure tests only."""
    thumb = bytes(range(64)) * 3                    # 8x8 RGB, 8-bit
    raw = raw_pixels(order)
    payload = {"canon": canon_note(order), "nikon": nikon_note(),
               "olympus": olympus_note(), "apple": apple_note(),
               "sony": sony_note(order), "unknown": b"ACME\x00" + MN_SERIAL,
               "panasonic": None}[note]
    # Panasonic: no ExifIFD maker note; its maker note (and serials) ride inside the
    # EXIF of a preview stored as an UNDEFINED IFD0 value, tag 0x002E.
    rw2 = [(0x002E, 7, Span("rw2_preview"))] if note == "panasonic" else []
    t = Tiff(order, magic=magic)
    preview, mask = preview_jpeg(), preview_jpeg((90, 160, 230))
    dng_only = [(0xC612, 1, [1, 4, 0, 0]),                     # DNGVersion
                (0xC68B, *ascii_(RAW_NAME)),                  # OriginalRawFileName
                (0xC68C, 7, RAW_DATA)] if dng else []         # OriginalRawFileData
    t.ifd("ifd0", dng_only + rw2 + [
        (0x00FE, 4, 1),                                         # NewSubFileType
        (0x0100, 4, 8), (0x0101, 4, 8), (0x0102, 3, [8, 8, 8]),
        (0x0103, 3, 1), (0x0106, 3, 2),
        (0x010E, *ascii_(DESCRIPTION)),
        (0x010F, *ascii_(MAKES[note])), (0x0110, *ascii_(b"TestModel")),
        (0x0111, 4, Ref("thumb")), (0x0115, 3, 3), (0x0116, 4, 8),
        (0x0117, 4, len(thumb)), (0x011C, 3, 1),
        (0x0131, *ascii_(SOFTWARE)), (0x0132, *ascii_(DATE)),
        (0x013B, *ascii_(ARTIST)), (0x013C, *ascii_(HOST)),
        (0x014A, 4, [Ref("raw_ifd"), Ref("preview_ifd"), Ref("mask_ifd")]),
        (0x02BC, 1, XMP),
        (0x8298, *ascii_(COPYRIGHT)),
        (0x8769, 4, Ref("exif")),
        (0x8825, 4, Ref("gps")),
        (0xC614, *ascii_(b"TestModel")),                        # UniqueCameraModel
        (0xC634, 1, PRIVATE),                                   # DNGPrivateData
        (0xC621, 10, [(1, 1), (0, 1), (0, 1), (0, 1), (1, 1), (0, 1),
                      (0, 1), (0, 1), (1, 1)]),                 # ColorMatrix1
        (0xC628, 5, [(5, 10), (10, 10), (7, 10)]),              # AsShotNeutral
        (0xC65A, 3, 21),                                        # CalibrationIlluminant1
    ])
    t.ifd("raw_ifd", [
        (0x00FE, 4, 0),
        (0x0100, 4, W), (0x0101, 4, H), (0x0102, 3, 16), (0x0103, 3, 1),
        (0x0106, 3, 32803), (0x0111, 4, Ref("raw")), (0x0115, 3, 1),
        (0x0116, 4, H), (0x0117, 4, len(raw)), (0x011C, 3, 1),
        (0x828D, 3, [2, 2]), (0x828E, 1, [0, 1, 1, 2]),         # CFA, RGGB
        (0xC61A, 3, 512), (0xC61D, 3, 4095),                    # Black/WhiteLevel
    ])
    t.ifd("preview_ifd", [
        (0x00FE, 4, 1), (0x0100, 4, 16), (0x0101, 4, 12), (0x0103, 3, 7),
        (0x0106, 3, 6), (0x0111, 4, Ref("preview")), (0x0117, 4, len(preview)),
    ])
    t.ifd("mask_ifd", [
        (0x00FE, 4, 4), (0x0100, 4, 16), (0x0101, 4, 12), (0x0103, 3, 7),
        (0x0111, 4, Ref("mask")), (0x0117, 4, len(mask)),
        (0xCD2E, *ascii_(MASK)),                                # SemanticName
    ])
    t.ifd("exif", ([] if payload is None else [(0x927C, 7, Span("note"))]) + [
        (0x9003, *ascii_(DATE)), (0x9004, *ascii_(DATE)),
        (0x9011, *ascii_(OFFSET)), (0x9291, *ascii_(SUBSEC)),
        (0xA420, *ascii_(UNIQUE_ID)),
        (0xA430, *ascii_(OWNER)),
        (0xA431, *ascii_(BODY_SERIAL)),
        (0xA435, *ascii_(LENS_SERIAL)),
    ])
    t.ifd("gps", [
        (0x0000, 1, [2, 3, 0, 0]), (0x0001, *ascii_(b"N")),
        (0x0002, 5, [(51, 1), (30, 1), (1234, 100)]), (0x0003, *ascii_(b"W")),
        (0x0004, 5, [(0, 1), (7, 1), (3912, 100)]), (0x001D, *ascii_(GPS_DATE)),
    ])
    if payload is not None:
        t.blob("note", payload)
    if note == "panasonic":
        t.blob("rw2_preview", preview_jpeg((220, 220, 30)))
    t.blob("thumb", thumb)
    t.blob("preview", preview)
    t.blob("mask", mask)
    t.blob("raw", raw)
    return t.build()


# --------------------------------------------------------------------------- #
# Canon CR3: the MP4 container around a still photo (M18)
# --------------------------------------------------------------------------- #
CR3_CANON_UUID = bytes.fromhex("85c0b687820f11e08111f4ce462b6a48")
CR3_XMP_UUID = bytes.fromhex("be7acfcb97a942e89c71999491e3afac")
CR3_PRVW_UUID = bytes.fromhex("eaf42b5e1c984b88b9fbb7dc406e4d16")
CR3_STAMP = 0xE0000000                       # a nonzero movie-header time
CTMD_TIME = b"CTMD-TIME!!!"                  # 12 bytes: the timestamp record body
COLOR_DATA = b"COLORDATA-THE-DECODER-NEEDS" * 4
CR3_RAW = bytes(range(256)) * 64             # the "sensor data": must not change


def _cmt(entries: list) -> bytes:
    """One CMT block: a complete little TIFF whose IFD0 holds `entries`."""
    return Tiff("<").ifd("ifd0", entries).build()


def _ctmd_sample() -> bytes:
    """Two CTMD records: a timestamp (type 1), and a maker-note TIFF (type 8) whose
    CameraInfo carries the shot count and an owner copy beside ColorData -- where
    LibRaw reads a CR3's white balance, so it must survive."""
    def record(kind: int, body: bytes) -> bytes:
        return struct.pack("<IH", 12 + len(body), kind) + b"\x00\x00\x00\x01\xff\xff" + body
    tiff = _cmt([(0x000D, 7, struct.pack("<I", SHUTTER_COUNT) + OWNER + bytes(40)),
                 (0x4001, 7, COLOR_DATA)])
    tiff_record = struct.pack("<II", 8 + len(tiff), 0x927C) + tiff
    return record(1, CTMD_TIME) + record(8, tiff_record)


def cr3() -> bytes:
    from . import mp4_iso_corpus as ic
    canon = (ic.box(b"CNCV", b"CanonCR3_001/00.10.00/00.00.00")
             + ic.box(b"CMT1", _cmt([(0x0132, *ascii_(DATE)),
                                     (0x013B, *ascii_(ARTIST)),
                                     (0x8298, *ascii_(COPYRIGHT))]))
             + ic.box(b"CMT2", _cmt([(0x9003, *ascii_(DATE)),
                                     (0xA430, *ascii_(OWNER)),
                                     (0xA431, *ascii_(BODY_SERIAL)),
                                     (0xA435, *ascii_(LENS_SERIAL))]))
             + ic.box(b"CMT3", _cmt([(0x0009, *ascii_(OWNER)),
                                     (0x000D, 7, camera_info_block()),
                                     (0x0028, 7, UNIQUE_ID[:16]),
                                     (0x0035, 4, [16, 120, 24, 1]),
                                     (0x0096, *ascii_(MN_INTERNAL)),
                                     (0x4001, 7, COLOR_DATA)]))
             + ic.box(b"CMT4", _cmt([(0x0000, 1, [2, 3, 0, 0]),
                                     (0x001D, *ascii_(GPS_DATE))]))
             + ic.box(b"THMB", bytes(16) + preview_jpeg((5, 5, 5))))
    xmp = ic.box(b"uuid", CR3_XMP_UUID + XMP + b" " * 64)
    prvw = ic.box(b"uuid", CR3_PRVW_UUID + bytes(16) + preview_jpeg())
    free = ic.box(b"free", bytes(32))
    sample = _ctmd_sample()

    def moov(raw_at: int, ctmd_at: int) -> bytes:
        return ic.box(b"moov", ic.box(b"uuid", CR3_CANON_UUID + canon)
                      + ic.mvhd(CR3_STAMP, CR3_STAMP, 3)
                      + ic.trak(1, b"vide", "", (raw_at,), CR3_STAMP, CR3_STAMP,
                                fmt=b"CRAW", sizes=(len(CR3_RAW),))
                      + ic.trak(2, b"meta", "", (ctmd_at,), CR3_STAMP, CR3_STAMP,
                                fmt=b"CTMD", sizes=(len(sample),)))
    head = ic.ftyp(b"crx ", (b"crx ", b"isom"))
    probe = moov(0, 0)
    data_at = len(head) + len(probe) + len(xmp) + len(prvw) + len(free) + 8
    body = moov(data_at, data_at + len(CR3_RAW))
    assert len(body) == len(probe)
    return (head + body + xmp + prvw + free
            + ic.box(b"mdat", CR3_RAW + sample))


# --------------------------------------------------------------------------- #
# Fujifilm RAF: the metadata lives in the preview, and copies in the Fuji block
# --------------------------------------------------------------------------- #
RAF_GEOMETRY = b"FUJI-GEOMETRY-THE-DECODER-NEEDS" * 4
RAF_SENSOR = bytes(range(255, -1, -1)) * 64
RAF_IMAGE_COUNT = 0x0DEC0DE1


def _app1(payload: bytes) -> bytes:
    return b"\xff\xe1" + struct.pack(">H", 2 + len(payload)) + payload


def raf() -> bytes:
    fuji_note = b"FUJIFILM" + struct.pack("<I", 12) + Tiff(
        "<", header=False, start=12).ifd("fuji", [
            (0x0000, 7, b"0130"),
            (0x0010, *ascii_(MN_INTERNAL)),
            (0x1438, 4, RAF_IMAGE_COUNT),
        ]).build()
    exif = Tiff("<")
    exif.ifd("ifd0", [(0x010F, *ascii_(b"FUJIFILM")), (0x0132, *ascii_(DATE)),
                      (0x013B, *ascii_(ARTIST)), (0x8769, 4, Ref("exif"))])
    exif.ifd("exif", [(0x9003, *ascii_(DATE)), (0x927C, 7, Span("note")),
                      (0xA431, *ascii_(BODY_SERIAL))])
    exif.blob("note", fuji_note)
    jpg = preview_jpeg((120, 60, 200))
    jpg = (jpg[:2] + _app1(b"Exif\x00\x00" + exif.build())
           + _app1(b"http://ns.adobe.com/xap/1.0/\x00" + XMP + b" " * 32)
           + jpg[2:])
    jpg = jpg[:jpg.index(PREVIEW_TRAILER)]          # a RAF preview has no trailer
    # The Fuji header block: records of (tag, size, data); 0xC000 carries the
    # geometry the decoder needs AND, unnamed, a copy of the serial and the date.
    record = RAF_GEOMETRY + BODY_SERIAL + bytes(8) + DATE + bytes(8)
    block = struct.pack(">I", 2) + struct.pack(">HHI", 0x0100, 4, 0x01000200) \
        + struct.pack(">HH", 0xC000, len(record)) + record
    at_jpg = 148
    at_hdr = at_jpg + len(jpg)
    at_cfa = at_hdr + len(block)
    head = (b"FUJIFILMCCD-RAW " + b"0201FF159505" + b"X-T4".ljust(32, b"\x00")
            + bytes(84 - 60)
            + struct.pack(">IIIIII", at_jpg, len(jpg), at_hdr, len(block), at_cfa,
                          len(RAF_SENSOR)))
    head += bytes(at_jpg - len(head))
    return head + jpg + block + RAF_SENSOR
