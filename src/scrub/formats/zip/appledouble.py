"""AppleDouble: the `._<name>` sidecar macOS writes when a file leaves a Mac volume.

Finder's *Compress* (`ditto`) puts one in `__MACOSX/` per file and folder, holding
what the file system kept beside the file: its extended attributes and any resource
fork. Measured (`docs/p6_tail_plan.md` §1): the URL a download came from
(`com.apple.metadata:kMDItemWhereFroms`), the browser and quarantine event
(`com.apple.quarantine`) and macOS's provenance record -- compressed inside the
archive, so neither a byte search nor a listing shows them.

The scrubber never keeps a sidecar; this reader exists so the report can say what
one held. Layout (RFC 1740, as Apple's `copyfile` extends it): a header, a table of
entries, and in entry 9 (Finder info) 32 bytes of Finder flags followed by an
`ATTR` block listing the extended attributes by name. Big-endian throughout.
"""
from __future__ import annotations

import plistlib
import struct

MAGIC = b"\x00\x05\x16\x07"
FINDER_INFO = 9
RESOURCE_FORK = 2


def is_appledouble(data: bytes) -> bool:
    return data[:4] == MAGIC and len(data) >= 26


def _value(name: str, raw: bytes) -> str:
    """An attribute's value as text: a binary property list's strings, printable
    text as it is, anything else by its length."""
    if raw.startswith(b"bplist00"):
        try:
            v = plistlib.loads(raw)
        except Exception:                                 # noqa: BLE001
            return f"({len(raw)} bytes)"
        items = v if isinstance(v, list) else [v]
        return ", ".join(str(i) for i in items)
    text = raw.rstrip(b"\x00")
    if text and all(32 <= c < 127 for c in text):
        return text.decode("ascii")
    return f"({len(raw)} bytes)"


def attributes(data: bytes) -> dict[str, str]:
    """{attribute name: value as text} for every extended attribute in a sidecar,
    plus `resource fork` when one is present. {} for anything unreadable."""
    if not is_appledouble(data):
        return {}
    out: dict[str, str] = {}
    try:
        n = struct.unpack_from(">H", data, 24)[0]
        for i in range(n):
            eid, off, length = struct.unpack_from(">III", data, 26 + 12 * i)
            if eid == RESOURCE_FORK and length:
                out["resource fork"] = f"({length} bytes)"
            if eid != FINDER_INFO or length <= 32:
                continue
            attr = off + 34                              # 32 Finder bytes + 2 pad
            if data[attr:attr + 4] != b"ATTR":
                continue
            count = struct.unpack_from(">H", data, attr + 34)[0]
            p = attr + 36
            for _ in range(count):
                a_off, a_len, _flags, nlen = struct.unpack_from(">IIHB", data, p)
                name = data[p + 11:p + 11 + nlen].rstrip(b"\x00").decode(
                    "utf-8", "replace")
                out[name] = _value(name, data[a_off:a_off + a_len])
                p = (p + 11 + nlen + 3) & ~3
    except (struct.error, IndexError):
        return out
    return out
