"""WMF at F1.

`docs/word_plan.md` §6 (W2b). A WMF is an optional 22-byte "placeable" header, a
fixed header, and records of `size in words, function, parameters`; nothing in it
is an offset. Measured in the 361 distinct WMFs inside 784 real Word documents:

- comments are `META_ESCAPE` records with escape `MFCOMMENT`. One kind draws: the
  'WMFC' chunks are **the EMF the picture was made from**, embedded whole, and
  Windows and LibreOffice draw that instead of the WMF records when it is present.
  It is reassembled, cleaned by the EMF rules (`emf.py`) and chunked again, in the
  original chunk sizes; its checksum is the value that makes the metafile's 16-bit
  words sum to zero (measured: both real files), written in the first chunk only,
  as Word does. Every other comment is private ('MGXI' and binary in the survey)
  and dropped;
- bytes after `META_EOF` dropped (3 files, 1 non-zero);
- the placeable header's reserved fields zeroed and its checksum recomputed; the
  header's `Size` and `MaxRecord` recomputed.

Refused by name: other escapes (PostScript passthroughs carry their own `%%For`),
bitmaps linking or embedding a colour profile, unknown functions. Kept and
documented: the placeable header's units per inch -- the picture's size (94, 95 and
129 appear beside 96: a screen's resolution).

Every scrub checks every drawing record is in the output byte for byte, in order.
"""
from __future__ import annotations

import struct

from ...errors import ContentError, ParseError
from . import emf

PLACEABLE_KEY = b"\xd7\xcd\xc6\x9a"
META_EOF, META_ESCAPE = 0x0000, 0x0626
MFCOMMENT = 0x000F
WMFC = b"WMFC"
WMFC_HEAD = 34                                  # identifier .. EnhancedMetafileDataSize

# MS-WMF RecordType: every function a metafile may hold.
FUNCTIONS = {
    0x0000, 0x0035, 0x0037, 0x0102, 0x0103, 0x0104, 0x0105, 0x0106, 0x0107, 0x0108,
    0x0127, 0x0139, 0x0142, 0x0149, 0x0201, 0x0209, 0x0211, 0x0213, 0x0214, 0x0220,
    0x0228, 0x0231, 0x0234, 0x0324, 0x0325, 0x020A, 0x020B, 0x020C, 0x020D, 0x020E,
    0x020F, 0x0410, 0x0412, 0x0415, 0x0416, 0x0418, 0x0419, 0x0429, 0x0436, 0x0521,
    0x0538, 0x0548, 0x041B, 0x041F, 0x061C, 0x061D, 0x001E, 0x081A, 0x0B23, 0x0626,
    0x012A, 0x012B, 0x012C, 0x012D, 0x012E, 0x0817, 0x0830, 0x0922, 0x0A32, 0x0D33,
    0x0940, 0x0B41, 0x0F43, 0x01F0, 0x00F7, 0x01F9, 0x02FA, 0x02FB, 0x02FC, 0x06FF,
}
# Device-independent bitmaps: where the DIB starts in each record that carries one.
DIB_AT = {0x0940: 22, 0x0B41: 26, 0x0F43: 28, 0x0D33: 24, 0x0142: 10}


def is_wmf(data) -> bool:
    if bytes(data[:4]) == PLACEABLE_KEY:
        return len(data) >= 40 and _is_header(data, 22)
    return _is_header(data, 0)


def _is_header(data, at: int) -> bool:
    if len(data) < at + 18:
        return False
    htype, hsize, version = struct.unpack_from("<HHH", data, at)
    return htype in (1, 2) and hsize == 9 and version in (0x0100, 0x0300)


def _parse(data):
    """(placeable header or b"", header, [(function, start, end)], end of EOF)."""
    if not is_wmf(data):
        raise ParseError("not a WMF file")
    at = 22 if bytes(data[:4]) == PLACEABLE_KEY else 0
    pos, recs = at + 18, []
    while True:
        if pos + 6 > len(data):
            raise ParseError("WMF: no end record")
        size, fn = struct.unpack_from("<IH", data, pos)
        if size < 3 or pos + 2 * size > len(data):
            raise ParseError(f"WMF: a record at {pos} has an impossible size")
        recs.append((fn, pos, pos + 2 * size))
        pos += 2 * size
        if fn == META_EOF:
            return bytes(data[:at]), bytes(data[at:at + 18]), recs, pos


def _escape(rec: bytes) -> tuple[int, bytes]:
    if len(rec) < 10:
        raise ParseError("WMF: an escape record is truncated")
    esc, count = struct.unpack_from("<HH", rec, 6)
    if 10 + count > len(rec):
        raise ParseError("WMF: an escape's data runs past its record")
    return esc, rec[10:10 + count]


def _escape_record(esc: int, data: bytes) -> bytes:
    body = struct.pack("<HH", esc, len(data)) + data + b"\0" * (len(data) % 2)
    return struct.pack("<IH", (6 + len(body)) // 2, META_ESCAPE) + body


def _check_bitmap(fn: int, rec: bytes) -> None:
    at = DIB_AT.get(fn)
    if at is None or len(rec) < at + 60:
        return
    if fn in (0x0940, 0x0B41) and len(rec) // 2 == (fn >> 8) + 3:
        return                                            # the form with no bitmap
    if emf._u32(rec, at) >= 124 and \
            emf._u32(rec, at + 56) in (emf.PROFILE_LINKED, emf.PROFILE_EMBEDDED):
        raise ContentError("WMF: a bitmap links or embeds a colour profile (a file "
                           "name, or a profile with its own provenance); refused")


def _embedded_emf(chunks: list[bytes]) -> tuple[bytes, list[int]]:
    """The EMF the 'WMFC' comments carry, and the chunk sizes they used."""
    pieces, sizes, total = [], [], None
    for c in chunks:
        if len(c) < WMFC_HEAD:
            raise ParseError("WMF: an embedded-EMF comment is truncated")
        ctype, version, _, _, _, cur, _, size = struct.unpack_from("<IIHIIIII", c, 4)
        if ctype != 1 or version != 0x00010000:
            raise ContentError("WMF: an embedded-EMF comment of an unknown version; "
                               "refused")
        if total not in (None, size) or WMFC_HEAD + cur > len(c):
            raise ParseError("WMF: the embedded EMF's chunks disagree")
        total = size
        pieces.append(c[WMFC_HEAD:WMFC_HEAD + cur])
        sizes.append(cur)
    whole = b"".join(pieces)
    if len(whole) != total or not emf.is_emf(whole):
        raise ContentError("WMF: the embedded EMF is incomplete; refused rather than "
                           "dropped, because readers draw it instead of the WMF")
    return whole, sizes


def _wmfc_chunks(clean: bytes, sizes: list[int]) -> list[bytes]:
    pieces, at = [], 0
    for n in sizes:
        if at >= len(clean):
            break
        pieces.append(clean[at:at + n])
        at += n
    if at < len(clean):                       # never: F1 only removes, but be exact
        raise ParseError("WMF: the cleaned EMF outgrew its chunks")
    out, remaining = [], len(clean)
    for piece in pieces:
        remaining -= len(piece)
        out.append(WMFC + struct.pack("<IIHIIIII", 1, 0x00010000, 0, 0, len(pieces),
                                      len(piece), remaining, len(clean)) + piece)
    return out


def _placeable(head: bytes) -> bytes:
    out = bytearray(head)
    struct.pack_into("<H", out, 4, 0)                     # HWmf: must be 0
    struct.pack_into("<I", out, 16, 0)                    # Reserved
    check = 0
    for (w,) in struct.iter_unpack("<H", bytes(out[:20])):
        check ^= w
    struct.pack_into("<H", out, 20, check)
    return bytes(out)


def _drawing(data) -> list[bytes]:
    _, _, recs, _ = _parse(data)
    out = []
    for fn, s, e in recs:
        rec = bytes(data[s:e])
        if fn == META_ESCAPE and _escape(rec)[0] == MFCOMMENT:
            continue
        out.append(rec)
    return out


def _scrub(data, report: bool = False):
    placeable, header, recs, end = _parse(data)
    removed: list[str] = []
    body: list[bytes] = []
    wmfc: list[bytes] = []
    wmfc_at = None
    for fn, s, e in recs:
        rec = bytes(data[s:e])
        if fn not in FUNCTIONS:
            raise ContentError(f"WMF: unknown record function {fn:#06x}; refused")
        if fn == META_ESCAPE:
            esc, payload = _escape(rec)
            if esc != MFCOMMENT:
                raise ContentError(f"WMF: escape {esc:#06x} has no rule here (a "
                                   "PostScript passthrough carries its own author); "
                                   "refused")
            if payload[:4] == WMFC:
                if wmfc_at is None:
                    wmfc_at = len(body)
                wmfc.append(payload)
            else:
                removed.append("a private comment")
            continue
        _check_bitmap(fn, rec)
        body.append(rec)
    first = None
    if wmfc:
        whole, sizes = _embedded_emf(wmfc)
        clean, inner = emf.scrub_with_report(whole)
        removed += [f"in the embedded EMF: {r}" for r in inner]
        records = [_escape_record(MFCOMMENT, c) for c in _wmfc_chunks(clean, sizes)]
        body[wmfc_at:wmfc_at] = records
        first = wmfc_at
    if end < len(data):
        removed.append(f"{len(data) - end} bytes after the end record")
    if placeable and placeable != _placeable(placeable):
        removed.append("the placeable header's reserved fields")
    out = bytearray(header + b"".join(body))
    struct.pack_into("<I", out, 6, len(out) // 2)                       # Size
    struct.pack_into("<I", out, 12, max(len(r) // 2 for r in body))    # MaxRecord
    struct.pack_into("<H", out, 16, 0)                                  # NumberOfMembers
    if first is not None:
        # The first chunk's checksum makes the metafile's words sum to zero.
        at = len(header) + sum(len(r) for r in body[:first]) + 10 + 12
        total = sum(w for (w,) in struct.iter_unpack("<H", bytes(out))) & 0xFFFF
        struct.pack_into("<H", out, at, -total & 0xFFFF)
    result = (_placeable(placeable) if placeable else b"") + bytes(out)
    if _drawing(data) != _drawing(result):
        raise ParseError("WMF F1 changed a drawing record; refused")
    return (result, removed) if report else result


def scrub(data) -> bytes:
    return _scrub(data)


def scrub_with_report(data) -> tuple[bytes, list[str]]:
    return _scrub(data, report=True)


def residuals(data) -> list[str]:
    """What a clean would still remove: the clean is a fixed point."""
    try:
        result, removed = _scrub(data, report=True)
    except ContentError as exc:
        return [str(exc)]
    except ParseError as exc:
        return [f"unreadable: {exc}"]
    return removed or (["the header's totals would change"] if result != bytes(data)
                       else [])


def describe(data) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        placeable, _, recs, end = _parse(data)
    except ParseError:
        return out
    if placeable:
        out["Units per inch"] = str(struct.unpack_from("<H", placeable, 14)[0])
    comments = [r for f, s, e in recs if f == META_ESCAPE
                for r in [_escape(bytes(data[s:e]))] if r[0] == MFCOMMENT]
    if any(p[:4] == WMFC for _, p in comments):
        out["Embedded EMF"] = "yes (drawn instead of the WMF records)"
    private = sum(1 for _, p in comments if p[:4] != WMFC)
    if private:
        out["Private comments"] = str(private)
    if end < len(data):
        out["After the end record"] = f"{len(data) - end} bytes"
    return out
