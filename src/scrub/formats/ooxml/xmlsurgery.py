"""Byte-level XML surgery — deletion that leaves every other byte where it was.

F1 means bit-preserving in the sense the PDF tier established: **we only delete.**
That rules out the obvious implementation. Parsing a part with `ElementTree` and
re-serialising it rewrites the whole file — namespace declarations get reordered and
pruned, attribute order changes, `<a/>` becomes `<a />`, the XML declaration's quotes
flip — none of which changes the document, all of which changes the bytes. That is a
*canonicalisation*, it belongs to F2, and doing it at F1 would mean the tier could no
longer claim the untouched parts are untouched.

So the surviving parts are edited in place: find the span of an attribute or an
element, cut it, leave everything else alone.

Regex is not sufficient on its own and is not used on its own. Attribute values are
safe to match (`"` inside a value must be escaped as `&quot;`, so `[^"]*` cannot run
past the closing quote), but **element removal needs a scanner**, because an element
may contain children with the same prefix and a non-greedy match to the next closing
tag would cut in the wrong place. `_element_span` tracks depth for the specific tag.

Nothing here interprets the document. It is deletion with the boundaries computed
correctly, which is exactly what "only delete" requires.
"""
from __future__ import annotations

import re

# An XML name: prefix:local, both restricted to what OOXML actually uses. Deliberately
# not the full Name production -- a part using exotic name characters is a part we
# would rather refuse than edit blind.
_NAME = r"[A-Za-z_][\w.-]*(?::[A-Za-z_][\w.-]*)?"


def remove_attributes(xml: bytes, pattern: str) -> tuple[bytes, int]:
    """Delete every attribute whose *written* name matches `pattern` (fullmatch).

    Written name, prefix included: OOXML parts declare their prefixes at the root and
    use them consistently, and F1 must not rewrite prefixes to resolve them.
    """
    rx = re.compile(pattern)
    out = bytearray()
    i = 0
    removed = 0
    for m in re.finditer(rb'\s+(' + _NAME.encode() + rb')\s*=\s*"[^"]*"', xml):
        if not rx.fullmatch(m.group(1).decode("ascii", "replace")):
            continue
        out += xml[i:m.start()]
        i = m.end()
        removed += 1
    out += xml[i:]
    return bytes(out), removed


def _element_span(xml: bytes, tag: str, start: int) -> tuple[int, int] | None:
    """The byte span of the next `<tag …>` at or after `start`, closing tag included.

    Handles both forms: self-closing `<tag/>` and a paired `<tag>…</tag>` whose body
    may contain nested elements — including further `<tag>` elements, which is why
    this counts depth instead of searching for the first `</tag>`.
    """
    open_rx = re.compile(rb"<" + re.escape(tag.encode()) + rb"(?=[\s/>])")
    close_tag = b"</" + tag.encode() + b">"
    m = open_rx.search(xml, start)
    if m is None:
        return None

    depth = 0
    pos = m.start()
    while pos < len(xml):
        nxt_open = open_rx.search(xml, pos)
        nxt_close = xml.find(close_tag, pos)
        # Where does this tag actually end? Scan for the '>' that closes it, skipping
        # any '>' inside an attribute value.
        if nxt_open is not None and (nxt_close < 0 or nxt_open.start() < nxt_close):
            end = _tag_end(xml, nxt_open.start())
            if end is None:
                return None
            if xml[end - 1:end] == b"/":          # self-closing
                if depth == 0:
                    return (m.start(), end + 1)
            else:
                depth += 1
            pos = end + 1
            continue
        if nxt_close < 0:
            return None
        depth -= 1
        if depth == 0:
            return (m.start(), nxt_close + len(close_tag))
        pos = nxt_close + len(close_tag)
    return None


def _tag_end(xml: bytes, start: int) -> int | None:
    """Index of the `>` closing the tag that begins at `start`, ignoring any `>`
    that sits inside a quoted attribute value."""
    i = start
    quote = None
    while i < len(xml):
        c = xml[i:i + 1]
        if quote:
            if c == quote:
                quote = None
        elif c in (b'"', b"'"):
            quote = c
        elif c == b">":
            return i
        i += 1
    return None


def remove_elements(xml: bytes, tag: str) -> tuple[bytes, int]:
    """Delete every `<tag>` element, with its children and closing tag."""
    out = xml
    removed = 0
    while True:
        span = _element_span(out, tag, 0)
        if span is None:
            return out, removed
        out = out[:span[0]] + out[span[1]:]
        removed += 1


def unwrap_elements(xml: bytes, tag: str) -> tuple[bytes, int]:
    """Delete a `<tag>`'s own tags while KEEPING its children.

    This is how a tracked insertion is accepted: `<w:ins …><w:r>…</w:r></w:ins>`
    becomes `<w:r>…</w:r>`, so the text stays exactly where it was and only the
    revision wrapper — which carries the author's name and the date — goes. Deleting
    the element outright would delete the reader's text with it.
    """
    out = xml
    removed = 0
    search_from = 0
    while True:
        span = _element_span(out, tag, search_from)
        if span is None:
            return out, removed
        end = _tag_end(out, span[0])
        if end is None:
            return out, removed
        if out[end - 1:end] == b"/":                  # self-closing: nothing inside
            out = out[:span[0]] + out[span[1]:]
        else:
            close = len(b"</" + tag.encode() + b">")
            inner = out[end + 1:span[1] - close]
            out = out[:span[0]] + inner + out[span[1]:]
        removed += 1
        search_from = span[0]


def remove_elements_where(xml: bytes, tag: str,
                          predicate) -> tuple[bytes, int]:
    """Delete `<tag>` elements whose opening tag text satisfies `predicate`.

    Used where the element itself is legitimate and only some instances are metadata
    -- a `w:bookmarkStart` named `_GoBack` is the cursor's last position, while a
    user's own bookmark is a link target that other parts reference by name.
    """
    out = xml
    removed = 0
    search_from = 0
    while True:
        span = _element_span(out, tag, search_from)
        if span is None:
            return out, removed
        end = _tag_end(out, span[0])
        opening = out[span[0]:(end + 1 if end is not None else span[1])]
        if predicate(opening):
            out = out[:span[0]] + out[span[1]:]
            removed += 1
            search_from = span[0]
        else:
            search_from = span[0] + 1


def attribute_value(opening_tag: bytes, name: str) -> bytes | None:
    m = re.search(re.escape(name.encode()) + rb'\s*=\s*"([^"]*)"', opening_tag)
    return m.group(1) if m else None

def remove_comments(xml: bytes) -> tuple[bytes, int]:
    """Delete every XML comment, skipping over CDATA sections.

    An XML comment is **never rendered**, so removing one cannot change what a reader
    sees — which makes it a deletion, and therefore F1's job rather than F2's. It is
    also a perfect hiding place: arbitrary text, invisible in Word, and reached by
    none of the rules in the locus census, because a comment has no element name and
    no attribute name for a rule to match on. No producer in this project's corpus
    writes one, which is exactly why it went unnoticed until it was looked for.

    CDATA is skipped rather than ignored: `<![CDATA[ ... <!-- ... ]]>` is *text*, and
    a scanner that cut at the first `<!--` would corrupt the document.
    """
    out = bytearray()
    i = 0
    removed = 0
    while i < len(xml):
        cdata = xml.find(b"<![CDATA[", i)
        comment = xml.find(b"<!--", i)
        if comment < 0:
            out += xml[i:]
            break
        if 0 <= cdata < comment:
            end = xml.find(b"]]>", cdata)
            if end < 0:                       # unterminated; copy the rest verbatim
                out += xml[i:]
                break
            out += xml[i:end + 3]
            i = end + 3
            continue
        end = xml.find(b"-->", comment)
        if end < 0:
            out += xml[i:]
            break
        out += xml[i:comment]
        i = end + 3
        removed += 1
    return bytes(out), removed


def remove_processing_instructions(xml: bytes) -> tuple[bytes, int]:
    """Delete every processing instruction except the XML declaration.

    A PI is an instruction to a consuming application, never rendered, and free to
    carry arbitrary text — the same argument as for comments. The XML declaration
    looks like one and is not: it must stay, so `<?xml` is skipped explicitly.

    CDATA is skipped for the same reason as in `remove_comments`.
    """
    out = bytearray()
    i = 0
    removed = 0
    while i < len(xml):
        cdata = xml.find(b"<![CDATA[", i)
        pi = xml.find(b"<?", i)
        if pi < 0:
            out += xml[i:]
            break
        if 0 <= cdata < pi:
            end = xml.find(b"]]>", cdata)
            if end < 0:
                out += xml[i:]
                break
            out += xml[i:end + 3]
            i = end + 3
            continue
        end = xml.find(b"?>", pi)
        if end < 0:
            out += xml[i:]
            break
        if re.match(rb"<\?xml[\s?]", xml[pi:pi + 6]):
            out += xml[i:end + 2]
            i = end + 2
            continue
        out += xml[i:pi]
        i = end + 2
        removed += 1
    return bytes(out), removed


def has_doctype(xml: bytes) -> bool:
    """Is there a DOCTYPE declaration before the root element?

    A DOCTYPE can define internal entities, which is both a data-hiding channel and
    the entry point for the classic expansion attacks. We do not model it, so a part
    carrying one is refused rather than edited blind — the standing answer for
    anything this project cannot account for.
    """
    head = xml[:4096]
    i = head.find(b"<!DOCTYPE")
    if i < 0:
        return False
    root = re.search(rb"<[A-Za-z_]", head)
    return root is None or i < root.start()
