"""What an MP4 / MOV's metadata says — for the scrub report, never for a tier.

Covers what M7 measured, including the two loci no box-tree reader shows: the stale
copies in unreferenced `mdat` bytes and in a trailing top-level `free` box. They are
reported by size and by what they contain, because "the file also holds a second copy
of this" is the part of the report a reader cannot get from ExifTool.
"""
from __future__ import annotations

import re

from ...standards import isobmff as iso
from ..m4a.inspect import _mvhd_times
from . import f1

_PREFIX = "com.apple.quicktime."
_ENCODERS = (b"x264 - core", b"x265 (build", b"Lavc")


def _keys(meta: iso.Box) -> list[str]:
    keys = next((c for c in meta.children if c.type == b"keys"), None)
    if keys is None:
        return []
    p, out, at = keys.payload, [], 8
    for _ in range(int.from_bytes(p[4:8], "big")):
        size = int.from_bytes(p[at:at + 4], "big")
        if size < 8:
            break
        out.append(p[at + 8:at + size].decode("utf-8", "replace"))
        at += size
    return out


def _mdta(meta: iso.Box, where: str) -> dict[str, str]:
    """QuickTime metadata: a `keys` table, and an `ilst` whose entries are typed by
    1-based key INDEX rather than by a four-character code."""
    names = _keys(meta)
    ilst = next((c for c in meta.children if c.type == b"ilst"), None)
    out: dict[str, str] = {}
    if ilst is None:
        return out
    for entry in ilst.children:
        index = int.from_bytes(entry.type, "big")
        if not 1 <= index <= len(names):
            continue
        try:
            data = next((b for b in iso.parse(entry.payload) if b.type == b"data"),
                        None)
        except Exception:                                 # noqa: BLE001
            data = None
        if data is None:
            continue
        kind, value = int.from_bytes(data.payload[:4], "big"), data.payload[8:]
        name = names[index - 1].removeprefix(_PREFIX)
        text = (value.decode("utf-8", "replace").strip()
                if kind == 1 and value else f"({len(value)} bytes)")
        out[f"{where}:{name}"] = text
    return out


def _handler_name(payload: bytes) -> bytes:
    """QuickTime writes the name as a Pascal string (a count byte first), ISO as a
    C string. Tell them apart by whether the first byte counts the rest."""
    raw = payload[24:].rstrip(b"\x00")
    if raw and raw[0] == len(raw) - 1:
        raw = raw[1:]
    return raw


def _timed_metadata_keys(trak: iso.Box) -> list[str]:
    stsd = trak.find(b"mdia/minf/stbl/stsd")
    if stsd is None:
        return []
    return [m.decode("utf-8", "replace").removeprefix(_PREFIX)
            for m in re.findall(rb"keyd(?:mdta|fiel)?([\x21-\x7e]+)", stsd.payload)]


def describe(data: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    tops, moov_hdr, mdat_hdr = f1._layout(data)
    moov = iso.parse(data[moov_hdr.offset:moov_hdr.end])[0]

    for node in moov.walk():
        if node.type in f1.TIMESTAMP_BOXES:
            out.update(_mvhd_times(node))
    meta = moov.find(b"meta")
    if meta is not None:
        out.update(_mdta(meta, "QuickTime"))
    udta = moov.find(b"udta")
    for atom in udta.children if udta is not None else ():
        if atom.type[:1] == b"\xa9":
            out[f"udta:{atom.type[1:].decode('latin-1')}"] = \
                atom.payload[4:].decode("utf-8", "replace").strip() \
                or f"({len(atom.payload)} bytes)"

    names: set[str] = set()
    for i, t in enumerate(f1._tracks(moov), start=1):
        tmeta = t.box.find(b"meta")
        if tmeta is not None:
            out.update(_mdta(tmeta, f"Track{i}"))
        for h in (b for b in t.box.walk() if b.type == b"hdlr"):
            name = _handler_name(h.payload)
            if name:
                names.add(name.decode("utf-8", "replace"))
        if t.handler in f1.DROP_HANDLERS:
            stbl = t.box.find(b"mdia/minf/stbl")
            n = (sum(len(c.sample_sizes) for c in iso.chunks(stbl, limit=len(data)))
                 if stbl else 0)
            keys = ", ".join(_timed_metadata_keys(t.box)) or "unnamed"
            out[f"Timed metadata track {t.track_id}"] = f"{keys} ({n} samples)"
    if names:
        out["Handler names"] = ", ".join(sorted(names))

    for b in tops:
        if b.type not in f1.KEEP_TOP:
            label = b.type.decode("latin-1")
            out[f"top-level {label} box"] = f"({b.size} bytes)"

    gap = _unreferenced(data, moov, mdat_hdr)
    if gap:
        out["Unreferenced bytes in mdat"] = f"({gap} bytes: a stale copy, not content)"

    head = data[mdat_hdr.offset:mdat_hdr.offset + (4 << 20)]
    for marker in _ENCODERS:
        at = head.find(marker)
        if at >= 0:
            text = re.match(rb"[\x20-\x7e]+", head[at:at + 80]).group()
            out["Encoder (inside the coded video)"] = text.decode()[:60]
            break
    return out


def _unreferenced(data: bytes, moov: iso.Box, mdat_hdr: iso.Box) -> int:
    """Bytes of mdat that no track -- kept OR dropped -- points at."""
    spans = []
    for t in f1._tracks(moov):
        stbl = t.box.find(b"mdia/minf/stbl")
        if stbl is None:
            continue
        try:
            spans += [(c.offset, c.end) for c in iso.chunks(stbl, limit=len(data))
                      if c.sample_sizes]
        except Exception:                                 # noqa: BLE001
            return 0
    cursor, gap = mdat_hdr.offset + mdat_hdr.header_len, 0
    for a, b in sorted(spans):
        gap += max(0, a - cursor)
        cursor = max(cursor, b)
    return gap + max(0, mdat_hdr.end - cursor)
