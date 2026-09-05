"""The document's visible text — the content identity every DOCX tier is held to.

Extracted by parsing, not by matching. The obvious regex —
`<w:t[^>]*>(.*?)</w:t>` — is wrong in a way that hides until F2 exists: an empty run
is written `<w:t></w:t>` by most producers, canonicalisation turns it into `<w:t/>`,
and the pattern then matches `<w:t/` as the opening tag and runs on to the *next*
run's closing tag, swallowing the markup between them. It reported that F2 had
changed the document's text when F2 had done nothing of the sort — and the same
pattern was the harness plugin's content-identity oracle, where a false negative is
much worse than a false alarm.

`w:tab` and `w:br` are rendered as a tab and a line break, so they are part of the
text a reader sees. `w:delText` is not: it is the *deleted* half of a tracked change,
shown only with markup on, and F2 accepts revisions — so counting it would make the
identity check fail for a change the tier makes deliberately.
"""
from __future__ import annotations

from xml.etree import ElementTree as ET

from ...errors import ParseError
from ..ooxml import opc

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

MAIN_PART = "word/document.xml"


def part_text(body: bytes) -> str:
    """The visible text of one WordprocessingML part."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise ParseError(f"malformed WordprocessingML: {exc}") from exc

    out: list[str] = []
    for el in root.iter():
        if el.tag == f"{W}t":
            out.append(el.text or "")
        elif el.tag == f"{W}tab":
            out.append("\t")
        elif el.tag in (f"{W}br", f"{W}cr"):
            out.append("\n")
    return "".join(out)


def document_text(data: bytes) -> str:
    """The visible text of a package's main document part."""
    pkg = opc.parse(data)
    entry = pkg.archive.by_name(MAIN_PART)
    if entry is None:
        raise ParseError(f"no {MAIN_PART} in package")
    return part_text(entry.content())
