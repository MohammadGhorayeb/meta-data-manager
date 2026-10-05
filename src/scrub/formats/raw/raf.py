"""Fujifilm RAF F1 — the metadata the decoder reads lives in the preview.

A RAF opens with Fujifilm's own header ("FUJIFILMCCD-RAW "), whose offset table
points at three things: a JPEG preview, a Fuji header block of image geometry, and
the sensor data (a TIFF-shaped container). EVERY identity value -- body and lens
serials, the internal serial with its manufacture date, image and exposure counts,
owner, copyright, dates -- is in the preview's own EXIF, Fujifilm's maker note
included. That is also where ExifTool reads a RAF's metadata from.

The preview's EXIF cannot be dropped: stripped, LibRaw no longer decodes the file
("Unexpected end of file") -- measured, M18, and the same failure the survey saw
from `exiftool -all=`. So it is cleaned IN PLACE, as the TIFF of a raw file is:
`f1.clean_tiff` on a writable view of the EXIF block, the EXIF thumbnail through the
preview cleaner, and the XMP packet rewritten empty at its own length.

**And a copy the measuring stick cannot see.** With the EXIF clean by ExifTool's
reading, a search of the bytes still found the body serial and a date inside the
Fuji header block (record 0xC000, 22 KB of Fuji data ExifTool does not decode). The
block carries what the decoder needs, so it is not zeroed: every text value the
EXIF pass removed is searched for there and each copy blanked -- the M16 rule for
Canon's CameraInfo. Nothing in the file moves, and the sensor data is checked
unchanged.
"""
from __future__ import annotations

import struct

from ...errors import ParseError, ScrubError
from ...standards import tiff_ifd as t
from ..jpeg import segments as jseg
from . import cr3, f1, identity

MAGIC = b"FUJIFILMCCD-RAW "
_XMP_ID = b"http://ns.adobe.com/xap/1.0/\x00"


def is_raf(data: bytes) -> bool:
    return data[:16] == MAGIC


def _layout(data: bytes) -> dict[str, tuple[int, int]]:
    if not is_raf(data) or len(data) < 108:
        raise ParseError("RAF: not a Fujifilm RAF")
    jpg, jlen, hdr, hlen, cfa, clen = struct.unpack_from(">IIIIII", data, 84)
    regions = {"preview": (jpg, jlen), "fuji header": (hdr, hlen), "sensor": (cfa, clen)}
    for name, (at, n) in regions.items():
        if at < 108 or at + n > len(data):
            raise ParseError(f"RAF: the {name} lies outside the file")
    return regions


def _exif_and_xmp(data: bytes, at: int, length: int):
    """The preview's EXIF TIFF and XMP packet, as (start, end) in the file."""
    structure = jseg.walk(data[at:at + length])
    exif = xmp = None
    for s in structure.segments:
        body = at + s.offset + 4                      # after marker and length
        if s.kind == "app1_exif" and exif is None:
            exif = (body + 6, at + s.end)             # after "Exif\0\0"
        elif s.kind == "app1_xmp" and xmp is None:
            xmp = (body + len(_XMP_ID), at + s.end)
    return exif, xmp


def scrub(data: bytes) -> bytes:
    return scrub_with_report(data)[0]


def scrub_with_report(data: bytes) -> tuple[bytes, list[str]]:
    try:
        return _scrub(data)
    except (struct.error, IndexError, ValueError) as e:
        raise ParseError(f"RAF: malformed file ({type(e).__name__}: {e})") from None


def _scrub(data: bytes) -> tuple[bytes, list[str]]:
    regions = _layout(data)
    at, length = regions["preview"]
    exif, xmp = _exif_and_xmp(data, at, length)
    buf = bytearray(data)
    view = memoryview(buf)
    removed: list[str] = []
    texts: set[bytes] = set()
    if exif is not None:
        tiff = view[exif[0]:exif[1]]
        before = bytes(tiff)
        removed += f1.clean_tiff(tiff, magics=(t.MAGIC_TIFF,))
        texts = _removed_texts(before, bytes(tiff))
        for thumb in t.parse(bytes(tiff), strict=True).thumbnails:
            if f1._clean_preview(tiff, thumb.offset, thumb.length):
                removed.append("the EXIF thumbnail's own metadata")
    if xmp is not None:
        view[xmp[0]:xmp[1]] = cr3._empty_xmp(xmp[1] - xmp[0])
        removed.append("XMP")
    hdr, hlen = regions["fuji header"]
    copies = identity.blank_copies(view, hdr, hlen, texts)
    if copies:
        removed.append(f"{copies} cop{'y' if copies == 1 else 'ies'} of a removed "
                       "serial or date inside the Fuji header block")
    out = bytes(buf)
    if len(out) != len(data) or out[:at] != data[:at]:
        raise ScrubError("RAF F1 changed the header")
    a, n = regions["sensor"]
    if out[a:a + n] != data[a:a + n]:
        raise ScrubError("RAF F1 altered the sensor data")
    return out, removed


def _removed_texts(before: bytes, after: bytes) -> set[bytes]:
    """Every text value the EXIF pass blanked, read off the difference itself, so
    there is no second list of fields to keep in step."""
    out: set[bytes] = set()
    tree = t.parse(before, strict=True)
    note = t.makernote(before, tree)
    for ifd in tree.ifds + (note.ifds if note else []):
        for e in ifd.entries:
            if e.type != 2:
                continue
            old = t.value_bytes(before, e).split(b"\x00")[0].strip()
            if len(old) >= identity.MIN_COPY_LEN and \
                    not t.value_bytes(after, e).strip(b"\x00"):
                out.add(old)
    return out


def residuals(data: bytes) -> list[str]:
    regions = _layout(data)
    exif, xmp = _exif_and_xmp(data, *regions["preview"])
    out: list[str] = []
    if exif is not None:
        out += [f"preview EXIF: {r}" for r in f1.residuals(data[exif[0]:exif[1]],
                                                            magics=(t.MAGIC_TIFF,))]
    if xmp is not None and not cr3._xmp_is_empty(data[xmp[0]:xmp[1]]):
        out.append("XMP survives")
    return out


def describe(data: bytes) -> dict[str, str]:
    from . import inspect as _inspect
    try:
        exif, _ = _exif_and_xmp(data, *_layout(data)["preview"])
    except Exception:                                     # noqa: BLE001
        return {}
    if exif is None:
        return {}
    return {f"preview {k}": v for k, v in
            _inspect.describe(data[exif[0]:exif[1]]).items()}
