"""XlsxPlugin — harness-side format knowledge for Excel workbooks (.xlsx).

The DOCX design again (`plugins/docx.py`): a workbook has **two producers in one
file**, so the A2 cell names which leaked:

- the **packager** -- the ZIP layer, the same keys as DOCX's, because it is the same
  layer written by the same `zipwrite` after F1;
- the **workbook model** -- how the SpreadsheetML was written: which parts exist,
  which elements the workbook and each sheet hold and in what order, how strings are
  stored (shared, inline), what a row records, how many styles the producer defines,
  and the `fileVersion` stamp F1 keeps and reports (limit #77).

`struct:size` is its own channel, as for DOCX.

Content identity is the cells as Excel shows them: every sheet in workbook order,
every cell's reference and its value with shared strings resolved -- so two
producers that store "Widgets" differently (a shared string, an inline string) hold
the same content, which is the premise of the A2 peer set.
"""
from __future__ import annotations

import hashlib
import posixpath
import re
import xml.etree.ElementTree as ET

from src.scrub.formats.ooxml import opc, zipread
from tests.harness.plugins.docx import (
    PACKAGER_KEYS,
    DocxPlugin,
    _selfclose,
    _xml_decl,
    packager_features,
    zip_header_constants,
)

X = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_R = "http://schemas.openxmlformats.org/package/2006/relationships"

MODEL_KEYS = ("struct:entry_set", "struct:xml_decl", "struct:selfclose_style",
              "struct:namespaces", "struct:part_types", "struct:workbook_children",
              "struct:file_version", "struct:sheet_children", "struct:cell_types",
              "struct:row_attrs", "struct:styles_shape", "struct:has_theme")
SIZE_KEYS = ("struct:size",)
CHANNELS = {"packager": PACKAGER_KEYS, "model": MODEL_KEYS, "size": SIZE_KEYS}


def header_pieces() -> list[bytes]:
    """Our writer's header fields declared in their pieces as well as whole, the
    ZIP plugin's device (`plugins/zip.py`): signature and version, then flags,
    method, time and date, and a zero byte for the zero-valued fields between a
    name and the next difference (extra and comment lengths, attributes, disk).
    A run that crosses a header must still be cut on a field boundary."""
    import struct

    from src.scrub.formats.ooxml import zipwrite as zwr
    local = b"PK\x03\x04" + struct.pack("<H", zwr.VERSION)
    central = b"PK\x01\x02" + struct.pack("<HH", (zwr.CREATE_SYSTEM << 8) | zwr.VERSION,
                                          zwr.VERSION)
    tail = struct.pack("<HHHH", zwr.FLAGS, zwr.METHOD_DEFLATE, zwr.DOS_TIME, zwr.DOS_DATE)
    return zip_header_constants() + [local, central, tail, b"\x00"]


_FIELD_ID = re.compile(rb'(<[A-Za-z_][\w.-]*:fld\s[^>]*?(?<![\w:.-])id\s*=\s*")\{[^"]*\}')


def guard_view(data: bytes) -> bytes:
    """The package as F1 leaves it, in the form a signature could hide in.

    Every local header and central-directory record, with the fields a writer does
    not CHOOSE blanked -- CRC, sizes and offsets are computed from the content, so no
    tool constant can live there, and with many members (eleven slide layouts) some
    member always lands at a similar offset in every output, which reads as a mark.
    Every field a writer does choose stays: version, flags, method, time, attributes,
    names, extra fields. Then each XML part DECOMPRESSED, as the EPUB plugin's view
    has it: F1's edits are in the XML, and identical text compresses to identical
    bytes the guard could not trace back to the input it echoes. Pictures are
    guarded in their own formats' matrices.

    Text-field IDs (`<a:fld id="{...}">`) are blanked for the same reason as CRCs:
    F1 DERIVES them, by hash, from the cleaned part (`package_f1.
    canonical_field_ids`), so they are computed, never chosen, and with twenty-odd
    fields a deck, some four hex digits matched across every output by chance --
    in half the runs on one machine, once on CI on another. What the derivation
    must guarantee (one form, a fixed point, different parts get different IDs)
    is tested on its own in `test_pptx.py`."""
    import struct
    try:
        arc = zipread.read(data)
    except Exception:                                     # noqa: BLE001
        return data
    out = []
    for e in arc.entries:
        head = bytearray(data[e.local_offset:e.data_offset])
        head[14:26] = bytes(12)                           # crc, comp, uncomp
        out.append(bytes(head))
    p = arc.cd_offset
    for _ in arc.entries:
        nlen, elen, clen = struct.unpack_from("<HHH", data, p + 28)
        rec = bytearray(data[p:p + 46 + nlen + elen + clen])
        rec[16:28] = bytes(12)                            # crc, comp, uncomp
        rec[42:46] = bytes(4)                             # local header offset
        out.append(bytes(rec))
        p += len(rec)
    end = bytearray(data[p:])
    if end[:4] == b"PK\x05\x06":
        end[12:20] = bytes(8)                             # directory size, offset
    out.append(bytes(end))
    for e in arc.entries:
        if not e.is_dir and e.name.endswith((".xml", ".rels")):
            out.append(_FIELD_ID.sub(rb"\1{}", e.content()))
    return b"".join(out)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parts(data: bytes) -> tuple[object, dict[str, bytes]]:
    arc = zipread.read(data)
    parts = {}
    for e in arc.entries:
        if not e.is_dir and e.name.endswith((".xml", ".rels")):
            try:
                parts[e.name] = e.content()
            except Exception:                             # noqa: BLE001
                continue
    return arc, parts


def _rels(parts: dict[str, bytes], source: str) -> dict[str, str]:
    """rId -> resolved part name for the relationships of `source`."""
    folder, base = posixpath.split(source)
    body = parts.get(posixpath.join(folder, "_rels", base + ".rels"))
    if not body:
        return {}
    out = {}
    for rel in ET.fromstring(body):
        target = rel.get("Target", "")
        if rel.get("TargetMode") == "External":
            continue
        out[rel.get("Id")] = posixpath.normpath(
            target.lstrip("/") if target.startswith("/") else posixpath.join(folder, target))
    return out


def sheets(parts: dict[str, bytes]) -> list[tuple[str, str]]:
    """(sheet name, part name) in workbook order."""
    wb = parts.get("xl/workbook.xml")
    if not wb:
        return []
    rels = _rels(parts, "xl/workbook.xml")
    out = []
    for el in ET.fromstring(wb).iter(f"{{{X}}}sheet"):
        part = rels.get(el.get(f"{{{R}}}id"))
        if part:
            out.append((el.get("name", ""), part))
    return out


def shared_strings(parts: dict[str, bytes]) -> list[str]:
    body = parts.get("xl/sharedStrings.xml")
    if not body:
        return []
    return ["".join(t.text or "" for t in si.iter(f"{{{X}}}t"))
            for si in ET.fromstring(body).iter(f"{{{X}}}si")]


def cells(data: bytes) -> list[tuple[str, str, str]]:
    """Every cell as Excel shows it: (sheet, reference, value)."""
    _arc, parts = _parts(data)
    sst = shared_strings(parts)
    out = []
    for name, part in sheets(parts):
        body = parts.get(part)
        if not body:
            continue
        for c in ET.fromstring(body).iter(f"{{{X}}}c"):
            kind = c.get("t", "")
            if kind == "inlineStr":
                value = "".join(t.text or "" for t in c.iter(f"{{{X}}}t"))
            else:
                v = c.find(f"{{{X}}}v")
                value = v.text if v is not None and v.text is not None else ""
                if kind == "s" and value:
                    value = sst[int(value)]
            if value != "":
                out.append((name, c.get("r", ""), value))
    return out


class XlsxPlugin:
    format_id = "xlsx"

    def matches(self, header: bytes, path: str = "") -> bool:
        if header[:4] != b"PK\x03\x04" or not path:
            return False
        try:
            return opc.looks_like(open(path, "rb").read(), "xlsx")
        except OSError:
            return False

    def annotate(self, in_path: str, offset: int) -> str | None:
        return DocxPlugin().annotate(in_path, offset)

    def canonical_content(self, path: str) -> bytes:
        try:
            return hashlib.sha256(repr(cells(open(path, "rb").read())).encode()).digest()
        except Exception:                                 # noqa: BLE001
            return b""

    def mandatory_constants(self) -> list[bytes]:
        """The ZIP layer's declared fields (as DOCX) and the names and namespaces a
        workbook must use. Anything from the workbook itself still fails the guard."""
        return header_pieces() + [
            b"[Content_Types].xml", b"_rels/.rels", b"xl/workbook.xml",
            b"xl/_rels/workbook.xml.rels", b"xl/worksheets/sheet1.xml",
            b"http://schemas.openxmlformats.org/package/2006/content-types",
            b"http://schemas.openxmlformats.org/package/2006/relationships",
            X.encode(), R.encode(),
        ]

    def guard_view(self, data: bytes) -> bytes:
        return guard_view(data)

    def structural_features(self, path: str) -> dict:
        try:
            data = open(path, "rb").read()
            arc, parts = _parts(data)
            pkg = opc.parse(data)
        except Exception:                                 # noqa: BLE001
            return {}
        wb = parts.get("xl/workbook.xml", b"")
        try:
            wb_root = ET.fromstring(wb)
        except ET.ParseError:
            return {}
        sheet_parts = [p for _n, p in sheets(parts)]
        first = parts.get(sheet_parts[0], b"") if sheet_parts else b""
        cell_types, row_attrs = set(), set()
        for p in sheet_parts:
            root = ET.fromstring(parts.get(p, b"<x/>"))
            for c in root.iter(f"{{{X}}}c"):
                cell_types.add(c.get("t", ""))
            for row in root.iter(f"{{{X}}}row"):
                row_attrs.update(_local(a) for a in row.attrib)
        fv = wb_root.find(f"{{{X}}}fileVersion")
        styles_shape = ()
        if parts.get("xl/styles.xml"):
            st = ET.fromstring(parts["xl/styles.xml"])
            styles_shape = tuple(
                (_local(child.tag), len(list(child))) for child in st)
        return {
            **packager_features(arc),
            # --- workbook model ---
            "entry_set": tuple(sorted(e.name for e in arc.entries)),
            "xml_decl": _xml_decl(wb),
            "selfclose_style": _selfclose(first),
            "namespaces": tuple(sorted({m.decode() for m in
                                        re.findall(rb"xmlns:([\w.-]+)=", wb)})),
            "part_types": tuple(sorted(pkg.overrides.values())),
            "workbook_children": tuple(_local(c.tag) for c in wb_root),
            "file_version": tuple(sorted(fv.attrib.items())) if fv is not None else (),
            "sheet_children": tuple(_local(c.tag) for c in ET.fromstring(first))
                              if first else (),
            "cell_types": tuple(sorted(cell_types)),
            "row_attrs": tuple(sorted(row_attrs)),
            "styles_shape": styles_shape,
            "has_theme": any(n.startswith("xl/theme/") for n in arc.names),
        }
