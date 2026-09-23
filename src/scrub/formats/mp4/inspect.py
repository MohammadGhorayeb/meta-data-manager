"""What an MP4's metadata says — for the scrub report, never for a tier.

Coverage is exactly this handler's coverage, which is the point: a locus we cannot
model is absent from the report *and* from the scrub, so the report must never be
read as "nothing else was in the file".
"""
from __future__ import annotations

import datetime as dt
import struct

from ...standards import isobmff
from . import walker as w

# 1904-01-01 to 1970-01-01: the offset between the ISOBMFF epoch and Unix time.
_EPOCH_DELTA = 2082844800

# iTunes-style atoms, as they appear in a `mdir`-namespace `ilst`.
_ILST = {
    b"\xa9nam": "Title", b"\xa9ART": "Artist", b"\xa9alb": "Album",
    b"\xa9day": "Year", b"\xa9cmt": "Comment", b"\xa9gen": "Genre",
    b"\xa9too": "Encoder", b"\xa9cpy": "Copyright", b"\xa9swr": "Software",
    b"\xa9mak": "Camera make", b"\xa9mod": "Camera model",
    b"\xa9xyz": "GPS position", b"desc": "Description", b"covr": "Cover art",
}


def _stamp(value: int) -> str:
    return dt.datetime.fromtimestamp(value - _EPOCH_DELTA, dt.UTC).strftime(
        "%Y-%m-%d %H:%M:%S UTC")


def _times(box: isobmff.Box) -> dict[str, str]:
    payload = box.payload
    if len(payload) < 4:
        return {}
    try:
        if payload[0] == 1:
            created, modified = struct.unpack_from(">QQ", payload, 4)
        else:
            created, modified = struct.unpack_from(">II", payload, 4)
    except struct.error:
        return {}
    name = box.type.decode("ascii", "replace")
    return {f"{name}:{label}": _stamp(v)
            for label, v in (("created", created), ("modified", modified)) if v}


def _loci(payload: bytes) -> dict[str, str]:
    """The QuickTime location box: language(2), name, role(1), lon/lat/alt.

    Longitude comes FIRST, which is the reverse of how a coordinate is spoken and
    the reason a hand-built fixture read back as having no GPS at all (§9).
    """
    at = 4 + 2                                        # version/flags, language
    end = payload.find(b"\x00", at)
    if end == -1 or len(payload) < end + 1 + 12:
        return {}
    at = end + 1 + 1                                  # name terminator, role
    try:
        lon, lat, _alt = struct.unpack_from(">iii", payload, at)
    except struct.error:
        return {}
    return {"udta:loci": f"{lat / 65536:.5f}, {lon / 65536:.5f}"}


def _keys_and_ilst(meta: isobmff.Box) -> dict[str, str]:
    """Tags from both `ilst` flavours.

    In the `mdir` namespace an entry's type is a fourcc. In the `mdta` (Keys)
    namespace it is a **1-based index into the `keys` box**, so the names live in
    a separate parallel list — which is why a reader matching only fourcc names
    finds nothing here, GPS included.
    """
    out: dict[str, str] = {}
    keys: list[str] = []
    keys_box = next((c for c in meta.children if c.type == b"keys"), None)
    if keys_box is not None and len(keys_box.payload) >= 8:
        count = int.from_bytes(keys_box.payload[4:8], "big")
        at = 8
        for _ in range(count):
            if at + 8 > len(keys_box.payload):
                break
            size = int.from_bytes(keys_box.payload[at:at + 4], "big")
            if size < 8 or at + size > len(keys_box.payload):
                break
            keys.append(keys_box.payload[at + 8:at + size]
                        .decode("utf-8", "replace"))
            at += size

    ilst = next((c for c in meta.children if c.type == b"ilst"), None)
    if ilst is None:
        return out
    for entry in ilst.children:
        data_box = next((d for d in entry.children if d.type == b"data"), None)
        raw = data_box.payload[8:] if data_box else entry.payload[16:]
        value = raw.decode("utf-8", "replace").strip("\x00").strip()
        if not value:
            continue
        index = int.from_bytes(entry.type, "big")
        if keys and 1 <= index <= len(keys):
            out[f"keys:{keys[index - 1]}"] = value
        else:
            out[_ILST.get(entry.type,
                          entry.type.decode("latin-1", "replace"))] = value
    return out


def describe(data: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        boxes = isobmff.parse(data)
    except Exception:
        return out

    for root in boxes:
        for box in root.walk():
            if box.type in isobmff.TIMESTAMP_BOXES:
                out.update(_times(box))
            elif box.type == b"loci":
                out.update(_loci(box.payload))
            elif box.type == b"meta":
                out.update(_keys_and_ilst(box))
            elif box.type == b"hdlr":
                name = box.payload[isobmff.HDLR_NAME_AT:].rstrip(b"\x00")
                kind = box.payload[8:12].decode("latin-1", "replace")
                if name and kind in ("vide", "soun"):
                    out[f"hdlr:{kind}"] = name.decode("utf-8", "replace")

    try:
        layout = w.walk(data)
        out["brand"] = layout.brand.decode("latin-1", "replace")
    except Exception:
        pass
    return out
