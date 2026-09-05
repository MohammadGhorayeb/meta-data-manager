"""What an M4A's metadata says — for the scrub report."""
from __future__ import annotations

import struct

from ...standards import isobmff

# iTunes-style tag atoms. The `©` prefix is 0xA9 in the box type.
_ILST = {
    b"\xa9nam": "Title", b"\xa9ART": "Artist", b"\xa9alb": "Album",
    b"\xa9day": "Year", b"\xa9cmt": "Comment", b"\xa9gen": "Genre",
    b"\xa9too": "Encoder", b"\xa9wrt": "Composer", b"aART": "Album artist",
    b"covr": "Cover art", b"desc": "Description", b"purd": "Purchase date",
    b"apID": "Account", b"cprt": "Copyright", b"----": "Custom tag",
}

# 1904-01-01 to 1970-01-01, the offset between the ISOBMFF epoch and Unix time.
_EPOCH_DELTA = 2082844800


def _mvhd_times(box: isobmff.Box) -> dict[str, str]:
    """Creation and modification times, which are structural fields rather than
    tags — which is why a tag-oriented tool leaves them (see the M4A benchmark row).
    """
    import datetime as dt
    payload = box.payload
    if len(payload) < 4:
        return {}
    version = payload[0]
    try:
        if version == 1:
            created, modified = struct.unpack_from(">QQ", payload, 4)
        else:
            created, modified = struct.unpack_from(">II", payload, 4)
    except struct.error:
        return {}
    out = {}
    for label, value in (("created", created), ("modified", modified)):
        if not value:
            continue
        stamp = dt.datetime.fromtimestamp(value - _EPOCH_DELTA, dt.UTC)
        out[f"{box.type.decode('ascii', 'replace')}:{label}"] = \
            stamp.strftime("%Y-%m-%d %H:%M:%S UTC")
    return out


def _ilst_value(box: isobmff.Box) -> str:
    """An ilst entry wraps its value in a `data` box: 4 type bytes, 4 locale, then
    the value.

    The `data` box is reached by parsing the entry's payload rather than by reading
    `children`: `ilst` entries are not in the walker's container set, because
    scrubbing removes them wholesale and never needs to look inside. Reporting does.
    """
    inner = box.children
    if not inner and box.payload:
        try:
            inner = isobmff.parse(box.payload)
        except Exception:                                 # noqa: BLE001
            return f"({len(box.payload)} bytes)"
    data_box = next((c for c in inner if c.type == b"data"), None)
    if data_box is None:
        return f"({box.size} bytes)"
    value = data_box.payload[8:]
    if box.type == b"covr" or len(value) > 256 or not value.isascii():
        return f"({len(value)} bytes)"
    return value.decode("utf-8", "replace").strip() or f"({len(value)} bytes)"


def describe(data: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    boxes = isobmff.parse(data)

    for box in boxes:
        for node in box.walk():
            if node.type in (b"mvhd", b"tkhd", b"mdhd"):
                out.update(_mvhd_times(node))
            elif node.type == b"ilst":
                for entry in node.children:
                    label = _ILST.get(entry.type)
                    name = label or entry.type.decode("latin-1", "replace")
                    out[f"iTunes:{name}"] = _ilst_value(entry)
            elif node.type == b"free" and node.size > 8:
                # Dead space that can hold arbitrary bytes, and whose size is a
                # muxer tell.
                out["free box"] = f"({node.size} bytes of slack)"
            elif node.type == b"\xa9xyz":
                out["iTunes:GPS"] = node.payload.decode("utf-8", "replace").strip()
    return out
