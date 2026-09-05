"""Canonical XML re-serialisation — the F2 layer.

F1 only deletes, so every surviving part keeps the producer's own *spelling*: which
namespace prefixes it declared, in what order it wrote attributes, whether it emits
`<a/>` or `<a />`, whether it escapes `>` in text, whether the XML declaration says
`standalone` and ends in LF or CRLF. E-DOCX measured all of that separating producers
after F1 (docs/p3_documents_plan.md §2.12), and none of it is content.

So F2 re-serialises every part through **one writer**. This is the direct analogue of
PDF F2's content-stream canonicaliser, one layer up: it closes the *spelling* of the
document model. What it cannot close is the model's *substance* — which parts a
producer emits, which styles it defines, how it chooses to represent a paragraph —
and that distinction is the whole point of measuring before and after.

Three traps, each of which silently breaks a document:

1. **`mc:Ignorable` names prefixes, not namespaces.** Renaming a prefix without
   rewriting that attribute leaves Word ignoring a prefix that no longer exists;
   dropping an "unused" declaration that `mc:Ignorable` still lists is the same bug
   from the other side. Both are handled by mapping through the URI.
2. **Text is content.** Re-indenting a part would change what the document says —
   `<w:t>` holds the visible words, and whitespace inside it is significant when
   `xml:space="preserve"` is set. Nothing here reformats text; only markup is
   rewritten.
3. **The XML declaration is not a processing instruction.** It looks like one, and
   removing it would leave a part without its encoding.
"""
from __future__ import annotations

import io
import re
from xml.etree import ElementTree as ET

from ...errors import ParseError

XML_NS = "http://www.w3.org/XML/1998/namespace"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"

# The prefixes the OOXML ecosystem actually uses. Pinning them to their conventional
# spelling is what lets a canonical part still look like a normal one -- an invented
# prefix would be a signature in exactly the way limit #9 describes.
CANONICAL_PREFIXES = {
    "http://schemas.openxmlformats.org/wordprocessingml/2006/main": "w",
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships": "r",
    "http://schemas.openxmlformats.org/package/2006/relationships": "rel",
    "http://schemas.openxmlformats.org/package/2006/content-types": "ct",
    "http://schemas.openxmlformats.org/markup-compatibility/2006": "mc",
    "http://schemas.openxmlformats.org/drawingml/2006/main": "a",
    "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing": "wp",
    "http://schemas.openxmlformats.org/officeDocument/2006/math": "m",
    "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties": "ep",
    "http://schemas.openxmlformats.org/package/2006/metadata/core-properties": "cp",
    "http://purl.org/dc/elements/1.1/": "dc",
    "http://purl.org/dc/terms/": "dcterms",
    "http://www.w3.org/2001/XMLSchema-instance": "xsi",
    "urn:schemas-microsoft-com:vml": "v",
    "urn:schemas-microsoft-com:office:office": "o",
    "http://schemas.microsoft.com/office/word/2010/wordml": "w14",
    "http://schemas.microsoft.com/office/word/2012/wordml": "w15",
}

# `mc` attributes whose VALUE is a whitespace-separated list of prefixes. Rewriting
# prefixes without rewriting these is the trap that breaks a document silently.
_PREFIX_LIST_ATTRS = (f"{{{MC_NS}}}Ignorable", f"{{{MC_NS}}}ProcessContent",
                      f"{{{MC_NS}}}MustUnderstand")

# Word's own declaration, which is also the largest real crowd: double quotes,
# `standalone`, CRLF. Picking a form nobody writes would be a signature; picking the
# most common one is the same reasoning that took the ZIP timestamp to 1980.
DECLARATION = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'


def _escape_text(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_attr(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("\t", "&#9;").replace("\n", "&#10;")
            .replace("\r", "&#13;"))


def _parse_with_prefixes(data: bytes) -> tuple[ET.Element, dict[str, str]]:
    """Parse, and recover the prefix -> URI map the document declared.

    `ElementTree` resolves namespaces into `{uri}local` and throws the prefixes away,
    which is exactly what `mc:Ignorable` needs back.
    """
    prefixes: dict[str, str] = {}
    root = None
    try:
        for event, payload in ET.iterparse(io.BytesIO(data),
                                           events=("start-ns", "start")):
            if event == "start-ns":
                prefix, uri = payload
                prefixes.setdefault(prefix, uri)
            elif root is None:
                root = payload
    except ET.ParseError as exc:
        raise ParseError(f"malformed XML: {exc}") from exc
    if root is None:
        raise ParseError("no root element")
    return root, prefixes


def _used_namespaces(root: ET.Element, *, skip_prefix_lists: bool = False) -> set[str]:
    """Every namespace URI referenced by an element or attribute name.

    `skip_prefix_lists` excludes the `mc:` attributes whose survival is not yet
    decided. Without that distinction the output is **not idempotent**, and it took a
    self-check to notice: a document declaring `mc` solely to carry
    `mc:Ignorable="w14 w15"` would have both those namespaces pruned as unused, which
    empties `mc:Ignorable`, which drops the attribute — leaving `mc` declared on pass
    one and unused on pass two. Canonical output that changes when canonicalised
    again is not canonical.
    """
    used: set[str] = set()
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        if el.tag.startswith("{"):
            used.add(el.tag[1:el.tag.index("}")])
        for name in el.attrib:
            if skip_prefix_lists and name in _PREFIX_LIST_ATTRS:
                continue
            if name.startswith("{"):
                used.add(name[1:name.index("}")])
    return used


def _assign_prefixes(uris: set[str], default_uri: str | None = None) -> dict[str, str]:
    """URI -> prefix. Known namespaces keep their conventional spelling; unknown ones
    get a stable generated prefix, ordered by URI so the result never depends on
    dictionary iteration order.

    `default_uri` keeps the **default namespace as the default namespace**, mapping to
    the empty prefix. That is not cosmetic and it is not optional: rewriting
    `<Types xmlns="…">` as `<ct:Types xmlns:ct="…">` is namespace-equivalent XML and
    **every real OPC reader rejects it** — LibreOffice answered `source file could not
    be loaded` for all five producers, which is how this was found. Whether a part
    uses a default namespace is decided by the part type rather than by the producer
    (every producer writes `[Content_Types].xml` and `.rels` with a default namespace
    and `document.xml` with the `w:` prefix), so preserving it preserves no
    fingerprint.
    """
    out: dict[str, str] = {}
    unknown = []
    for uri in sorted(uris):
        if uri == default_uri:
            out[uri] = ""
        elif uri == XML_NS:
            out[uri] = "xml"
        elif uri in CANONICAL_PREFIXES:
            out[uri] = CANONICAL_PREFIXES[uri]
        else:
            unknown.append(uri)
    for i, uri in enumerate(unknown):
        out[uri] = f"ns{i}"
    return out


def _qname(name: str, prefixes: dict[str, str]) -> str:
    if not name.startswith("{"):
        return name
    uri, local = name[1:].split("}", 1)
    prefix = prefixes.get(uri)
    return f"{prefix}:{local}" if prefix else local


def _default_namespace(root: ET.Element, old_prefixes: dict[str, str]) -> str | None:
    """The URI the document used as its default namespace, if the root is in it.

    Only the root's own namespace qualifies: a default declared for some subtree is
    not something any OOXML part does, and honouring it would mean tracking scoped
    declarations for no benefit.
    """
    uri = old_prefixes.get("")
    if uri is None or not isinstance(root.tag, str):
        return None
    return uri if root.tag.startswith(f"{{{uri}}}") else None


def _rewrite_prefix_list(value: str, old: dict[str, str],
                         new: dict[str, str]) -> str:
    """Map a whitespace-separated prefix list through the URI, dropping any entry
    whose namespace is no longer declared."""
    out = []
    for token in value.split():
        uri = old.get(token)
        if uri is None:
            continue
        prefix = new.get(uri)
        if prefix:
            out.append(prefix)
    return " ".join(out)


def _serialize(el: ET.Element, prefixes: dict[str, str], declared: dict[str, str],
               attrs_for, root: bool, buf: list[str]) -> None:
    tag = _qname(el.tag, prefixes)
    buf.append(f"<{tag}")

    if root:
        # Every namespace the document uses is declared once, on the root, in prefix
        # order — the default namespace first. `xml` is bound by the spec and must
        # never be declared.
        for uri, prefix in sorted(declared.items(), key=lambda kv: kv[1]):
            if prefix == "xml":
                continue
            if prefix == "":
                buf.append(f' xmlns="{_escape_attr(uri)}"')
            else:
                buf.append(f' xmlns:{prefix}="{_escape_attr(uri)}"')

    for name, value in sorted(attrs_for(el).items(),
                              key=lambda kv: _qname(kv[0], prefixes)):
        buf.append(f' {_qname(name, prefixes)}="{_escape_attr(value)}"')

    children = list(el)
    text = el.text or ""
    if not children and not text:
        buf.append("/>")
        return
    buf.append(">")
    if text:
        buf.append(_escape_text(text))
    for child in children:
        _serialize(child, prefixes, declared, attrs_for, False, buf)
        if child.tail:
            buf.append(_escape_text(child.tail))
    buf.append(f"</{tag}>")


def canonicalize(data: bytes) -> bytes:
    """Re-serialise one XML part canonically. Raises ParseError on malformed input.

    Idempotent by construction: `canonicalize(canonicalize(x)) == canonicalize(x)`,
    which a test asserts on every part of every producer in the corpus. That property
    is not decoration — a canonical form that still changes on a second pass is not
    canonical, and the first version of this function was not (see
    `_used_namespaces`).
    """
    root, old_prefixes = _parse_with_prefixes(data)
    default_uri = _default_namespace(root, old_prefixes)

    # Namespaces needed by names that are certainly emitted...
    used = _used_namespaces(root, skip_prefix_lists=True)
    prefixes = _assign_prefixes(used, default_uri)

    # ...then resolve the prefix-list attributes against those, since a list that
    # prunes to nothing takes its attribute -- and possibly the `mc` declaration --
    # with it.
    resolved: dict[int, dict[str, str]] = {}
    mc_needed = False
    for el in root.iter():
        emitted = {}
        for name, value in el.attrib.items():
            if name in _PREFIX_LIST_ATTRS:
                value = _rewrite_prefix_list(value, old_prefixes, prefixes)
                if not value:
                    continue
                mc_needed = True
            emitted[name] = value
        resolved[id(el)] = emitted

    if mc_needed and MC_NS not in used:
        used.add(MC_NS)
        prefixes = _assign_prefixes(used, default_uri)

    declared = {uri: p for uri, p in prefixes.items() if uri != XML_NS}
    buf: list[str] = []
    _serialize(root, prefixes, declared, lambda el: resolved[id(el)], True, buf)
    return DECLARATION + "".join(buf).encode("utf-8")


def spelling_features(data: bytes) -> dict:
    """What this part's spelling looks like — used by tests to show the difference
    between a producer's form and the canonical one."""
    decl = re.match(rb"<\?xml[^>]*\?>(\r\n|\r|\n)?", data)
    return {
        "declaration": decl.group(0) if decl else b"",
        "spaced_selfclose": len(re.findall(rb"\s/>", data)),
        "tight_selfclose": len(re.findall(rb"[^\s]/>", data)),
        "ns_declared": sorted({m.decode() for m in
                               re.findall(rb"xmlns:([\w.-]+)=", data)}),
    }
