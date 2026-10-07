"""GIF at F1: the block stream rebuilt from what draws the picture.

Phase 6 M3 (`docs/p6_tail_plan.md` §1, §4 D2). A GIF is a header, a screen
descriptor, a colour table and a stream of blocks; nothing in it is an offset, so
F1 rebuilds the stream from an ALLOWLIST:

- kept: every image (descriptor, local colour table, LZW data), every graphic
  control extension (frame timing, transparency, disposal), plain-text extensions
  (text the decoder renders -- content), and the looping extensions
  (`NETSCAPE2.0`, `ANIMEXTS1.0`);
- kept, sanitized: an ICC profile (`ICCRGBG1012`), by TIFF's rule -- a published
  colour space byte for byte, anything else with its provenance zeroed, the same
  length, so its sub-block framing stands;
- dropped: comment extensions, XMP (`XMP DataXMP`), every other application or
  unknown extension, and anything after the trailer.

The header's version is left as it is. Every scrub checks the images are in the
output byte for byte, in order.
"""
from __future__ import annotations

from ...errors import ParseError
from ...standards import icc

LOOP_APPS = (b"NETSCAPE2.0", b"ANIMEXTS1.0")
ICC_APP = b"ICCRGBG1012"
XMP_APP = b"XMP DataXMP"


def is_gif(data: bytes) -> bool:
    return data[:6] in (b"GIF87a", b"GIF89a")


def _sub_blocks(data, pos: int) -> int:
    """Position after a sub-block sequence starting at `pos`."""
    while True:
        if pos >= len(data):
            raise ParseError("GIF: a block runs past the end of the file")
        n = data[pos]
        pos += 1
        if n == 0:
            return pos
        pos += n


def _blocks(data) -> tuple[int, list[tuple[str, int, int, bytes]], int]:
    """(end of the header and global colour table, [(kind, start, end, label)],
    position after the trailer)."""
    if not is_gif(data) or len(data) < 13:
        raise ParseError("not a GIF file")
    flags = data[10]
    pos = 13 + (3 * (2 << (flags & 7)) if flags & 0x80 else 0)
    head_end = pos
    out = []
    while True:
        if pos >= len(data):
            raise ParseError("GIF: no trailer")
        b = data[pos]
        if b == 0x3B:
            return head_end, out, pos + 1
        if b == 0x21:
            if pos + 2 > len(data):
                raise ParseError("GIF: an extension runs past the end of the file")
            label = data[pos + 1]
            app = b""
            if label == 0xFF and pos + 3 < len(data):
                app = bytes(data[pos + 3:pos + 3 + data[pos + 2]])
            end = _sub_blocks(data, pos + 2)
            kind = {0xF9: "gce", 0xFE: "comment", 0x01: "text", 0xFF: "app"}.get(
                label, "unknown")
            out.append((kind, pos, end, app))
            pos = end
        elif b == 0x2C:
            if pos + 10 > len(data):
                raise ParseError("GIF: an image descriptor runs past the end")
            fl = data[pos + 9]
            p = pos + 10 + (3 * (2 << (fl & 7)) if fl & 0x80 else 0)
            end = _sub_blocks(data, p + 1)                # after the LZW code size
            out.append(("image", pos, end, b""))
            pos = end
        else:
            raise ParseError(f"GIF: unknown block {b:#04x} at {pos}")


def _sanitized_icc(data, start: int, end: int) -> bytes:
    """The ICC application extension with its profile sanitized in place: the
    profile spans the sub-blocks, and sanitizing keeps its length, so the framing
    is rewritten around the same sizes."""
    pos = start + 2 + 1 + data[start + 2]                 # past the app identifier
    sizes, payload = [], b""
    while data[pos]:
        n = data[pos]
        sizes.append(n)
        payload += bytes(data[pos + 1:pos + 1 + n])
        pos += 1 + n
    if not icc.looks_like_profile(payload) or icc.is_standard(payload):
        return bytes(data[start:end])
    clean = icc.sanitize(payload)
    out, at = bytearray(data[start:start + 3 + data[start + 2]]), 0
    for n in sizes:
        out += bytes([n]) + clean[at:at + n]
        at += n
    return bytes(out + b"\0")


def _scrub(data: bytes, report: bool = False):
    head_end, blocks, after = _blocks(data)
    out, removed = bytearray(data[:head_end]), []
    for kind, start, end, app in blocks:
        block = bytes(data[start:end])
        if kind in ("image", "gce", "text"):
            out += block
        elif kind == "app" and app in LOOP_APPS:
            out += block
        elif kind == "app" and app == ICC_APP:
            clean = _sanitized_icc(data, start, end)
            if clean != block:
                removed.append("ICC profile provenance (kept for colour)")
            out += clean
        elif kind == "comment":
            removed.append("a comment")
        elif kind == "app" and app == XMP_APP:
            removed.append("XMP")
        elif kind == "app":
            removed.append(f"an application extension {app.decode('latin-1')!r}")
        else:
            removed.append("an unknown extension")
    out += b"\x3b"
    if after < len(data):
        removed.append(f"{len(data) - after} bytes after the trailer")
    result = bytes(out)
    if _images(data) != _images(result):
        raise ParseError("GIF F1 changed an image; refused")
    return (result, removed) if report else result


def _images(data) -> list[bytes]:
    head_end, blocks, _ = _blocks(data)
    return [bytes(data[:head_end])] + [bytes(data[s:e]) for k, s, e, _ in blocks
                                       if k in ("image", "gce", "text")]


def scrub(data: bytes) -> bytes:
    return _scrub(data)


def scrub_with_report(data: bytes) -> tuple[bytes, list[str]]:
    return _scrub(data, report=True)


def residuals(data: bytes) -> list[str]:
    _, blocks, after = _blocks(data)
    out = []
    if after != len(data):
        out.append("bytes follow the trailer")
    for kind, start, end, app in blocks:
        if kind in ("comment", "unknown") or (
                kind == "app" and app not in LOOP_APPS + (ICC_APP,)):
            out.append(f"a {kind} block survives ({app.decode('latin-1')})")
        if kind == "app" and app == ICC_APP and \
                _sanitized_icc(data, start, end) != bytes(data[start:end]):
            out.append("the ICC profile still carries provenance")
    return out


def describe(data: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        _, blocks, after = _blocks(data)
    except ParseError:
        return out
    comments = [bytes(data[s + 3:e - 1]).decode("latin-1", "replace")[:80]
                for k, s, e, _ in blocks if k == "comment"]
    if comments:
        out["Comments"] = " | ".join(comments)
    apps = [a.decode("latin-1") for k, _, _, a in blocks if k == "app"]
    if apps:
        out["Application extensions"] = ", ".join(apps)
    out["Frames"] = str(sum(1 for k, *_ in blocks if k == "image"))
    if after < len(data):
        out["After the trailer"] = f"{len(data) - after} bytes"
    return out

