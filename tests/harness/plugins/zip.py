"""ZipPlugin — harness-side format knowledge for plain ZIP archives.

Content identity is what extracting the archive puts on a disk: each member's name,
kind (file, executable, folder, link) and bytes, sidecars excluded.
`structural_features` is the A2 channel, the container only -- the producer's
version fields, flags, extra fields, attribute bits, compression choices, entry
order, folder entries and sidecars, and the size. The members are files of their
own formats, judged in those formats' matrices, so the guard's view blanks their
data (`guard_view`) and A2 keys never look inside them.
"""
from __future__ import annotations

import hashlib
import struct

from src.scrub.formats.ooxml import zipread, zipwrite
from src.scrub.formats.zip import f1


def _read(path: str):
    try:
        return zipread.read(open(path, "rb").read())
    except Exception:                                     # noqa: BLE001
        return None


class ZipPlugin:
    format_id = "zip"

    def matches(self, header: bytes, path: str = "") -> bool:
        return header[:4] in (b"PK\x03\x04", b"PK\x05\x06")

    def annotate(self, in_path: str, offset: int) -> str | None:
        a = _read(in_path)
        if a is None:
            return None
        if offset >= a.cd_offset:
            return "central directory" if offset < a.cd_offset + a.cd_size else "eocd"
        for e in a.entries:
            if e.local_offset <= offset < e.data_offset:
                return f"local header:{e.name}"
            if e.data_offset <= offset < e.data_offset + e.comp_size:
                return f"data:{e.name}"
        return None

    def canonical_content(self, path: str) -> bytes:
        a = _read(path)
        if a is None:
            return b""
        h = hashlib.sha256()
        # A folder entry for a folder its files imply adds nothing to a disk.
        implied = {e.name[:i + 1] for e in a.entries
                   for i in range(len(e.name) - 1) if e.name[i] == "/"}
        for e in sorted(a.entries, key=lambda e: e.name):
            body = b"" if e.is_dir else e.content()
            if f1.sidecar(e.name, body) or (e.is_dir and e.name in implied):
                continue
            mode = e.external_attr >> 16 if e.create_system == 3 else 0
            kind = ("dir" if e.is_dir else "link" if mode & 0o170000 == 0o120000
                    else "exec" if mode & 0o111 else "file")
            h.update(e.name.encode() + b"\0" + kind.encode() + b"\0"
                     + hashlib.sha256(body).digest())
        return h.digest()

    def mandatory_constants(self) -> list[bytes]:
        """The fixed fields of our headers: signature, versions, flags (0, or bit
        11 for a UTF-8 name), method (stored or deflated), the 1980 time; the four
        canonical attribute words; the end record's signature. Every one is the
        crowd's value (`zipwrite`), so declaring them declares conformity: they mark
        a file as canonically rebuilt, never as rebuilt from what (limit #9)."""
        out = [b"PK\x05\x06"]
        for flags in (0, zipwrite.FLAG_UTF8):
            for method in (zipwrite.METHOD_STORED, zipwrite.METHOD_DEFLATE):
                tail = struct.pack("<HHHH", flags, method, zipwrite.DOS_TIME,
                                   zipwrite.DOS_DATE)
                out.append(b"PK\x03\x04" + struct.pack("<H", zipwrite.VERSION) + tail)
                out.append(b"PK\x01\x02" + struct.pack(
                    "<HH", (zipwrite.UNIX << 8) | zipwrite.VERSION, zipwrite.VERSION)
                    + tail)
        out += [struct.pack("<I", a) for a in sorted(f1.CANONICAL_ATTRS)]
        return out + [b"\x00"]

    def guard_view(self, data: bytes) -> bytes:
        """The container without its members' data: headers, names, the central
        directory. A member is a file of its own format, guarded in its own
        matrix; here its bytes would only be compared against themselves."""
        try:
            a = zipread.read(data)
        except Exception:                                 # noqa: BLE001
            return data
        parts = [data[e.local_offset:e.data_offset] for e in a.entries]
        return b"".join(parts) + data[a.cd_offset:]

    def structural_features(self, path: str) -> dict:
        a = _read(path)
        if a is None:
            return {}
        es = a.entries

        def ids(blob):
            return tuple(hid for hid, _ in f1._extras(blob))
        return {
            "versions": tuple(sorted({(e.version_made_by, e.create_system,
                                       e.version_needed) for e in es})),
            "flags": tuple(sorted({e.flags for e in es})),
            "methods": tuple(e.method for e in sorted(es, key=lambda e: e.name)),
            "extra_fields": tuple(sorted({ids(e.extra_cen) + ids(e.extra_loc)
                                          for e in es})),
            "external_attrs": tuple(sorted({e.external_attr for e in es})),
            "internal_attrs": tuple(sorted({e.internal_attr for e in es})),
            "entry_order": tuple(e.name for e in es),
            "folder_entries": tuple(sorted(e.name for e in es if e.is_dir)),
            "sidecars": sum(1 for e in es if e.name.startswith("__MACOSX")
                            or "/._" in e.name or e.name.startswith("._")),
            "comment": bool(a.comment),
            "size": a.size,
        }
