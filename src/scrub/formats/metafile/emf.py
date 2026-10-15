"""EMF (and the EMF+ drawing inside it) at F1.

`docs/word_plan.md` §6 (W2b). An EMF is a list of records, each `type, size,
fields`; every offset in a record is relative to that record, so records can be
dropped or rebuilt without patching anything but the header's totals. Measured in
the 50 distinct EMFs inside 784 real Word documents:

- the header's description names the producing application, optionally the
  picture -- removed (the header is rebuilt; `sReserved` zeroed);
- comment records draw nothing (GDI skips them) and carry what the producing
  program chose to keep: 4 of 25 private comments were paths, 2 file names; 19
  EMFs hold a whole second copy of the picture as the WMF it was converted from
  (`WINDOWS_METAFILE`). Private comments, those copies and string comments are
  dropped; a group's description is removed and the group kept (an end-of-group
  record pairs with it);
- EMF+ records travel inside comments and ARE the drawing for GDI+ (Office draws
  them, not the EMF records). Kept, except EMF+ comment records; an EMF+ image
  object holds a whole picture file -- a JPEG with Photoshop's APP12 in the survey --
  which is cleaned by its own handler (`embedded.py`); an image split across
  continuation records is reassembled, cleaned and split again;
- bytes after the `EOF` record dropped; `nBytes`/`nRecords` recomputed (3 of 48
  real files carried a wrong record count).

Refused by name, none of them in the survey: `MULTIFORMATS` comments (may hold EPS),
records naming a colour profile's file, printer escapes, bitmaps linking or
embedding a profile, unknown record or comment types. Kept and documented: the
reference device (`szlDevice`/`szlMillimeters`, EMF+ DPI) -- the drawing's
coordinates are expressed in it.

Every scrub checks every drawing record -- EMF and EMF+ -- is in the output byte
for byte, in order.
"""
from __future__ import annotations

import struct

from ... import embedded
from ...errors import ContentError, ParseError

EMR_HEADER, EMR_EOF, EMR_GDICOMMENT = 1, 14, 70
SIGNATURE = b" EMF"
LAST_RECORD_TYPE = 122                                  # EMR_CREATECOLORSPACEW

REFUSED = {
    99: "a colour-space record (it names a colour profile's file)",
    122: "a colour-space record (it names a colour profile's file)",
    121: "a colour-matching record (it names a colour profile's file)",
    112: "an ICM profile record (a colour profile's file name or data)",
    113: "an ICM profile record (a colour profile's file name or data)",
    105: "a printer escape record",
    106: "a printer escape record",
    107: "a named printer escape record (it names a printer driver)",
}
# Records carrying a device-independent bitmap: where each (offBmi, cbBmi) pair sits.
BMI_FIELDS = {76: (84,), 77: (84,), 78: (84, 112), 79: (96, 124), 80: (48,),
              81: (48,), 114: (84,), 116: (84,), 93: (16,), 94: (16,)}
PROFILE_LINKED, PROFILE_EMBEDDED = 0x4C494E4B, 0x4D424544   # 'LINK', 'MBED'

# Public comments (identifier `GDIC`), by the kind that follows.
BEGINGROUP, ENDGROUP = 0x00000002, 0x00000003
WINDOWS_METAFILE, MULTIFORMATS = 0x80000001, 0x40000004
UNICODE_STRING, UNICODE_END = 0x00000040, 0x00000080

# EMF+ (MS-EMFPLUS): record types run 0x4001..0x403A.
PLUS_COMMENT, PLUS_OBJECT = 0x4003, 0x4008
OBJECT_IMAGE = 5
CONTINUED = 0x8000


def is_emf(data) -> bool:
    return (len(data) >= 88 and bytes(data[:4]) == b"\x01\x00\x00\x00"
            and bytes(data[40:44]) == SIGNATURE)


def _u32(b, at: int) -> int:
    return struct.unpack_from("<I", b, at)[0]


def _pad4(b: bytes) -> bytes:
    return b + b"\0" * (-len(b) % 4)


def records(data) -> tuple[list[tuple[int, int, int]], int]:
    """([(type, start, end)], end of the EOF record)."""
    if not is_emf(data):
        raise ParseError("not an EMF file")
    out, pos = [], 0
    while True:
        if pos + 8 > len(data):
            raise ParseError("EMF: no EOF record")
        rtype, size = struct.unpack_from("<II", data, pos)
        if size < 8 or size % 4 or pos + size > len(data):
            raise ParseError(f"EMF: a record at {pos} has an impossible size")
        out.append((rtype, pos, pos + size))
        pos += size
        if rtype == EMR_EOF:
            return out, pos


# --- the header ------------------------------------------------------------------

def _header_parts(rec: bytes) -> tuple[int, bytes, int, int]:
    """(fixed length: 88, 100 or 108; pixel-format bytes; description characters;
    description offset)."""
    size = len(rec)
    n_desc, off_desc = _u32(rec, 60), _u32(rec, 64)
    fixed, pix = 88, b""
    if size >= 100:
        cb_pix, off_pix = _u32(rec, 88), _u32(rec, 92)
        if (not n_desc or off_desc >= 100) and (not cb_pix or off_pix >= 100):
            fixed = 100
            if size >= 108 and (not n_desc or off_desc >= 108) and \
                    (not cb_pix or off_pix >= 108):
                fixed = 108
        if fixed >= 100 and cb_pix:
            if off_pix < fixed or off_pix + cb_pix > size:
                raise ParseError("EMF: the header's pixel format lies outside it")
            pix = rec[off_pix:off_pix + cb_pix]
    if n_desc and (off_desc < fixed or off_desc + 2 * n_desc > size):
        raise ParseError("EMF: the header's description lies outside it")
    return fixed, pix, n_desc, off_desc


def _clean_header(rec: bytes, removed: list[str]) -> bytes:
    fixed, pix, n_desc, off_desc = _header_parts(rec)
    out = bytearray(rec[:fixed])
    if n_desc:
        removed.append("the header's description (the producing application)")
    if rec[58:60] != b"\0\0":
        removed.append("the header's reserved field")
    struct.pack_into("<HII", out, 58, 0, 0, 0)
    if fixed >= 100:
        struct.pack_into("<II", out, 88, len(pix), len(out) if pix else 0)
        out += _pad4(pix)
    accounted = fixed + len(_pad4(pix)) + (len(_pad4(b"\0" * 2 * n_desc)) if n_desc else 0)
    if len(rec) > accounted:
        removed.append(f"{len(rec) - accounted} unaccounted bytes in the header")
    struct.pack_into("<I", out, 4, len(out))
    return bytes(out)


def description(data) -> str:
    rec = bytes(data[:_u32(data, 4)])
    _, _, n, off = _header_parts(rec)
    return rec[off:off + 2 * n].decode("utf-16-le", "replace").replace("\0", " | ").strip(" |")


# --- bitmaps ---------------------------------------------------------------------

def _check_bitmaps(rtype: int, rec: bytes) -> None:
    for at in BMI_FIELDS.get(rtype, ()):
        if at + 8 > len(rec):
            continue
        off, cb = _u32(rec, at), _u32(rec, at + 4)
        if cb >= 60 and off + 60 <= len(rec) and _u32(rec, off) >= 124 and \
                _u32(rec, off + 56) in (PROFILE_LINKED, PROFILE_EMBEDDED):
            raise ContentError("EMF: a bitmap links or embeds a colour profile (a file "
                               "name, or a profile with its own provenance); refused")


# --- EMF+ ------------------------------------------------------------------------

def _plus_records(blob: bytes) -> list[tuple[int, int, bytes, bytes]]:
    """[(type, flags, data, raw record)] from an EMF+ comment's payload."""
    out, pos = [], 0
    while pos < len(blob):
        if pos + 12 > len(blob):
            raise ParseError("EMF+: a record header runs past its comment")
        ptype, flags, size, dsize = struct.unpack_from("<HHII", blob, pos)
        if size < 12 or size % 4 or pos + size > len(blob) or dsize > size - 12:
            raise ParseError("EMF+: a record has an impossible size")
        if not 0x4001 <= ptype <= 0x403A:
            raise ContentError(f"EMF+: unknown record type {ptype:#06x}; refused")
        out.append((ptype, flags, blob[pos + 12:pos + 12 + dsize], blob[pos:pos + size]))
        pos += size
    return out


def _plus_record(ptype: int, flags: int, data: bytes) -> bytes:
    return struct.pack("<HHII", ptype, flags, 12 + len(_pad4(data)), len(data)) + _pad4(data)


def _is_image(ptype: int, flags: int) -> bool:
    return ptype == PLUS_OBJECT and (flags >> 8) & 0x7F == OBJECT_IMAGE


def _clean_image(obj: bytes, where: str) -> bytes:
    """An EmfPlusImage: a compressed bitmap is a whole picture file; a metafile is
    a whole WMF or EMF. Each is cleaned by its own handler."""
    if len(obj) < 8:
        raise ParseError("EMF+: an image object is truncated")
    itype = _u32(obj, 4)
    if itype == 1:                                            # bitmap
        if len(obj) < 28:
            raise ParseError("EMF+: a bitmap object is truncated")
        if _u32(obj, 24) == 1:                                # compressed: a file
            return obj[:28] + embedded.scrub_bytes(obj[28:], where)
        return obj                                            # raw pixels
    if itype == 2:                                            # metafile
        if len(obj) < 16 or 16 + _u32(obj, 12) > len(obj):
            raise ParseError("EMF+: a metafile object is truncated")
        clean = embedded.scrub_bytes(obj[16:16 + _u32(obj, 12)], where)
        return obj[:12] + struct.pack("<I", len(clean)) + clean
    if itype == 0:
        return obj
    raise ContentError(f"EMF+: an image object of unknown type {itype}; refused")


# --- the scrub -------------------------------------------------------------------

def _comment(rec: bytes, removed: list[str]):
    """One GDI comment: ('plus', [records]) to keep, bytes to keep, or None."""
    dsize = _u32(rec, 8)
    if 12 + dsize > len(rec):
        raise ParseError("EMF: a comment's data runs past its record")
    data = rec[12:12 + dsize]
    if data[:4] == b"EMF+":
        return ("plus", _plus_records(data[4:]))
    if data[:4] != b"GDIC":
        removed.append("a private comment")
        return None
    if len(data) < 8:
        raise ParseError("EMF: a public comment is truncated")
    kind = _u32(data, 4)
    if kind == ENDGROUP:
        return rec
    if kind == BEGINGROUP:
        if len(data) < 28 or 28 + 2 * _u32(data, 24) > len(data):
            raise ParseError("EMF: a group comment is truncated")
        if not _u32(data, 24):
            return rec
        removed.append("a group's description")
        return _gdi_comment(data[:24] + b"\0\0\0\0")
    if kind == WINDOWS_METAFILE:
        removed.append("a hidden copy of the picture (the WMF it was converted from)")
        return None
    if kind in (UNICODE_STRING, UNICODE_END):
        removed.append("a string comment")
        return None
    if kind == MULTIFORMATS:
        raise ContentError("EMF: a multi-format comment (it can hold an EPS copy with "
                           "its own author and date) has no rule here; refused")
    raise ContentError(f"EMF: an unknown public comment {kind:#010x}; refused")


def _gdi_comment(data: bytes) -> bytes:
    body = _pad4(data)
    return struct.pack("<III", EMR_GDICOMMENT, 12 + len(body), len(data)) + body


def _plus_comment(recs: list[bytes]) -> bytes:
    return _gdi_comment(b"EMF+" + b"".join(recs))


def _clean_plus(groups: list[list], removed: list[str],
                where: str) -> list[list[list[bytes]]]:
    """EMF+ across all the comments that carry it (an object split into
    continuation records may span comments). Returns, for each comment, the
    comments to write in its place -- its own records, then any a re-split picture
    needed -- each as a list of EMF+ records (empty ones are not written)."""
    flat = [(g, r) for g, recs in enumerate(groups) for r in recs]
    keep: dict[int, list[bytes]] = {g: [] for g in range(len(groups))}
    extra: dict[int, list[list[bytes]]] = {}          # group -> comments to insert
    i = 0
    while i < len(flat):
        g, (ptype, flags, data, raw) = flat[i]
        if ptype == PLUS_COMMENT:
            removed.append("an EMF+ comment")
            i += 1
            continue
        if not _is_image(ptype, flags):
            keep[g].append(raw)
            i += 1
            continue
        if not flags & CONTINUED:
            clean = _clean_image(data, f"{where}: an EMF+ picture")
            if clean != data:
                removed.append("an EMF+ picture's own metadata")
                raw = _plus_record(ptype, flags, clean)
            keep[g].append(raw)
            i += 1
            continue
        # Continuation: every chunk is `TotalObjectSize` + a piece; the C flag is
        # set on all but (in some writers) the last; the total ends the chain.
        if len(data) < 4:
            raise ParseError("EMF+: a continued object is truncated")
        total, chunks, j = _u32(data, 0), [], i
        while sum(len(c) for c in chunks) < total:
            if j >= len(flat):
                raise ParseError("EMF+: a continued object ends early")
            _, (t2, f2, d2, _) = flat[j]
            if t2 != PLUS_OBJECT or f2 & 0x7FFF != flags & 0x7FFF or len(d2) < 4:
                raise ParseError("EMF+: a continued object is interrupted")
            chunks.append(d2[4:])
            j += 1
        whole = b"".join(chunks)
        if len(whole) != total:
            raise ParseError("EMF+: a continued object overruns its size")
        clean = _clean_image(whole, f"{where}: an EMF+ picture")
        if clean == whole:
            for gg, r in flat[i:j]:                       # each chunk where it was
                keep[gg].append(r[3])
        else:
            removed.append("an EMF+ picture's own metadata")
            step = len(chunks[0])
            pieces = [clean[k:k + step] for k in range(0, len(clean), step)] or [b""]
            last_flag = flat[j - 1][1][1] & CONTINUED
            new = []
            for n, piece in enumerate(pieces):
                f = flags & 0x7FFF | (CONTINUED if n < len(pieces) - 1 else last_flag)
                new.append([_plus_record(ptype, f, struct.pack("<I", len(clean)) + piece)])
            # The chunks go where the object began, each in a comment of its own,
            # after whatever that comment held before it.
            extra.setdefault(g, []).extend(new)
        i = j
    return [[keep[g]] + extra.get(g, []) for g in range(len(groups))]


def _drawing(data) -> list[bytes]:
    """Every record that draws: EMF records other than the header and comments,
    and EMF+ records other than comments and pictures (checked by their own
    handlers)."""
    recs, _ = records(data)
    out = []
    for rtype, s, e in recs:
        rec = bytes(data[s:e])
        if rtype == EMR_HEADER:
            continue
        if rtype != EMR_GDICOMMENT:
            out.append(rec)
            continue
        dsize = _u32(rec, 8)
        payload = rec[12:12 + dsize]
        if payload[:4] == b"EMF+":
            out.extend(raw for t, f, _, raw in _plus_records(payload[4:])
                       if t != PLUS_COMMENT and not _is_image(t, f))
    return out


def _scrub(data, report: bool = False, where: str = "EMF"):
    recs, end = records(data)
    removed: list[str] = []
    header = _clean_header(bytes(data[recs[0][1]:recs[0][2]]), removed)
    # items: bytes (a record to write) or an int (an index into the EMF+ groups)
    items: list = []
    groups: list[list] = []
    originals: list[bytes] = []
    for rtype, s, e in recs[1:]:
        rec = bytes(data[s:e])
        if rtype in REFUSED:
            raise ContentError(f"EMF: {REFUSED[rtype]}; refused")
        if rtype == EMR_HEADER or not 1 <= rtype <= LAST_RECORD_TYPE:
            raise ContentError(f"EMF: unknown record type {rtype}; refused")
        if rtype == EMR_GDICOMMENT:
            kept = _comment(rec, removed)
            if isinstance(kept, tuple):
                items.append(len(groups))
                groups.append(kept[1])
                originals.append(rec)
            elif kept is not None:
                items.append(kept)
            continue
        _check_bitmaps(rtype, rec)
        items.append(rec)
    plus = _clean_plus(groups, removed, where) if groups else []
    body = []
    for item in items:
        if isinstance(item, int) and plus[item] == [[r[3] for r in groups[item]]]:
            body.append(originals[item])                  # unchanged, as it was
        elif isinstance(item, int):
            body.extend(_plus_comment(r) for r in plus[item] if r)
        else:
            body.append(item)
    if end < len(data):
        removed.append(f"{len(data) - end} bytes after the end record")
    out = bytearray(header + b"".join(body))
    struct.pack_into("<II", out, 48, len(out), 1 + len(body))
    result = bytes(out)
    if _drawing(data) != _drawing(result):
        raise ParseError("EMF F1 changed a drawing record; refused")
    return (result, removed) if report else result


def scrub(data, where: str = "EMF") -> bytes:
    return _scrub(data, where=where)


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
    return removed or (["the totals in the header would change"] if result != bytes(data)
                       else [])


def describe(data) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        recs, end = records(data)
    except ParseError:
        return out
    text = description(data)
    if text:
        out["Description"] = text[:120]
    comments = sum(1 for t, *_ in recs if t == EMR_GDICOMMENT)
    if comments:
        out["Comment records"] = str(comments)
    w, h, mw, mh = struct.unpack_from("<iiii", data, 72)
    out["Reference device"] = f"{w} x {h} pixels, {mw} x {mh} mm"
    if end < len(data):
        out["After the end record"] = f"{len(data) - end} bytes"
    return out
