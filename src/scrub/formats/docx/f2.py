"""DOCX F2 — canonical re-serialisation, and the revisions F1 had to refuse.

F1 only deletes, so every part keeps the producer's own *spelling*: its namespace
prefixes, its attribute order, whether it writes `<a/>` or `<a />`, whether it
escapes `>` in text, how its XML declaration is punctuated. E-DOCX measured all of
that still separating producers after F1 (§2.12), and none of it is content — so F2
re-serialises every part through one writer. It is the direct analogue of PDF F2's
content-stream canonicaliser, one layer up.

F2 also does the thing F1 correctly refused. **Tracked changes and comments are
content**: with markup switched on a reader sees them, so deleting them at a
bit-preserving tier would be a content-preservation failure. At F2 the tier already
licenses a rewrite, so revisions are *accepted* — insertions unwrapped so their text
stays exactly where it was, deletions removed as the author intended — and comments
are dropped. That is a real, stated cost and not a free win: **the markup view is
gone, and it cannot be recovered.** A reviewer's document becomes the document as it
stands. Recorded in `docs/limits.md` before the tier is offered, the way F3's loss of
the PDF text layer was.
"""
from __future__ import annotations

import re

from ...errors import ContentError, ParseError
from ..ooxml import opc, xmlcanon
from ..ooxml import xmlsurgery as xs
from . import f1

# Revision wrappers whose CONTENT the reader keeps once the change is accepted.
_UNWRAP = ("w:ins", "w:moveTo", "w:moveToRangeStart", "w:moveToRangeEnd",
           "w:customXmlInsRangeStart", "w:customXmlInsRangeEnd")

# Revision wrappers whose content the author deleted, plus the comment anchors that
# have nothing left to point at once `word/comments*.xml` is gone.
_REMOVE = ("w:del", "w:moveFrom", "w:moveFromRangeStart", "w:moveFromRangeEnd",
           "w:commentRangeStart", "w:commentRangeEnd", "w:commentReference",
           "w:customXmlDelRangeStart", "w:customXmlDelRangeEnd")

# Attributes that survive only on a revision wrapper; once the wrappers are gone any
# left behind are an author name or a date on some other element.
_REVISION_ATTRS = r"w:author|w:date|w:oouthor|w16du:dateUtc"

_COMMENT_PARTS = (
    re.compile(r"^word/comments.*\.xml$"),
    re.compile(r"^word/threadedComments/"),
)


def _accept_revisions(body: bytes) -> bytes:
    for tag in _REMOVE:
        body, _ = xs.remove_elements(body, tag)
    for tag in _UNWRAP:
        body, _ = xs.unwrap_elements(body, tag)
    body, _ = xs.remove_attributes(body, _REVISION_ATTRS)
    return body


def scrub(data: bytes) -> bytes:
    """F1's deletions, plus revision acceptance, plus canonical re-serialisation."""
    pkg = opc.parse(data)
    if pkg.flavour != "docx":
        raise ParseError(f"not a DOCX package (looks like {pkg.flavour})")

    # F2 handles revisions and comments, so only the refusals that are *structural*
    # still apply -- macros, OLE objects, signatures, a DOCTYPE, an inconsistent
    # package. Reusing F1's preflight wholesale would refuse the very inputs this
    # tier exists to accept.
    problems = [p for p in f1._preflight(pkg)
                if "content displayed" not in p and "comments are content" not in p]
    if problems:
        raise ContentError("; ".join(sorted(set(problems))))

    dropped = {n for n in pkg.parts()
               if f1._is_dropped(n) or any(r.search(n) for r in _COMMENT_PARTS)}

    kept: dict[str, bytes] = {}
    for entry in pkg.archive.entries:
        if entry.is_dir or entry.name in dropped:
            continue
        body = entry.content()
        if entry.name == opc.CONTENT_TYPES:
            body = f1._prune_content_types(body, dropped)
        elif entry.name.endswith(".rels"):
            body = f1._prune_rels(f1._rels_source(entry.name), body, dropped)
        elif entry.name.startswith("word/media/"):
            kept[entry.name] = f1._scrub_media(entry.name, body)
            continue
        elif entry.name.endswith(".xml"):
            body = f1._scrub_xml(entry.name, body)
            body = _accept_revisions(body)

        if entry.name.endswith((".xml", ".rels")):
            body = xmlcanon.canonicalize(body)
        kept[entry.name] = body

    for name in [n for n in kept if n.endswith(".rels")]:
        if not re.search(rb"<[\w.-]*:?Relationship[\s/>]", kept[name]):
            del kept[name]
            dropped.add(name)

    from ..ooxml import zipwrite
    out = zipwrite.write(kept)

    result = opc.parse(out)
    orphans = result.orphans(set(result.parts()))
    if orphans:
        raise ParseError(f"scrub left dangling relationships: {orphans}")
    return out


def residuals(data: bytes) -> list[str]:
    """F1's failure checks, plus the ones only F2 can be held to."""
    out = f1.residuals(data)
    try:
        pkg = opc.parse(data)
    except ParseError as exc:
        return [f"unparseable: {exc}"]

    for entry in pkg.archive.entries:
        if not entry.name.endswith((".xml", ".rels")):
            continue
        try:
            body = entry.content()
        except ParseError:
            continue
        if any(r.search(entry.name) for r in _COMMENT_PARTS):
            out.append(f"{entry.name}: a comments part survived F2")
        for tag in _REMOVE + _UNWRAP:
            if re.search(rb"<" + re.escape(tag.encode()) + rb"[\s/>]", body):
                out.append(f"{entry.name}: <{tag}> survived")
        if body != xmlcanon.canonicalize(body):
            out.append(f"{entry.name}: not in canonical form")
    return out


def advisories(data: bytes) -> list[str]:
    """What F2 still leaves. The namespace-set advisory is gone by construction —
    an unused declaration is dropped during canonicalisation — so what remains is
    substance: bookmark names, external targets, and the font table."""
    return [a for a in f1.advisories(data) if "versioned namespaces" not in a]
