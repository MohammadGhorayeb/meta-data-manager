"""Element spans from a real XML parser, for deletion by resolved name.

`xmlsurgery` finds elements by their *written* name, which is right for OOXML (its
parts declare their prefixes once, at the root, and keep them). EPUB's package
document is not like that: one producer writes `<dc:creator>`, another declares
the Dublin Core namespace as the default on `<metadata>` and writes `<creator>`,
a third uses `<opf:meta>`. What decides whether an element is metadata is its
namespace, so this module asks expat, and returns each element's byte span in the
input, so the edit is still a cut that leaves every other byte where it was.

Expat's byte index on an end event is the start of `</name>` for an element with
content, but the byte *after* the tag for an empty `<name/>` -- which, inside a
parent, is often the parent's `</`. So the self-closing check is made on the start
tag's own bytes, never inferred from the end event.
"""
from __future__ import annotations

import xml.parsers.expat
from dataclasses import dataclass, field

from ...errors import ParseError
from .xmlsurgery import _tag_end


@dataclass
class Element:
    uri: str
    local: str
    attrs: dict[str, str]          # resolved names: "uri local", or "local" unqualified
    start: int                     # '<' of the start tag
    open_end: int                  # just after the start tag's '>'
    end: int = 0                   # just after the element (its end tag, or itself)
    end_tag_start: int = 0         # '<' of `</name>`; == end for `<name/>`
    parent: int | None = None
    text: str = ""                 # character data directly inside, concatenated
    children: list[int] = field(default_factory=list)

    @property
    def inner(self) -> tuple[int, int]:
        """The span between the tags (empty for `<name/>`)."""
        return self.open_end, self.end_tag_start

    def attr(self, local: str, uri: str = "") -> str | None:
        return self.attrs.get(f"{uri} {local}" if uri else local)


def parse(data: bytes, what: str = "XML") -> list[Element]:
    """Every element, in document order, with its span. Refuses what is not
    well-formed, and external or parameter entities (nothing here fetches)."""
    if data[:2] in (b"\xfe\xff", b"\xff\xfe"):
        raise ParseError(f"{what}: UTF-16 is not supported")
    els: list[Element] = []
    stack: list[int] = []
    p = xml.parsers.expat.ParserCreate(namespace_separator=" ")
    p.SetParamEntityParsing(xml.parsers.expat.XML_PARAM_ENTITY_PARSING_NEVER)

    def start(name, attrs):
        uri, _, local = name.rpartition(" ")
        pos = p.CurrentByteIndex
        end = _tag_end(data, pos)
        if end is None:
            raise ParseError(f"{what}: a start tag with no end")
        el = Element(uri, local, dict(attrs), pos, end + 1,
                     parent=stack[-1] if stack else None)
        if stack:
            els[stack[-1]].children.append(len(els))
        stack.append(len(els))
        els.append(el)

    def finish(_name):
        el = els[stack.pop()]
        if data[el.open_end - 2:el.open_end - 1] == b"/":     # <name/>
            el.end = el.end_tag_start = el.open_end
            return
        pos = p.CurrentByteIndex
        close = data.find(b">", pos)
        if close < 0:
            raise ParseError(f"{what}: an end tag with no '>'")
        el.end_tag_start, el.end = pos, close + 1

    def text(t):
        if stack:
            els[stack[-1]].text += t

    def external(*_):
        raise ParseError(f"{what}: an external entity reference; refused")

    def entity(name, is_param, value, base, system_id, public_id, notation):
        if is_param or system_id or public_id or notation:
            raise ParseError(f"{what}: entity {name!r} is not a plain string; refused")

    p.StartElementHandler = start
    p.EndElementHandler = finish
    p.CharacterDataHandler = text
    p.ExternalEntityRefHandler = external
    p.EntityDeclHandler = entity
    try:
        p.Parse(data, True)
    except xml.parsers.expat.ExpatError as exc:
        raise ParseError(f"{what}: not well-formed XML ({exc})") from None
    return els


def cut(data: bytes, spans: list[tuple[int, int]]) -> bytes:
    """`data` without the given spans (overlaps and nesting allowed: an outer span
    swallows an inner one)."""
    out = bytearray()
    pos = 0
    for s, e in sorted(spans):
        if e <= pos:
            continue
        out += data[pos:max(s, pos)]
        pos = e
    out += data[pos:]
    return bytes(out)


def splice(data: bytes, edits: list[tuple[int, int, bytes]]) -> bytes:
    """`data` with each span replaced by its bytes. Spans must not overlap."""
    out = bytearray()
    pos = 0
    for s, e, new in sorted(edits):
        if s < pos:
            raise ParseError("overlapping XML edits")
        out += data[pos:s] + new
        pos = e
    out += data[pos:]
    return bytes(out)


def escape_text(value: str) -> bytes:
    return (value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .encode("utf-8"))
