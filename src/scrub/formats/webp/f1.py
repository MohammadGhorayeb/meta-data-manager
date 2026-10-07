"""WebP at F1: the RIFF rebuilt from the chunks a picture is made of.

Phase 6 M2 (`docs/p6_tail_plan.md` §1, §4 D2). A WebP is a RIFF of chunks; metadata
lives in its own chunks (`EXIF`, `XMP `) beside the image (`VP8 `, `VP8L`, `ALPH`,
or `ANIM` + `ANMF` frames), announced by flags in `VP8X`. No chunk holds an absolute
offset, so F1 rebuilds the file from an ALLOWLIST of image chunks -- an unknown
chunk is something a reader skips, which means it could hold anything -- inside
animation frames too; clears the EXIF and XMP flags; and writes the RIFF size.

Bytes after the RIFF's declared end are dropped: no reader sees them, and anything
can hide there. The ICC profile follows TIFF's rule (`icc.is_standard`): a published
colour space stays byte for byte, anything else is sanitized. The coded image is
copied bit for bit -- F1 never re-encodes -- so a lossy picture keeps its encoder's
fingerprint (A2), as a JPEG does.
"""
from __future__ import annotations

import struct

from ...errors import ParseError
from ...standards import icc

KEEP = (b"VP8X", b"ICCP", b"ANIM", b"ANMF", b"ALPH", b"VP8 ", b"VP8L")
FRAME_KEEP = (b"ALPH", b"VP8 ", b"VP8L")
FLAG_ICC, FLAG_ALPHA, FLAG_EXIF, FLAG_XMP, FLAG_ANIM = 0x20, 0x10, 0x08, 0x04, 0x02


def is_webp(data: bytes) -> bool:
    return data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def chunks(data, start: int, end: int) -> list[tuple[bytes, int, int]]:
    """(fourcc, payload offset, payload length) for each chunk in [start, end)."""
    out, pos = [], start
    while pos < end:
        if pos + 8 > end:
            raise ParseError("WebP: a chunk header runs past its container")
        fourcc = bytes(data[pos:pos + 4])
        size = struct.unpack_from("<I", data, pos + 4)[0]
        if pos + 8 + size > end:
            raise ParseError(f"WebP: chunk {fourcc!r} runs past its container")
        out.append((fourcc, pos + 8, size))
        pos += 8 + size + (size & 1)
    return out


def _layout(data: bytes) -> tuple[int, list[tuple[bytes, int, int]]]:
    if not is_webp(data) or len(data) < 20:
        raise ParseError("not a WebP file")
    riff_end = 8 + struct.unpack_from("<I", data, 4)[0]
    if riff_end > len(data) or riff_end < 20:
        raise ParseError("WebP: the RIFF size does not fit the file")
    found = chunks(data, 12, riff_end)
    if not found or found[0][0] not in (b"VP8X", b"VP8 ", b"VP8L"):
        raise ParseError("WebP: the first chunk is not an image or VP8X header")
    return riff_end, found


def _chunk(fourcc: bytes, payload: bytes) -> bytes:
    return fourcc + struct.pack("<I", len(payload)) + payload + b"\0" * (len(payload) & 1)


def _frame(data, at: int, size: int) -> tuple[bytes, list[str]]:
    """An ANMF payload: its 16-byte header, then only the image sub-chunks."""
    if size < 16:
        raise ParseError("WebP: an animation frame is shorter than its header")
    dropped, body = [], b""
    for fourcc, p, n in chunks(data, at + 16, at + size):
        if fourcc in FRAME_KEEP:
            body += _chunk(fourcc, bytes(data[p:p + n]))
        else:
            dropped.append(f"an unknown {fourcc.decode('latin-1')!r} chunk in a frame")
    return bytes(data[at:at + 16]) + body, dropped


def _scrub(data: bytes, report: bool = False):
    riff_end, found = _layout(data)
    removed, body = [], b""
    if riff_end < len(data):
        removed.append(f"{len(data) - riff_end} bytes after the end of the file")
    for fourcc, at, n in found:
        payload = bytes(data[at:at + n])
        if fourcc == b"EXIF":
            removed.append("EXIF")
        elif fourcc == b"XMP ":
            removed.append("XMP")
        elif fourcc not in KEEP:
            removed.append(f"an unknown {fourcc.decode('latin-1')!r} chunk")
        elif fourcc == b"ANMF":
            payload, dropped = _frame(data, at, n)
            removed += dropped
            body += _chunk(fourcc, payload)
        elif fourcc == b"ICCP":
            if icc.looks_like_profile(payload) and not icc.is_standard(payload):
                payload = icc.sanitize(payload)
                removed.append("ICC profile provenance (kept for colour)")
            body += _chunk(fourcc, payload)
        elif fourcc == b"VP8X":
            if n < 10:
                raise ParseError("WebP: VP8X is shorter than its header")
            flags = payload[0] & ~(FLAG_EXIF | FLAG_XMP)
            body += _chunk(fourcc, bytes([flags]) + payload[1:])
        else:
            body += _chunk(fourcc, payload)
    out = b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + body
    _check_image_untouched(data, found, out)
    return (out, removed) if report else out


def _image_payloads(data, found) -> list[bytes]:
    out = []
    for fourcc, at, n in found:
        if fourcc in (b"VP8 ", b"VP8L", b"ALPH"):
            out.append(bytes(data[at:at + n]))
        elif fourcc == b"ANMF":
            out.append(bytes(data[at:at + 16]))
            out += [bytes(data[p:p + m]) for f, p, m in chunks(data, at + 16, at + n)
                    if f in FRAME_KEEP]
    return out


def _check_image_untouched(data: bytes, found, out: bytes) -> None:
    """The tier's promise on the bytes: the coded image -- every frame, every
    alpha plane -- is in the output exactly as it was in the input, in order."""
    _, again = _layout(out)
    if _image_payloads(data, found) != _image_payloads(out, again):
        raise ParseError("WebP F1 changed the coded image; refused")


def scrub(data: bytes) -> bytes:
    return _scrub(data)


def scrub_with_report(data: bytes) -> tuple[bytes, list[str]]:
    return _scrub(data, report=True)


def residuals(data: bytes) -> list[str]:
    riff_end, found = _layout(data)
    out = []
    if riff_end != len(data):
        out.append("bytes follow the end of the RIFF")
    for fourcc, at, n in found:
        if fourcc not in KEEP:
            out.append(f"a {fourcc.decode('latin-1')!r} chunk survives")
        if fourcc == b"ANMF":
            out += [f"a {f.decode('latin-1')!r} chunk survives in a frame"
                    for f, _, _ in chunks(data, at + 16, at + n) if f not in FRAME_KEEP]
        if fourcc == b"VP8X" and data[at] & (FLAG_EXIF | FLAG_XMP):
            out.append("VP8X still announces EXIF or XMP")
        if fourcc == b"ICCP":
            p = bytes(data[at:at + n])
            if icc.looks_like_profile(p) and not icc.is_standard(p) \
                    and icc.sanitize(p) != p:
                out.append("the ICC profile still carries provenance")
    return out


def describe(data: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        riff_end, found = _layout(data)
    except ParseError:
        return out
    out["Chunks"] = " ".join(f.decode("latin-1").strip() for f, _, _ in found)
    for fourcc, at, n in found:
        if fourcc in (b"EXIF", b"XMP "):
            out[fourcc.decode().strip()] = f"{n} bytes"
        if fourcc == b"ICCP":
            out["ICC profile"] = icc.description(bytes(data[at:at + n])) or f"{n} bytes"
    if riff_end < len(data):
        out["After the end"] = f"{len(data) - riff_end} bytes"
    return out
