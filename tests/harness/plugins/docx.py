"""DocxPlugin — harness-side format knowledge for DOCX (FormatPlugin).

Like M4A and PDF, a DOCX has **two producers in one file**, so its A2 cells name
which one leaked instead of averaging them:

- the **packager**, which decided how the ZIP was laid out — entry order, compression
  settings, the timestamp scheme, the version and attribute bytes nobody reads;
- the **document model**, which decided how the WordprocessingML was written — the
  XML idiom, which namespaces are declared, which styles exist, what
  `settings.xml` contains.

The split is not cosmetic. F1 rewrites the container through our own writer, so the
packager channel should close outright while the model channel is untouched — exactly
the shape PDF F1 had (serializer closed, layout open), and knowing *which* is what
scopes F2. A single averaged "A2 fails" would say nothing about what to build next.

`struct:size` is a third channel, named rather than folded into either: it is a side
effect of both, and the M4A lesson was that a size difference deserves its own row
rather than being quietly counted as evidence about content.
"""
from __future__ import annotations

import hashlib
import re

from src.scrub.formats.ooxml import opc, zipread

PACKAGER_KEYS = ("struct:order_policy", "struct:methods", "struct:level_pinned",
                 "struct:timestamp_scheme", "struct:create_system",
                 "struct:version_made_by", "struct:external_attrs",
                 "struct:extra_field_ids", "struct:dir_entries", "struct:flags")

# `entry_set` is here rather than in PACKAGER_KEYS, and the placement was corrected
# after the first E-DOCX run rather than assumed. **Which parts a package contains is
# a decision of the program that wrote the document, not of the thing that zipped
# it**: LibreOffice emits `styles.xml`, `settings.xml` and `fontTable.xml`, macOS
# `textutil` emits a `theme` and its own `meta.xml`, Word adds `webSettings.xml`.
# Filing it under the packager would have credited F1 with a leak it cannot close --
# preserving the part set is what content preservation *means* here.
MODEL_KEYS = ("struct:entry_set", "struct:xml_decl", "struct:selfclose_style",
              "struct:namespaces", "struct:part_types", "struct:style_ids",
              "struct:settings_keys", "struct:sectpr", "struct:has_theme",
              "struct:paragraph_idiom")


def _dos_scheme(entries) -> str:
    stamps = {(e.dos_date, e.dos_time) for e in entries}
    if stamps == {(0x21, 0)}:
        return "epoch-1980"
    if stamps == {(0, 0)}:
        return "unset"
    return f"wall-clock({len(stamps)} distinct)"


# The level this project compresses at, measured as the crowd's in §2.7.
OUR_LEVEL = 6


def _level_pinned(entries) -> str:
    """Is every deflated entry consistent with OUR level, or with zlib at all?

    The obvious feature — the recovered level *class* — was tried first and had to be
    replaced, because it is **size-dependent**: a ZIP stores raw deflate with no
    header byte, so the level is recovered by recompression, and small parts compress
    identically at several levels while large ones pin to one. The synthetic
    producer's three tiny parts therefore reported `5,6,7,8,9` where LibreOffice's
    eight larger parts reported `6` — a difference in part size wearing the name of a
    compression setting, which the variance engine correctly called a leak and which
    said nothing about any producer.

    So the feature now answers the question we actually claim: is this consistent
    with level 6? Membership in the class is size-independent; the class itself is
    not.
    """
    import zlib
    any_zlib = False
    for e in entries:
        if e.method != 8 or e.is_dir or not e.raw:
            continue
        try:
            plain = zlib.decompress(e.raw, -15)
        except zlib.error:
            continue
        matched = False
        for mem in (8, 9):
            co = zlib.compressobj(OUR_LEVEL, zlib.DEFLATED, -15, mem)
            if co.compress(plain) + co.flush() == e.raw:
                matched = True
                break
        if not matched:
            return "not-level-6"
        any_zlib = True
    return f"level-{OUR_LEVEL}" if any_zlib else "-"


def _order_policy(entries) -> str:
    """The ordering *scheme*, independent of which parts exist.

    The raw entry order was the first feature here and stopped measuring anything
    after F1: our writer sorts, so the sequence becomes a pure function of the part
    set, and the key then reported the document model through a packager-shaped hole.
    A scheme is what a packager actually chooses -- the same abstraction
    `timestamp_scheme` uses, and for the same reason.
    """
    names = [e.name for e in entries]
    if not names:
        return "empty"
    rest = names[1:] if names[0] == opc.CONTENT_TYPES else names
    if names[0] == opc.CONTENT_TYPES and rest == sorted(rest):
        return "content-types-first-then-sorted"
    if names == sorted(names):
        return "sorted"
    if names[-1] == opc.CONTENT_TYPES:
        return "content-types-last"
    if names[0] == opc.CONTENT_TYPES:
        return "content-types-first-unsorted"
    return "producer-specific"


def empty_package_skeleton(fidelity: str = "F2") -> bytes:
    """**Our own output for a document with no content**, generated not transcribed.

    The `plugins/pdf.py` device, and it is here for a measured reason rather than a
    pre-emptive one: the guard passed at F1 on ten short constants and **failed the
    moment F2 landed**, reporting the complete entry for `_rels/.rels` — local
    header, name, compressed bytes and CRC — as our signature.

    That one is real and is not a corpus artefact. Every DOCX's root relationships
    part ends up declaring exactly one relationship once `docProps/*` is dropped, and
    canonicalisation then gives every package the same bytes for it. That is
    *convergence*, which is the entire purpose of the tier: it says a file was
    canonically rewritten, never what it was rewritten from (limit #9). Declaring it
    by hand would have meant transcribing a compressed byte run; declaring the empty
    package instead means any run common to every output that is a substring of a
    package **with no content** is structure by construction.

    The declaration is broad, so it is checked rather than trusted:
    `test_matrix_docx.py` asserts the skeleton contains no producer string and no
    timestamp, so introducing one tomorrow fails a test even though the guard itself
    would still pass.
    """
    from src.scrub.formats.docx import f1 as docx_f1
    from src.scrub.formats.docx import f2 as docx_f2
    from src.scrub.formats.ooxml import zipwrite as zwr
    ns = "http://schemas.openxmlformats.org"
    minimal = zwr.write({
        "[Content_Types].xml":
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<Types xmlns="{ns}/package/2006/content-types">'
            f'<Default Extension="rels" ContentType="application/vnd.openxmlformats-'
            f'package.relationships+xml"/>'
            f'<Default Extension="xml" ContentType="application/xml"/>'
            f'<Override PartName="/word/document.xml" ContentType="application/vnd'
            f'.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            f'</Types>'.encode(),
        "_rels/.rels":
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<Relationships xmlns="{ns}/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{ns}/officeDocument/2006/relationships/'
            f'officeDocument" Target="word/document.xml"/></Relationships>'.encode(),
        "word/document.xml":
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<w:document xmlns:w="{ns}/wordprocessingml/2006/main"><w:body>'
            f'</w:body></w:document>'.encode(),
    })
    return (docx_f2 if fidelity == "F2" else docx_f1).scrub(minimal)


class DocxPlugin:
    format_id = "docx"

    def matches(self, header: bytes, path: str = "") -> bool:
        if header[:4] != b"PK\x03\x04":
            return False
        if not path:
            # A prefix cannot decide this -- every ZIP shares it -- so with no path
            # to open, decline rather than claim.
            return False
        try:
            return opc.looks_like(open(path, "rb").read(), "docx")
        except OSError:
            return False

    def annotate(self, in_path: str, offset: int) -> str | None:
        """Map a byte offset to the part it falls inside."""
        try:
            arc = zipread.read(open(in_path, "rb").read())
        except Exception:
            return None
        for e in arc.entries:
            if e.local_offset <= offset < e.data_offset + e.comp_size:
                where = ("header" if offset < e.data_offset
                         else f"+{offset - e.data_offset}")
                return f"{e.name}@{where}"
        if offset >= arc.cd_offset:
            return f"central_directory@+{offset - arc.cd_offset}"
        return None

    def canonical_content(self, path: str) -> bytes:
        """The document's text is the content identity.

        The same judgement `plugins/pdf.py` records for `pdftotext` over pixels: text
        is what a reader loses if a scrub goes wrong, and it costs milliseconds. A
        pixel-space identity (render through LibreOffice, compare) is what an F2 that
        rewrites the XML will need, and belongs to that tier rather than being paid
        for by every tier here.
        """
        from src.scrub.formats.docx import textextract
        try:
            text = textextract.document_text(open(path, "rb").read())
        except Exception:
            return b""
        return hashlib.sha1(text.encode("utf-8")).digest()

    def mandatory_constants(self) -> list[bytes]:
        """Format-required invariants the fingerprint guard must not read as a tool
        signature — recorded in the matrix's `excluded` block, never hidden.

        A ZIP's per-entry headers are mostly fixed fields, so at any useful
        `min_len` the guard surfaces the local-header prefix on every conformant
        archive ever written. Our pinned constants make that prefix *more* constant,
        not less — which is the point of pinning them, and is limit #9 again: it
        marks a file as canonically repackaged, never as repackaged from what.

        Every one of these values was measured as the crowd's in §2.7, so declaring
        them is declaring conformity rather than excusing a signature. What is NOT
        declared, and so still fails the guard, is anything from the document itself.
        """
        import struct as _s

        from src.scrub.formats.ooxml import zipwrite as zwr
        # The invariant head of one of our local file headers: signature, version,
        # flags, method, and the 1980 timestamp.
        head = (b"PK\x03\x04" + _s.pack("<H", zwr.VERSION)
                + _s.pack("<HHHH", zwr.FLAGS, zwr.METHOD_DEFLATE,
                          zwr.DOS_TIME, zwr.DOS_DATE))
        cen = (b"PK\x01\x02"
               + _s.pack("<HH", (zwr.CREATE_SYSTEM << 8) | zwr.VERSION, zwr.VERSION)
               + _s.pack("<HHHH", zwr.FLAGS, zwr.METHOD_DEFLATE,
                         zwr.DOS_TIME, zwr.DOS_DATE))
        return [
            head, cen, b"PK\x05\x06",
            b"[Content_Types].xml", b"_rels/.rels", b"word/document.xml",
            b"http://schemas.openxmlformats.org/package/2006/content-types",
            b"http://schemas.openxmlformats.org/package/2006/relationships",
            b"http://schemas.openxmlformats.org/wordprocessingml/2006/main",
            b"http://schemas.openxmlformats.org/officeDocument/2006/relationships",
            empty_package_skeleton("F1"),
            empty_package_skeleton("F2"),
        ]

    def structural_features(self, path: str) -> dict:
        try:
            data = open(path, "rb").read()
            arc = zipread.read(data)
            pkg = opc.parse(data)
        except Exception:
            return {}

        parts = {}
        for e in arc.entries:
            if e.is_dir or not e.name.endswith(".xml"):
                continue
            try:
                parts[e.name] = e.content()
            except Exception:
                continue

        doc = parts.get("word/document.xml", b"")
        settings = parts.get("word/settings.xml", b"")
        styles = parts.get("word/styles.xml", b"")

        return {
            # --- packager ---
            "order_policy": _order_policy(arc.entries),
            "methods": tuple(sorted({e.method for e in arc.entries})),
            "level_pinned": _level_pinned(arc.entries),
            "timestamp_scheme": _dos_scheme(arc.entries),
            "create_system": tuple(sorted({e.create_system for e in arc.entries})),
            "version_made_by": tuple(sorted({e.version_made_by
                                             for e in arc.entries})),
            "external_attrs": tuple(sorted({e.external_attr for e in arc.entries})),
            "extra_field_ids": tuple(sorted({
                e.extra_cen[:2].hex() for e in arc.entries if e.extra_cen})),
            "dir_entries": sum(1 for e in arc.entries if e.is_dir),
            "flags": tuple(sorted({e.flags for e in arc.entries})),
            # --- document model ---
            "entry_set": tuple(sorted(e.name for e in arc.entries)),
            "xml_decl": _xml_decl(doc),
            "selfclose_style": _selfclose(doc),
            "namespaces": tuple(sorted({m.decode() for m in
                                        re.findall(rb"xmlns:([\w.-]+)=", doc)})),
            "part_types": tuple(sorted(pkg.overrides.values())),
            "style_ids": tuple(sorted({m.decode() for m in re.findall(
                rb'w:styleId="([^"]*)"', styles)})),
            "settings_keys": tuple(sorted({m.decode() for m in re.findall(
                rb"<(w[0-9]*:[\w.-]+)", settings)})),
            "sectpr": _sectpr(doc),
            "has_theme": any(n.startswith("word/theme/") for n in arc.names),
            "paragraph_idiom": _paragraph_idiom(doc),
        }


def _xml_decl(body: bytes) -> str:
    """How the producer spells the XML declaration. MAT2 writes single quotes and no
    trailing newline; Word writes double quotes and CRLF. Pure spelling — which is
    exactly why it belongs to the model channel and should close at F2."""
    m = re.match(rb"<\?xml[^>]*\?>(\r\n|\r|\n)?", body)
    return m.group(0).decode("ascii", "replace") if m else "<none>"


def _selfclose(body: bytes) -> str:
    spaced = len(re.findall(rb"\s/>", body))
    tight = len(re.findall(rb"[^\s]/>", body))
    if spaced and tight:
        return "mixed"
    return "spaced" if spaced else "tight" if tight else "none"


def _sectpr(body: bytes) -> tuple[str, ...]:
    m = re.search(rb"<w:sectPr[^>]*>(.*?)</w:sectPr>", body, re.S)
    if not m:
        return ()
    return tuple(sorted({t.decode() for t in
                         re.findall(rb"<(w:[\w.-]+)", m.group(1))}))


def _paragraph_idiom(body: bytes) -> tuple[str, ...]:
    """Which child elements a producer puts inside a paragraph. Substance rather than
    spelling — the DOCX candidate for the residual F2 cannot close, by the same
    argument that makes glyph geometry PDF's floor."""
    return tuple(sorted({t.decode() for t in
                         re.findall(rb"<(w:(?:pPr|rPr|proofErr|bookmarkStart|"
                                    rb"lastRenderedPageBreak|r|t|tab|br))[\s/>]",
                                    body)}))
