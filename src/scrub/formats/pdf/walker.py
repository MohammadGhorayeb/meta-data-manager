"""PDF structure walker (byte depth).

A PDF is not a stream of chunks like FLAC or a tree of boxes like ISOBMFF. It is a
*random-access* file: a body of numbered objects, then a cross-reference table
mapping object number to byte offset, then a trailer, then `startxref` pointing at
the table. Edits are made by **appending** a whole new (body, xref, trailer) group
whose trailer's `/Prev` points back at the previous one. So a file's edit history is
a linked list, walked backwards from the end.

    [junk?] %PDF-1.x  [binary comment]  objects…  xref  trailer  startxref  %%EOF
                                        objects…  xref  trailer  startxref  %%EOF   <- revision 2
                                                                                    …

This walker reads the file the way a forensic analyst does — as bytes, not through a
library — and its job is **accounting**, not interpretation:

  * enumerate every revision, so F1 can assert it collapsed them to one;
  * enumerate every physical `N G obj` definition, so F1 can assert the ones it
    dropped are absent from the output. pikepdf gives the *reachable* set; the
    difference between the two is the removed set, and asserting on that difference
    is what `m4a/f1.py` does for `mdat`;
  * fail closed on structures we cannot reason about — encryption, junk before the
    header, bytes past the final `%%EOF`, a hybrid-reference file.

It deliberately does **not** parse objects. pikepdf is a better object parser than
anything written here would be, and the W0 decision only moved byte *emission* into
this project, not byte *interpretation*. Two parsers, two jobs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ...errors import ParseError

MAGIC = b"%PDF-"

# ISO 32000 §7.5.2 allows up to 1024 bytes of junk before the header, with all
# offsets then relative to the header rather than to the file. Real files with junk
# here are almost always a concatenation or an attack; we fail closed instead.
MAX_HEADER_SEARCH = 1024

_STARTXREF = re.compile(rb"startxref\s+(\d+)\s*?%%EOF")
_OBJ_DEF = re.compile(rb"(?:^|[\s>\]])(\d+)\s+(\d+)\s+obj\b")
_PREV = re.compile(rb"/Prev\s+(\d+)")
_XREFSTM = re.compile(rb"/XRefStm\s+(\d+)")
_HEADER = re.compile(rb"%PDF-(\d)\.(\d)")


@dataclass
class ObjDef:
    """One physical `N G obj` in the file. Several may share a number: that is what
    an incremental update looks like, and the later one wins."""
    num: int
    gen: int
    offset: int


@dataclass
class Revision:
    """One appended (body, xref, trailer) group."""
    xref_offset: int
    xref_kind: str                 # "table" | "stream"
    eof_end: int                   # byte just past this revision's %%EOF
    prev: int | None = None        # /Prev — the previous revision's xref offset
    xrefstm: int | None = None     # /XRefStm — hybrid-reference cross-link
    encrypted: bool = False        # /Encrypt in this revision's trailer


@dataclass
class Layout:
    header: bytes                          # e.g. b"%PDF-1.4"
    version: tuple[int, int]
    binary_comment: bytes | None           # the `%` + 4 high bytes producers emit
    revisions: list[Revision] = field(default_factory=list)
    objects: list[ObjDef] = field(default_factory=list)
    trailing: bytes = b""                  # bytes after the final %%EOF
    linearized: bool = False
    object_streams: int = 0                # `/Type /ObjStm` containers seen

    @property
    def encrypted(self) -> bool:
        return any(r.encrypted for r in self.revisions)

    @property
    def objects_fully_enumerated(self) -> bool:
        """False when the file uses object streams.

        `ObjStm` holds a run of objects *compressed inside another object*, so they
        have no `N G obj` bytes of their own and a byte scan cannot see them. Any
        ledger built from `objects` is therefore a lower bound on such a file, and
        code that treats it as complete would report a falsely clean result. Callers
        must check this rather than trusting the count.
        """
        return self.object_streams == 0

    @property
    def hybrid(self) -> bool:
        """A classic trailer carrying `/XRefStm` has *two* cross-reference structures.
        Old and new readers can therefore see different documents from the same file —
        a genuine steganographic channel, not a curiosity."""
        return any(r.xrefstm is not None for r in self.revisions)

    @property
    def superseded(self) -> dict[int, int]:
        """{object number: how many older definitions exist}. Non-empty means history."""
        counts: dict[int, int] = {}
        for o in self.objects:
            counts[o.num] = counts.get(o.num, 0) + 1
        return {n: c - 1 for n, c in counts.items() if c > 1}


def _find_header(data: bytes) -> tuple[int, bytes, tuple[int, int]]:
    m = _HEADER.search(data, 0, MAX_HEADER_SEARCH + 8)
    if not m:
        raise ParseError("PDF: no %PDF-x.y header in the first 1024 bytes")
    if m.start() != 0:
        # Offsets in a junk-prefixed file are relative to the header, so a scrubber
        # that ignored the junk would resolve every object to the wrong place — and
        # the junk itself is unaccounted bytes that may carry anything.
        raise ParseError(
            f"PDF: {m.start()} bytes before the %PDF header (offsets would be "
            "header-relative; refusing rather than guessing)")
    return m.start(), m.group(0), (int(m.group(1)), int(m.group(2)))


def _binary_comment(data: bytes, after: int) -> bytes | None:
    """The `%` + four >127 bytes a producer writes on line 2.

    Its presence is spec-recommended; the *specific bytes* are a per-producer tell
    (qpdf writes `%\\xbf\\xf7\\xa2\\xfe`, Skia `%\\xd3\\xeb\\xe9\\xe1`), which is why
    W0 rejected letting qpdf write our files and why our serializer emits none.
    """
    pos = after
    while pos < len(data) and data[pos] in b"\r\n":
        pos += 1
    if pos < len(data) and data[pos:pos + 1] == b"%":
        end = pos
        while end < len(data) and data[end] not in b"\r\n":
            end += 1
        comment = data[pos:end]
        if any(b > 127 for b in comment):
            return comment
    return None


def _revision_at(data: bytes, xref_offset: int, eof_end: int) -> Revision:
    """Classify one revision's cross-reference structure and read its links."""
    if xref_offset < 0 or xref_offset >= len(data):
        raise ParseError(f"PDF: startxref points outside the file ({xref_offset})")

    window_end = data.find(b"startxref", xref_offset)
    if window_end == -1:
        window_end = len(data)
    window = data[xref_offset:window_end]

    if window.lstrip()[:4] == b"xref":
        kind = "table"
        # The trailer dictionary follows the table; /Prev and /XRefStm live in it.
        search = window[window.find(b"trailer"):] if b"trailer" in window else b""
    elif _OBJ_DEF.search(b" " + window[:64]):
        # A cross-reference *stream*: an ordinary object whose dictionary carries the
        # xref data compressed. The dictionary itself is plaintext up to `stream`.
        kind = "stream"
        search = window[:window.find(b"stream")] if b"stream" in window else window
    else:
        raise ParseError(
            f"PDF: startxref {xref_offset} points at neither an xref table nor an "
            "xref stream")

    prev = _PREV.search(search)
    xrefstm = _XREFSTM.search(search)
    return Revision(xref_offset=xref_offset, xref_kind=kind, eof_end=eof_end,
                    prev=int(prev.group(1)) if prev else None,
                    xrefstm=int(xrefstm.group(1)) if xrefstm else None,
                    # Scoped to the trailer rather than the whole file: `/Encrypt`
                    # can appear inside a string or a stream, and refusing a file we
                    # could have scrubbed is a real cost of a sloppy fail-closed test.
                    encrypted=b"/Encrypt" in search)


def walk(data: bytes) -> Layout:
    header_offset, header, version = _find_header(data)
    comment = _binary_comment(data, header_offset + len(header))

    marks = list(_STARTXREF.finditer(data))
    if not marks:
        raise ParseError("PDF: no `startxref … %%EOF` — cannot locate any revision")

    revisions = [_revision_at(data, int(m.group(1)), m.end()) for m in marks]

    trailing = data[marks[-1].end():]
    if trailing.strip(b"\r\n \t"):
        # Anything past the last %%EOF is unaccounted for. Some tools append a
        # newline, which is harmless; actual content is not.
        raise ParseError(
            f"PDF: {len(trailing.strip())} unexplained bytes after the final %%EOF")

    objects = [ObjDef(int(m.group(1)), int(m.group(2)), m.start(1))
               for m in _OBJ_DEF.finditer(data)]

    return Layout(header=header, version=version, binary_comment=comment,
                  revisions=revisions, objects=objects, trailing=trailing,
                  linearized=b"/Linearized" in data[:2048],
                  object_streams=data.count(b"/ObjStm"))


def revision_count(data: bytes) -> int:
    """How many revisions the file carries. One means it was written, never edited.

    Keys on `startxref <n> %%EOF` rather than on `%%EOF` alone: a bare `%%EOF` shows
    up inside compressed streams by chance (measured, in MAT2 output), and counting
    those would report history in files that have none.
    """
    return len(_STARTXREF.findall(data))
