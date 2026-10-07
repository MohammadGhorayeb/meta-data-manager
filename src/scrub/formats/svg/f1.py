"""SVG at F1: XML surgery, and every picture inside cleaned at its own format.

Phase 6 M4 (`docs/p6_tail_plan.md` §1, §3, §4 D3). Measured on 4,255 SVGs shipped
in installed apps and on LibreOffice's export: editors leave their own namespaces
(Inkscape's file name and version, an export path naming a home folder), generator
comments, `<metadata>` blocks -- and **pictures embedded as base64 carry their own
metadata: LibreOffice embeds the original photo byte for byte, GPS included**, which
ExifTool, reading the SVG, does not report.

F1 only deletes (the DOCX surgery, `ooxml/xmlsurgery.py`): comments; processing
instructions except the XML declaration and `xml-stylesheet` (styling: content);
`<metadata>`; every element, attribute and declaration in an editor's namespace,
resolved from the declarations rather than trusted from the prefix (Illustrator
binds its prefixes through DTD entities); a `<title>`/`<desc>` that is only a
generator's signature ("Created with Sketch."). It rewrites exactly one thing:
each `data:` picture is decoded, scrubbed by its own format's handler
(`embedded.py`) and re-encoded. Fonts and CSS embedded as data are content, kept and
reported; any other embedded type is refused.

The safety net, checked on every scrub: the document's elements, attributes and
text, read by a real XML parser, are identical before and after except for what the
rules above remove and the embedded pictures' payloads. LibreOffice's own namespace
(`ooo:`) is never touched: its exported presentations run a script that reads it.
DOCTYPE entities are allowed only as plain strings (Illustrator's); external or
parameter entities are refused. A gzipped `.svgz` is handled inside its gzip, which
is rewritten without the original file name and time it carries.
"""
from __future__ import annotations

import base64
import binascii
import gzip
import re
import urllib.parse
import xml.parsers.expat

from ... import embedded
from ...errors import ParseError
from ..ooxml import xmlsurgery as xs

SVG_NS = "http://www.w3.org/2000/svg"
EDITOR_NS = {
    "http://www.inkscape.org/namespaces/inkscape": "Inkscape",
    "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd": "Sodipodi (Inkscape)",
    "http://www.bohemiancoding.com/sketch/ns": "Sketch",
    "http://ns.adobe.com/AdobeIllustrator/10.0/": "Adobe Illustrator",
    "http://ns.adobe.com/Extensibility/1.0/": "Adobe Illustrator",
    "http://ns.adobe.com/Graphs/1.0/": "Adobe Illustrator",
    "http://ns.adobe.com/AdobeSVGViewerExtensions/3.0/": "Adobe SVG Viewer",
    "http://ns.adobe.com/Variables/1.0/": "Adobe Illustrator",
    "http://ns.adobe.com/SaveForWeb/1.0/": "Adobe Illustrator",
    "http://ns.adobe.com/ImageReplacement/1.0/": "Adobe Illustrator",
    "http://ns.adobe.com/GenericCustomNamespace/1.0/": "Adobe Illustrator",
    "http://ns.adobe.com/XPath/1.0/": "Adobe Illustrator",
    "http://www.serif.com/": "Affinity",
    "https://boxy-svg.com": "Boxy SVG",
}
# Vocabularies used inside <metadata>; their declarations go once nothing uses them.
METADATA_NS = {"http://www.w3.org/1999/02/22-rdf-syntax-ns#",
               "http://purl.org/dc/elements/1.1/", "http://creativecommons.org/ns#",
               "http://web.resource.org/cc/", "http://purl.org/dc/terms/"}
GENERATOR_TEXT = re.compile(r"\s*(created|generated|made) (with|by) [^<]{1,80}",
                            re.IGNORECASE)
_DATA_URI = re.compile(
    rb"data:([a-z0-9.+-]+/[a-z0-9.+-]+)((?:;[a-z0-9-]+=[^;,\"'\s]*)*)(;base64)?,")
_ASCII_COMPATIBLE = {"utf-8", "utf8", "us-ascii", "ascii", "iso-8859-1", "latin-1",
                     "windows-1252"}
# A data URI as a parser hands it over (attribute whitespace normalized): up to the
# quote or the `)` of a CSS `url(...)`. The safety net compares everything else.
_PAYLOAD = re.compile(r"data:[^)\"']*")
_USER_PATH = re.compile(rb"(?:file:/+|/Users/|/home/|[A-Za-z]:[\\/]Users[\\/])"
                        rb"[^\"'<>\s)]{1,200}")


def is_svg(data: bytes) -> bool:
    if data[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(data[:1 << 22])[:4096]
        except Exception:                                 # noqa: BLE001
            return False
    head = data[:4096].lstrip(b"\xef\xbb\xbf \t\r\n")
    if not head.startswith(b"<"):
        return False
    m = re.search(rb"<(?:[A-Za-z_][\w.-]*:)?svg[\s>]", head)
    return m is not None and not re.search(rb"<(?!\?|!)[A-Za-z_]", head[:m.start()])


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #
class _Doc:
    """What a real XML parser says about the document: prefix bindings, entities,
    and the event stream the safety net compares."""

    def __init__(self, data: bytes):
        self.prefixes: dict[str, str] = {}
        self.bindings: dict[str, set[str]] = {}
        self.entities: list[tuple[str, str]] = []
        self.events: list[tuple] = []
        p = xml.parsers.expat.ParserCreate(namespace_separator=" ")
        p.SetParamEntityParsing(xml.parsers.expat.XML_PARAM_ENTITY_PARSING_NEVER)
        p.StartNamespaceDeclHandler = self._ns
        p.EntityDeclHandler = self._entity
        p.StartElementHandler = lambda n, a: self.events.append(("start", n, a))
        p.EndElementHandler = lambda n: self.events.append(("end", n))
        p.CharacterDataHandler = self._text
        p.ExternalEntityRefHandler = self._external
        try:
            p.Parse(data, True)
        except xml.parsers.expat.ExpatError as e:
            raise ParseError(f"SVG: not well-formed XML ({e})") from None

    def _ns(self, prefix, uri):
        """Every binding of every prefix. The default namespace is re-bound as a
        matter of course (XHTML inside `<foreignObject>`); a named prefix bound to
        an editor's namespace in one place and to anything else in another cannot
        be cut by its written name, so that is refused."""
        prefix = prefix or ""
        uri = uri or ""
        seen = self.bindings.setdefault(prefix, set())
        seen.add(uri)
        if prefix and len(seen) > 1 and seen & set(EDITOR_NS):
            raise ParseError(f"SVG: prefix {prefix!r} is bound both to an editor's "
                             "namespace and to another; refused")
        self.prefixes.setdefault(prefix, uri)

    def _entity(self, name, is_param, value, base, system_id, public_id, notation):
        if is_param or system_id or public_id or notation or value is None \
                or "&" in value or "<" in value:
            raise ParseError(f"SVG: entity {name!r} is not a plain string -- external, "
                             "parameter or nested entities are refused")
        self.entities.append((name, value))

    def _external(self, *_):
        raise ParseError("SVG: an external entity reference; refused")

    def _text(self, text):
        if self.events and self.events[-1][0] == "text":
            self.events[-1] = ("text", self.events[-1][1] + text)
        else:
            self.events.append(("text", text))


def _decode(data: bytes) -> tuple[bytes, bool]:
    if data[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(data), True
        except Exception as e:                            # noqa: BLE001
            raise ParseError(f"SVG: a damaged .svgz ({e})") from None
    return data, False


def _check_encoding(xml: bytes) -> None:
    if xml[:2] in (b"\xff\xfe", b"\xfe\xff"):
        raise ParseError("SVG: UTF-16 is not handled -- not scrubbed (limit #57)")
    m = re.match(rb'\s*<\?xml[^>]*encoding\s*=\s*["\']([^"\']+)', xml.lstrip(b"\xef\xbb\xbf"))
    if m and m.group(1).decode("ascii", "replace").lower() not in _ASCII_COMPATIBLE:
        raise ParseError(f"SVG: encoding {m.group(1).decode()!r} is not handled -- not "
                         "scrubbed (limit #57)")


# --------------------------------------------------------------------------- #
# The rules
# --------------------------------------------------------------------------- #
def _editor_prefixes(doc: _Doc) -> list[str]:
    return sorted(p for p, uri in doc.prefixes.items() if p and uri in EDITOR_NS)


def _svg_prefixes(doc: _Doc) -> list[str]:
    return [p for p, uris in doc.bindings.items() if SVG_NS in uris]


def _written_elements(xml: bytes, prefix: str) -> set[str]:
    return {m.group(1).decode() for m in re.finditer(
        rb"<(" + re.escape(prefix.encode()) + rb":[A-Za-z_][\w.-]*)(?=[\s/>])", xml)}


def _qualified(svg_prefix: str, local: str) -> str:
    return f"{svg_prefix}:{local}" if svg_prefix else local


def _is_generator_text(xml: bytes, start: int, end: int) -> bool:
    inner = xml[start:end]
    body = inner[inner.find(b">") + 1:inner.rfind(b"</")]
    return b"<" not in body and bool(GENERATOR_TEXT.fullmatch(
        body.decode("utf-8", "replace")))


def _remove_generator_labels(xml: bytes, tag: str, removed: list[str]) -> bytes:
    pos = 0
    while True:
        span = xs._element_span(xml, tag, pos)
        if span is None:
            return xml
        if _is_generator_text(xml, *span):
            xml = xml[:span[0]] + xml[span[1]:]
            removed.append(f"a <{tag}> that only names the program that made it")
        else:
            pos = span[0] + 1


def _remove_pis(xml: bytes, removed: list[str]) -> bytes:
    """Processing instructions other than the XML declaration and `xml-stylesheet`
    (which styles the drawing in a browser: content)."""
    out, i = bytearray(), 0
    for m in re.finditer(rb"<\?([A-Za-z_][\w.-]*)[^?]*(?:\?(?!>)[^?]*)*\?>", xml):
        if m.group(1) in (b"xml", b"xml-stylesheet"):
            continue
        out += xml[i:m.start()]
        i = m.end()
        removed.append(f"a <?{m.group(1).decode()}?> instruction")
    return bytes(out + xml[i:])


def _data_uris(xml: bytes) -> list[tuple[int, int, str, bool, bytes]]:
    """(start, end, mime, is_base64, payload) for each `data:` URI."""
    out = []
    for m in _DATA_URI.finditer(xml):
        start, body = m.start(), m.end()
        is_b64 = m.group(3) is not None
        stop = re.compile(rb"[^A-Za-z0-9+/=\s]" if is_b64 else rb"[\"')<\s]")
        end_m = stop.search(xml, body)
        end = end_m.start() if end_m else len(xml)
        while is_b64 and end > body and xml[end - 1:end].isspace():
            end -= 1
        out.append((start, end, m.group(1).decode().lower(), is_b64, xml[body:end]))
    return out


def _clean_data_uris(xml: bytes, removed: list[str], depth: int) -> bytes:
    out, i = bytearray(), 0
    for n, (start, end, mime, is_b64, payload) in enumerate(_data_uris(xml)):
        where = f"embedded {mime} #{n + 1}"
        if mime.startswith(("font/", "application/font", "application/x-font",
                            "text/css", "text/plain")):
            continue                                       # content; reported
        if not mime.startswith("image/"):
            raise ParseError(f"SVG: {where} is not a picture this project can clean; "
                             "refused rather than passed through")
        try:
            raw = (base64.b64decode(re.sub(rb"\s", b"", payload), validate=True)
                   if is_b64 else urllib.parse.unquote_to_bytes(payload.decode("latin-1")))
        except (ValueError, binascii.Error) as e:
            # Damaged base64 is a damaged file (the fuzz suite found it escaping as
            # an unexpected error rather than a refusal).
            raise ParseError(f"SVG: {where} is not valid base64 ({e})") from None
        if mime == "image/svg+xml" and depth >= 3:
            raise ParseError(f"SVG: {where} nests SVGs more than three deep; refused")
        clean = (_scrub_xml_bytes(raw, depth + 1)[0] if mime == "image/svg+xml"
                 else embedded.scrub_bytes(raw, f"SVG {where}"))
        if clean != raw:
            removed.append(f"{where}: its own metadata")
        head = xml[start:start + xml[start:end].index(b",") + 1]
        encoded = (base64.b64encode(clean) if is_b64
                   else urllib.parse.quote(clean, safe="").encode())
        out += xml[i:start] + head + encoded
        i = end
    return bytes(out + xml[i:])


def _remove_editor_entities(xml: bytes, doc: _Doc, removed: list[str]) -> bytes:
    """Illustrator binds its namespaces through DOCTYPE entities
    (`<!ENTITY ns_ai "http://ns.adobe.com/AdobeIllustrator/10.0/">`); once nothing
    references one, its declaration is the producer's name left behind."""
    for name, value in doc.entities:
        if value in EDITOR_NS and b"&" + name.encode() + b";" not in xml:
            xml, n = re.subn(rb"<!ENTITY\s+" + re.escape(name.encode())
                             + rb"\s+(\"[^\"]*\"|'[^']*')\s*>\s*", b"", xml)
            if n:
                removed.append(f"the DOCTYPE entity naming {EDITOR_NS[value]}")
    return xml


def _tidy_prolog(xml: bytes) -> bytes:
    """Before the root element there is no document text -- a parser reports none
    -- so the empty lines a removed comment or instruction leaves there go too.
    Inside the root the whitespace between elements is text the parser reports, and
    stays: the blank line is declared to the fingerprint guard instead."""
    root = re.search(rb"<(?![?!])", xml)
    if root is None:
        return xml
    head = re.sub(rb"\n[ \t]*(?=\n)", b"", xml[:root.start()])
    return head + xml[root.start():]


def _apply(xml: bytes, doc: _Doc, removed: list[str], depth: int) -> bytes:
    xml, n = xs.remove_comments(xml)
    if n:
        removed.append(f"{n} comment{'s' if n > 1 else ''}")
    xml = _remove_pis(xml, removed)
    for p in _svg_prefixes(doc):
        xml, n = xs.remove_elements(xml, _qualified(p, "metadata"))
        if n:
            removed.append("<metadata>")
        for tag in ("title", "desc"):
            xml = _remove_generator_labels(xml, _qualified(p, tag), removed)
    for p in _editor_prefixes(doc):
        name = EDITOR_NS[doc.prefixes[p]]
        for tag in sorted(_written_elements(xml, p)):
            xml, n = xs.remove_elements(xml, tag)
            if n:
                removed.append(f"{name}: <{tag}>")
        xml, n = xs.remove_attributes(xml, re.escape(p) + r":[A-Za-z_][\w.-]*")
        if n:
            removed.append(f"{name}: {n} attribute{'s' if n > 1 else ''}")
    unused = [p for p, uri in doc.prefixes.items() if p and (
        uri in EDITOR_NS or (uri in METADATA_NS and not re.search(
            rb"[<\s]" + re.escape(p.encode()) + rb":[A-Za-z_]", xml)))]
    if unused:
        xml, n = xs.remove_attributes(
            xml, "xmlns:(?:" + "|".join(re.escape(p) for p in unused) + ")")
        if n:
            removed.append("the editors' namespace declarations")
    # After the declarations: `xmlns:x="&ns_extend;"` is what used the entity.
    xml = _remove_editor_entities(xml, doc, removed)
    return _tidy_prolog(_clean_data_uris(xml, removed, depth))


# --------------------------------------------------------------------------- #
# The safety net
# --------------------------------------------------------------------------- #
def _kept_stream(doc: _Doc) -> list[tuple]:
    """The document as F1 must leave it: what the rules remove taken out, embedded
    payloads replaced by a placeholder."""
    out, skip = [], 0
    events = doc.events
    for k, ev in enumerate(events):
        kind = ev[0]
        if kind == "start":
            uri, _, local = ev[1].rpartition(" ")
            drop = uri in EDITOR_NS or (uri == SVG_NS and local == "metadata") \
                or (uri == SVG_NS and local in ("title", "desc")
                    and _only_generator_text(events, k))
            if skip or drop:
                skip += 1
                continue
            attrs = {}
            for name, value in ev[2].items():
                auri = name.rpartition(" ")[0] if " " in name else ""
                if auri in EDITOR_NS:
                    continue
                attrs[name] = _PAYLOAD.sub("<data>", value)
            out.append(("start", ev[1], tuple(sorted(attrs.items()))))
        elif kind == "end":
            if skip:
                skip -= 1
                continue
            out.append(ev)
        elif not skip:
            if out and out[-1][0] == "text":
                out[-1] = ("text", out[-1][1] + ev[1])
            else:
                out.append(ev)
    return [("text", _PAYLOAD.sub("<data>", e[1])) if e[0] == "text" else e
            for e in out]


def _only_generator_text(events: list[tuple], k: int) -> bool:
    nxt = events[k + 1] if k + 1 < len(events) else None
    after = events[k + 2] if k + 2 < len(events) else None
    if nxt is not None and nxt[0] == "end":
        return False
    return (nxt is not None and nxt[0] == "text" and after is not None
            and after[0] == "end" and bool(GENERATOR_TEXT.fullmatch(nxt[1])))


def _scrub_xml_bytes(xml: bytes, depth: int = 0) -> tuple[bytes, list[str]]:
    _check_encoding(xml)
    doc = _Doc(xml)
    removed: list[str] = []
    out = _apply(xml, doc, removed, depth)
    if _kept_stream(_Doc(out)) != _kept_stream(doc):
        raise ParseError("SVG F1 changed the drawing itself; refused")
    return out, removed


def _scrub(data: bytes, report: bool = False):
    xml, zipped = _decode(data)
    out, removed = _scrub_xml_bytes(xml)
    if zipped:
        out = gzip.compress(out, mtime=0)
        removed.append("the .svgz wrapper's original file name and time")
    return (out, removed) if report else out


def scrub(data: bytes) -> bytes:
    return _scrub(data)


def scrub_with_report(data: bytes) -> tuple[bytes, list[str]]:
    return _scrub(data, report=True)


def residuals(data: bytes) -> list[str]:
    xml, _ = _decode(data)
    doc = _Doc(xml)
    out = []
    if xs.remove_comments(xml)[1]:
        out.append("a comment survives")
    stream = [e for e in doc.events if e[0] == "start"]
    for ev in stream:
        uri, _, local = ev[1].rpartition(" ")
        if uri in EDITOR_NS:
            out.append(f"an {EDITOR_NS[uri]} element survives")
        if uri == SVG_NS and local == "metadata":
            out.append("<metadata> survives")
        if any(n.rpartition(" ")[0] in EDITOR_NS for n in ev[2] if " " in n):
            out.append("an editor's attribute survives")
    if any(uri in EDITOR_NS for uri in doc.prefixes.values()):
        out.append("an editor's namespace declaration survives")
    for n, (_, _, mime, is_b64, payload) in enumerate(_data_uris(xml)):
        if mime.startswith("image/") and mime != "image/svg+xml" and is_b64:
            try:
                raw = base64.b64decode(re.sub(rb"\s", b"", payload))
            except (ValueError, binascii.Error):
                out.append(f"embedded {mime} #{n + 1} is not valid base64")
                continue
            out += embedded.residuals(raw, f"embedded {mime} #{n + 1}")
    return list(dict.fromkeys(out))


def advise(data: bytes) -> list[str]:
    xml, _ = _decode(data)
    out = []
    paths = sorted({m.group().decode("latin-1") for m in _USER_PATH.finditer(
        re.sub(rb"data:[^\"')<\s]+", b"", xml))})
    if paths:
        out.append("the drawing refers to files on a computer by path ("
                   + ", ".join(p[:60] for p in paths[:3]) + "); a renderer reads those, "
                   "so they are kept -- remove them in the editor if they should not "
                   "be shared")
    fonts = [m for m in _data_uris(xml) if not m[2].startswith("image/")]
    if fonts:
        out.append(f"{len(fonts)} embedded font or stylesheet resource(s) kept as they "
                   "are: they are part of how the drawing looks")
    return out


def describe(data: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        xml, _ = _decode(data)
        doc = _Doc(xml)
    except ParseError:
        return out
    editors = sorted({EDITOR_NS[u] for u in doc.prefixes.values() if u in EDITOR_NS})
    if editors:
        out["Editor namespaces"] = ", ".join(editors)
    for name in (b"sodipodi:docname", b"inkscape:version", b"inkscape:export-filename"):
        m = re.search(re.escape(name) + rb'="([^"]*)"', xml)
        if m:
            out[name.decode()] = m.group(1).decode("utf-8", "replace")
    comments = re.findall(rb"<!--(.{0,80}?)-->", xml, re.S)
    if comments:
        out["Comments"] = " | ".join(c.decode("utf-8", "replace").strip()
                                     for c in comments[:3])
    images = [d[2] for d in _data_uris(xml)]
    if images:
        out["Embedded resources"] = ", ".join(images)
    return out
