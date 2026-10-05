"""Canon CR3 F1 — the MP4 container again, around a still photo. Nothing moves.

Measured on a real EOS R6 Mark III file before writing this (docs/p4_media_plan.md
§8.9, §10.4). A CR3 is ISOBMFF, brand `crx `:

  * `moov/uuid(85c0b687...)` is Canon's own box. Inside it `CMT1`..`CMT4` are four
    complete little TIFF files -- IFD0, the EXIF IFD, the Canon maker note and the
    GPS IFD, each as the IFD0 of its own TIFF -- beside a thumbnail (`THMB`) and
    `CTBO`, a table of ABSOLUTE offsets to the boxes below. That table is the reason
    this tier moves nothing: every edit is in place.
  * four tracks: a full-size JPEG, two raw images (`CRAW`), and `CTMD`, a timed-
    metadata track. Its sample is a list of records: a timestamp (type 1), focus
    and exposure (3-5), and TIFF records (7-9) -- one of them a Canon maker note
    whose ColorData is where LibRaw reads a CR3's white balance. Zeroed whole, the
    camera white balance became [0, 1, 0, 0] and the render lost its colour
    (measured), so CTMD is edited record by record, like a maker note;
  * top-level `uuid` boxes for XMP and for a preview (`PRVW`), and the usual movie,
    track and media headers with wall-clock creation times.

So: the TIFF field tables M16/M17 built are applied to each CMT block (EXIF fields
to CMT2's IFD0, the Canon maker-note tables to CMT3's), the GPS block is emptied to
a valid TIFF with no entries, every text value removed is searched for across the
maker note again (the 80D's CameraInfo copy), the XMP packet is rewritten as an
empty packet padded to its length -- the way XMP itself provides for -- the header
times are zeroed, the CTMD sample is zeroed, and the JPEGs go through the preview
cleaner. The raw images' bytes are checked unchanged before returning.
"""

from __future__ import annotations

import struct

from ...errors import ParseError, ScrubError
from ...standards import isobmff as iso
from ...standards import tiff_ifd as t
from . import f1, identity
from .identity import Field

CANON_UUID = bytes.fromhex("85c0b687820f11e08111f4ce462b6a48")
XMP_UUID = bytes.fromhex("be7acfcb97a942e89c71999491e3afac")
PRVW_UUID = bytes.fromhex("eaf42b5e1c984b88b9fbb7dc406e4d16")
BRAND = b"crx "

# CTMD record types F1 edits. A record is: size (4), type (2), 6 bytes, payload.
_CTMD_TIMESTAMP = 1
_CTMD_TIFF = {7: "ExifIFD", 8: "MakerNote", 9: "MakerNote"}

_EMPTY_XMP_HEAD = (b'<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>'
                   b'<x:xmpmeta xmlns:x="adobe:ns:meta/"/>')
_EMPTY_XMP_TAIL = b'<?xpacket end="w"?>'


def is_cr3(data: bytes) -> bool:
    return len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] == BRAND


def _ifd0_fields(fields, from_ifd: str) -> list[Field]:
    """The same fields, addressed to a CMT block's own IFD0."""
    return [Field("IFD0", f.tag, f.name, f.span) for f in fields if f.ifd == from_ifd]


def _empty_xmp(length: int) -> bytes:
    """A valid, empty XMP packet exactly `length` bytes long, padded with the
    whitespace XMP reserves for in-place editing."""
    pad = length - len(_EMPTY_XMP_HEAD) - len(_EMPTY_XMP_TAIL)
    if pad < 1:
        raise ScrubError("CR3: the XMP box is too small for an empty packet")
    return _EMPTY_XMP_HEAD + b"\n" + b" " * (pad - 1) + _EMPTY_XMP_TAIL


def _xmp_is_empty(body: bytes) -> bool:
    middle = body[len(_EMPTY_XMP_HEAD):len(body) - len(_EMPTY_XMP_TAIL)]
    return (body.startswith(_EMPTY_XMP_HEAD) and body.endswith(_EMPTY_XMP_TAIL)
            and not middle.strip(b" \n"))


def _jpeg_at(buf, start: int, end: int) -> tuple[int, int] | None:
    at = bytes(buf[start:min(end, start + 64)]).find(b"\xff\xd8\xff")
    return (start + at, end - start - at) if at != -1 else None


def scrub(data: bytes) -> bytes:
    try:
        return _scrub(data)[0]
    except (struct.error, IndexError, ValueError) as e:
        raise ParseError(f"CR3: malformed file ({type(e).__name__}: {e})") from None


def scrub_with_report(data: bytes) -> tuple[bytes, list[str]]:
    try:
        return _scrub(data)
    except (struct.error, IndexError, ValueError) as e:
        raise ParseError(f"CR3: malformed file ({type(e).__name__}: {e})") from None


def _layout(data: bytes):
    if not is_cr3(data):
        raise ParseError("CR3: not a Canon CR3 (brand crx)")
    tops = iso.scan(data)
    moovs = [b for b in tops if b.type == b"moov"]
    if len(moovs) != 1:
        raise ParseError("CR3: expected exactly one moov")
    moov_h = moovs[0]
    moov = iso.parse(data[moov_h.offset:moov_h.end])[0]
    canon = next((c for c in moov.children if c.type == b"uuid" and
                  data[moov_h.offset + c.offset + 8:moov_h.offset + c.offset + 24]
                  == CANON_UUID), None)
    if canon is None:
        raise ParseError("CR3: no Canon metadata box")
    base = moov_h.offset + canon.offset + canon.header_len
    blocks = {k.type: (base + k.offset + k.header_len, base + k.end)
              for k in iso.parse(canon.payload)}
    return tops, moov_h, moov, blocks


def _scrub(data: bytes) -> tuple[bytes, list[str]]:
    tops, moov_h, moov, blocks = _layout(data)
    buf = bytearray(data)
    view = memoryview(buf)
    removed: list[str] = []
    texts: set[bytes] = set()

    plan = {
        b"CMT1": [Field("IFD0", tag, name) for tag, name in
                  f1._BLANK_ANYWHERE.items()] + _ifd0_fields(identity.COMMON, "IFD0"),
        b"CMT2": [Field("IFD0", tag, name) for tag, name in
                  f1._BLANK_ANYWHERE.items()] + _ifd0_fields(identity.COMMON,
                                                             "ExifIFD"),
        b"CMT3": _ifd0_fields(identity.MAKERNOTE_FIELDS["canon"]
                              + f1.MAKERNOTE_DATES["canon"], "MakerNote"),
    }
    for name, fields in plan.items():
        if name not in blocks:
            raise ParseError(f"CR3: no {name.decode()} block")
        start, end = blocks[name]
        tiff = view[start:end]
        tree = t.parse(bytes(tiff), strict=True)
        texts |= identity.apply_fields(tiff, {"IFD0": tree.ifd("IFD0")}, fields,
                                       removed)
        for tag, label in f1._REMOVE_ANYWHERE.items():
            if t.remove_entry(tiff, tree.ifd("IFD0"), tag):
                removed.append(label)
    start, end = blocks[b"CMT3"]
    copies = identity.blank_copies(view[start:end], 0, end - start, texts)
    if copies:
        removed.append(f"{copies} further cop{'y' if copies == 1 else 'ies'} of a "
                       "removed value inside the maker note")

    if b"CMT4" in blocks:                                  # GPS: an empty TIFF
        start, end = blocks[b"CMT4"]
        gps = t.parse(bytes(view[start:end]), strict=True).ifd("IFD0")
        if gps is not None and gps.entries:
            order = gps.order
            t.zero(view, start + 8, end - start - 8)
            view[start + 4:start + 8] = struct.pack(order + "I", 8)
            removed.append(f"GPS ({len(gps.entries)} fields)")

    for box in moov.walk():                                # header times
        if box.type in iso.TIMESTAMP_BOXES:
            at = moov_h.offset + box.offset + box.header_len
            view[at:at + len(box.payload)] = iso.zero_timestamps(box.payload, box.type)
    removed.append("movie, track and media creation times")

    for b in tops:
        ext = data[b.offset + 8:b.offset + 24]
        if b.type == b"uuid" and ext == XMP_UUID:
            at = b.offset + b.header_len
            view[at:b.end] = _empty_xmp(b.end - at)
            removed.append("XMP")
        elif b.type in (b"free", b"skip"):
            t.zero(view, b.offset + b.header_len, b.end - b.offset - b.header_len)

    jpegs: list[tuple[str, int, int]] = []
    if b"THMB" in blocks:
        jpegs.append(("thumbnail", *blocks[b"THMB"]))
    for b in tops:
        if b.type == b"uuid" and data[b.offset + 8:b.offset + 24] == PRVW_UUID:
            jpegs.append(("preview", b.offset + b.header_len, b.end))

    raw_regions: list[tuple[int, int]] = []
    for trak in (c for c in moov.children if c.type == b"trak"):
        handler = trak.find(b"mdia/hdlr").payload[8:12]
        stsd = trak.find(b"mdia/minf/stbl/stsd")
        chunks = iso.chunks(trak.find(b"mdia/minf/stbl"), limit=len(data))
        spans = [(c.offset, c.size) for c in chunks if c.sample_sizes]
        if handler == b"meta":                             # CTMD
            for at, n in spans:
                removed += _scrub_ctmd(view[at:at + n], texts)
        elif bytes(data[spans[0][0]:spans[0][0] + 3]) == b"\xff\xd8\xff":
            jpegs += [("full-size JPEG", at, at + n) for at, n in spans]
        elif stsd is not None and stsd.payload[12:16] == b"CRAW":
            raw_regions += spans
        else:
            raise ParseError(f"CR3: a track not modelled ({handler!r})")

    for where, start, end in jpegs:
        found = _jpeg_at(view, start, end)
        if found and f1._clean_preview(buf, *found):
            removed.append(f"{where}: its own metadata")

    out = bytes(buf)
    if len(out) != len(data):
        raise ScrubError("CR3 F1 changed the file's length")
    for at, n in raw_regions:
        if out[at:at + n] != data[at:at + n]:
            raise ScrubError(f"CR3 F1 altered raw image data at {at}")
    return out, removed


def _ctmd_records(sample) -> list[tuple[int, int, int]]:
    """(offset, size, type) of each record in a CTMD sample."""
    out, pos = [], 0
    while pos + 12 <= len(sample):
        size, kind = struct.unpack_from("<IH", sample, pos)
        if size < 12 or pos + size > len(sample):
            raise ParseError(f"CR3: CTMD record at {pos} declares size {size}")
        out.append((pos, size, kind))
        pos += size
    return out


def _ctmd_tiff(record):
    """The TIFF inside a CTMD record: after the 12-byte header, a size and the tag
    it stands for (0x8769 EXIF, 0x927C maker note), then the TIFF. Its IFD0 is all
    that matters; the next-IFD pointer after it is junk, so it is read non-strictly."""
    view = record[20:]
    return view, t.parse(bytes(view), strict=False).ifd("IFD0")


def _scrub_ctmd(sample, texts: set[bytes]) -> list[str]:
    removed: list[str] = []
    for pos, size, kind in _ctmd_records(sample):
        record = sample[pos:pos + size]
        if kind == _CTMD_TIMESTAMP:
            t.zero(record, 12, size - 12)
            removed.append("CTMD timestamp")
        elif kind in _CTMD_TIFF and bytes(record[20:24]) in (b"II*\x00", b"MM\x00*"):
            view, ifd0 = _ctmd_tiff(record)
            fields = (identity.MAKERNOTE_FIELDS["canon"]
                      if _CTMD_TIFF[kind] == "MakerNote" else identity.COMMON)
            hit: list[str] = []
            texts |= identity.apply_fields(view, {"IFD0": ifd0},
                                           _ifd0_fields(fields, _CTMD_TIFF[kind]), hit)
            copies = identity.blank_copies(view, 0, len(view), texts)
            if hit or copies:
                removed.append(f"CTMD record {kind}: " + ", ".join(hit or ["a copy"]))
    return removed


def describe(data: bytes) -> dict[str, str]:
    """For the report: the CMT blocks' identity, date and GPS fields, by block."""
    from ...standards import tiff_values as tv
    out: dict[str, str] = {}
    try:
        tops, moov_h, moov, blocks = _layout(data)
    except Exception:                                     # noqa: BLE001
        return out
    names = {f.tag: f.name for f in identity.COMMON + identity.MAKERNOTE_FIELDS["canon"]
             + f1.MAKERNOTE_DATES["canon"]}
    names.update(f1._BLANK_ANYWHERE)
    for block in (b"CMT1", b"CMT2", b"CMT3", b"CMT4"):
        if block not in blocks:
            continue
        start, end = blocks[block]
        try:
            ifd0 = t.parse(data[start:end], strict=False).ifd("IFD0")
        except Exception:                                 # noqa: BLE001
            continue
        for e in ifd0.entries if ifd0 is not None else ():
            name = (tv.tag_name("GPS", e.tag) if block == b"CMT4" else names.get(e.tag))
            if name is None:
                continue
            value = tv.decode(data[start:end], ifd0.order, e)
            if value:
                out[f"{block.decode()}:{name}"] = value
    for b in tops:
        if (b.type == b"uuid" and data[b.offset + 8:b.offset + 24] == XMP_UUID
                and not _xmp_is_empty(data[b.offset + b.header_len:b.end])):
            out["XMP"] = f"({b.end - b.offset - b.header_len} bytes)"
    return out


def residuals(data: bytes) -> list[str]:
    out: list[str] = []
    tops, moov_h, moov, blocks = _layout(data)
    checks = {b"CMT1": (identity.COMMON, "IFD0"), b"CMT2": (identity.COMMON, "ExifIFD"),
              b"CMT3": (identity.MAKERNOTE_FIELDS["canon"], "MakerNote")}
    for name, (fields, src) in checks.items():
        start, end = blocks[name]
        ifd0 = t.parse(data[start:end], strict=True).ifd("IFD0")
        for f in _ifd0_fields(fields, src):
            e = ifd0.get(f.tag)
            if e is None:
                continue
            value = t.value_bytes(data[start:end], e)
            if f.span:
                value = value[f.span[0]:f.span[0] + f.span[1]]
            if value.strip(b"\x00"):
                out.append(f"{f.name} survives in {name.decode()}")
        for tag, label in f1._BLANK_ANYWHERE.items():
            e = ifd0.get(tag)
            if e is not None and t.value_bytes(data[start:end], e).strip(b"\x00 "):
                out.append(f"{label} survives in {name.decode()}")
    if b"CMT4" in blocks:
        start, end = blocks[b"CMT4"]
        if t.parse(data[start:end], strict=True).ifd("IFD0").entries:
            out.append("GPS survives in CMT4")
    for box in moov.walk():
        if box.type in iso.TIMESTAMP_BOXES:
            width = 8 if box.payload[0] == 1 else 4
            if box.payload[4:4 + 2 * width].strip(b"\x00"):
                out.append(f"{box.type.decode()} timestamps survive")
    for b in tops:
        if b.type == b"uuid" and data[b.offset + 8:b.offset + 24] == XMP_UUID:
            if not _xmp_is_empty(data[b.offset + b.header_len:b.end]):
                out.append("XMP survives")
    for trak in (c for c in moov.children if c.type == b"trak"):
        if trak.find(b"mdia/hdlr").payload[8:12] != b"meta":
            continue
        for c in iso.chunks(trak.find(b"mdia/minf/stbl"), limit=len(data)):
            sample = data[c.offset:c.end]
            for pos, size, kind in _ctmd_records(sample):
                record = sample[pos:pos + size]
                if kind == _CTMD_TIMESTAMP and record[12:].strip(b"\x00"):
                    out.append("the CTMD timestamp survives")
                if _CTMD_TIFF.get(kind) == "MakerNote" and record[20:24] in (
                        b"II*\x00", b"MM\x00*"):
                    view, ifd0 = _ctmd_tiff(record)
                    e = ifd0.get(0x000D)
                    if e is not None and t.value_bytes(view, e).strip(b"\x00"):
                        out.append(f"CameraInfo survives in CTMD record {kind}")
    return out
