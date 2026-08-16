"""PDF serializer — we emit the bytes (the W0 decision).

pikepdf/qpdf stays as the object-graph *reader* and semantic layer; byte layout is
ours. W0 measured why: the project's own fingerprint guard fails qpdf's output with a
103-byte signature spanning its constant binary header comment and its catalog-first
object order, and `/ID[0]` is inherited through a qpdf rewrite whatever the trailer
says. Neither is patchable from outside the writer. This is also the doctrinally
consistent answer — `standards/isobmff.py` has `parse()` *and* `serialize()`, and
`flac/f1.py` writes block headers by hand — so PDF does not become the one format
where byte layout is outsourced and then argued past our own guard.

Every producer-identifying choice is settled here, in one place:

  * **no binary header comment.** Producers write `%` plus four >127 bytes on line 2,
    and the specific bytes are a per-producer tell: qpdf `%\\xbf\\xf7\\xa2\\xfe`, Skia
    `%\\xd3\\xeb\\xe9\\xe1`, cairo `%\\xb5\\xed\\xae\\xfb`, LibreOffice `%\\xc3\\xa4…`,
    macOS Quartz a twelve-byte one (all measured). Substituting our own constant just
    makes it ours — the mistake `flac/f1.py:43-47` records — so we emit none. ISO
    32000 §7.5.2 says a producer *should*, not *shall*.
  * **no `/ID`.** W0: qpdf inherits `/ID[0]` from the input, so a rewritten file still
    carries the identifier linking it to every other revision of the same document;
    and `static_id=True` writes qpdf's hardcoded pi constant, which identifies the
    library. Neither is fixable through the API. Omitting the whole array is legal
    (`/ID` is required only for encrypted files, which we refuse).
  * **pinned version header**, since a rewrite otherwise inherits the input's.
  * **sorted dictionary keys, generation 0 throughout, breadth-first numbering,
    direct `/Length`, one classic xref table** — every one of which is a producer
    choice in the wild and a constant here.

Streams are written with their *raw* (still-encoded) bytes, so nothing is recompressed
and F1 stays bit-preserving in content. Compression choices therefore pass through
from the input at F1; normalising them is F2's job, not this module's.
"""
from __future__ import annotations

import re
from decimal import Decimal

import pikepdf

from ...errors import ParseError

_REF = re.compile(rb"(\d+) 0 R")

# Pinned so the header stops being an input-derived tell. 1.7 is ISO 32000-1, which
# every reader in circulation implements; we never emit a construct newer than that.
VERSION = b"%PDF-1.7"

# Names must escape anything outside the "regular character" set (ISO 32000 §7.3.5).
_NAME_SAFE = frozenset(
    b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    b"*+-.@_$&^!\"'|~,;=:?<>[]{}")

_MAX_DEPTH = 256


def _name(obj) -> bytes:
    raw = str(obj).encode("latin-1", "replace")
    if not raw.startswith(b"/"):
        raise ParseError(f"PDF: malformed name object {raw!r}")
    out = bytearray(b"/")
    for b in raw[1:]:
        out += bytes([b]) if b in _NAME_SAFE else b"#%02X" % b
    return bytes(out)


def _string(raw: bytes) -> bytes:
    r"""Literal `(…)` when the content is plain printable ASCII, hex `<…>` otherwise.

    A pure function of the content, so it stays deterministic; and it keeps text
    readable instead of hex-bloating every `/ToUnicode` map, which emitting hex
    unconditionally would do.
    """
    if all(32 <= b <= 126 for b in raw):
        esc = raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
        return b"(" + esc + b")"
    return b"<" + raw.hex().upper().encode("ascii") + b">"


def _number(value) -> bytes:
    if isinstance(value, bool):                    # before int: bool IS an int
        return b"true" if value else b"false"
    if isinstance(value, int):
        return b"%d" % value
    d = Decimal(str(value)) if not isinstance(value, Decimal) else value
    # PDF has no exponent notation; 'f' formatting is what keeps 1E+2 from shipping.
    text = format(d, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".") or "0"
    return text.encode("ascii")


class _Writer:
    def __init__(self) -> None:
        self.numbers: dict[tuple[int, int], int] = {}   # source objgen -> our number
        self.order: list = []                           # objects in output order

    def assign(self, obj) -> int:
        """Breadth-first numbering. Deterministic given the same graph, and it puts
        the catalog and pages first, which is also what makes the output readable."""
        queue = [obj]
        while queue:
            current = queue.pop(0)
            og = current.objgen
            if og in self.numbers:
                continue
            self.numbers[og] = len(self.order) + 1
            self.order.append(current)
            queue.extend(self._children(current))
        return self.numbers[obj.objgen]

    def _children(self, obj, depth: int = 0) -> list:
        """Indirect objects reachable from `obj` without passing through another
        indirect object. Direct containers are walked inline; sorted keys keep the
        traversal — and therefore the numbering — independent of dictionary order."""
        if depth > _MAX_DEPTH:
            raise ParseError("PDF: object nesting deeper than 256; refusing")
        out = []
        if isinstance(obj, pikepdf.Dictionary | pikepdf.Stream):
            # /Length is skipped deliberately: we always rewrite it as a direct
            # integer, so following an indirect one (cairo writes them) would number
            # and emit an object that nothing in the output then references.
            values = [obj[k] for k in sorted(obj.keys())
                      if not (k == "/Length" and isinstance(obj, pikepdf.Stream))]
        elif isinstance(obj, pikepdf.Array):
            values = list(obj)
        else:
            return out
        for v in values:
            if isinstance(v, pikepdf.Object) and v.is_indirect:
                out.append(v)
            else:
                out.extend(self._children(v, depth + 1))
        return out

    def value(self, obj, depth: int = 0) -> bytes:
        """A value *in place*: an indirect object renders as a reference, not inline.

        The split from `contents()` below is the whole reason a first cut of this
        module emitted `1 0 obj\\n1 0 R\\nendobj` — at its own definition site an
        object must render its contents, everywhere else its reference.
        """
        if depth > _MAX_DEPTH:
            raise ParseError("PDF: object nesting deeper than 256; refusing")
        if isinstance(obj, pikepdf.Object) and obj.is_indirect:
            num = self.numbers.get(obj.objgen)
            if num is None:
                raise ParseError(f"PDF: reference to unnumbered object {obj.objgen}")
            return b"%d 0 R" % num
        return self.contents(obj, depth)

    def contents(self, obj, depth: int = 0) -> bytes:
        """The object's own bytes, ignoring whether it is indirect."""
        if depth > _MAX_DEPTH:
            raise ParseError("PDF: object nesting deeper than 256; refusing")
        if obj is None:
            return b"null"
        if isinstance(obj, bool | int | float | Decimal):
            return _number(obj)
        if not isinstance(obj, pikepdf.Object):
            raise ParseError(f"PDF: cannot serialize {type(obj).__name__}")

        code = obj._type_code
        T = pikepdf.ObjectType
        if code == T.null:
            return b"null"
        if code == T.boolean:
            return b"true" if bool(obj) else b"false"
        if code in (T.integer, T.real):
            return _number(obj.as_int() if code == T.integer else obj.as_decimal())
        if code == T.name_:
            return _name(obj)
        if code == T.string:
            return _string(bytes(obj))
        if code == T.array:
            return b"[" + b" ".join(self.value(v, depth + 1) for v in obj) + b"]"
        if code in (T.dictionary, T.stream):
            return self.dictionary(obj, depth)
        raise ParseError(f"PDF: unsupported object type {code}")

    def dictionary(self, obj, depth: int = 0, extra: dict | None = None) -> bytes:
        parts = [b"<<"]
        items = [(k, obj[k]) for k in sorted(obj.keys())]
        if extra:
            items = [(k, v) for k, v in items if k not in extra]
            items += sorted(extra.items())
        for key, val in items:
            rendered = val if isinstance(val, bytes) else self.value(val, depth + 1)
            parts.append(b" " + _name(pikepdf.Name(key)) + b" " + rendered)
        parts.append(b" >>")
        return b"".join(parts)

    def body(self, obj) -> bytes:
        if isinstance(obj, pikepdf.Stream):
            raw = obj.read_raw_bytes()
            # /Length direct and recomputed: an indirect /Length is a producer tell
            # and an extra object, and a stale one is a corrupt file.
            head = self.dictionary(obj, extra={"/Length": b"%d" % len(raw)})
            return head + b"\nstream\n" + raw + b"\nendstream"
        return self.contents(obj)


def serialize(pdf: pikepdf.Pdf) -> bytes:
    """Write `pdf` as a single-revision, canonically-laid-out PDF.

    Only what is reachable from `/Root` is written. That is the mechanism that kills
    incremental-update history (E-PDF-HISTORY): superseded objects are simply never
    reached, so they are never emitted — no deletion pass, nothing to get wrong.
    Everything the caller wants kept must hang off the catalog.
    """
    root = pdf.Root
    if not isinstance(root, pikepdf.Object) or not root.is_indirect:
        raise ParseError("PDF: /Root is not an indirect object")

    w = _Writer()
    w.assign(root)

    out = bytearray(VERSION + b"\n")            # no binary comment — see module docs
    offsets: dict[int, int] = {}
    referenced: set[int] = {w.numbers[root.objgen]}
    for obj in w.order:
        num = w.numbers[obj.objgen]
        offsets[num] = len(out)
        rendered = w.body(obj)
        referenced.update(int(m) for m in _REF.findall(rendered))
        out += b"%d 0 obj\n" % num + rendered + b"\nendobj\n"

    # The ledger, applied to our own output: an object we emit that nothing points
    # at is exactly the residue this phase exists to remove, and shipping one would
    # mean the walker's own orphan check finds our fingerprint rather than an input's.
    orphans = sorted(set(offsets) - referenced)
    if orphans:
        raise ParseError(f"PDF: serializer emitted unreferenced object(s) {orphans}")

    xref_offset = len(out)
    count = len(w.order) + 1
    out += b"xref\n0 %d\n" % count
    out += b"0000000000 65535 f \n"
    for num in range(1, count):
        out += b"%010d 00000 n \n" % offsets[num]

    # No /ID, no /Prev, no /Info: the first two are producer identity (W0), and the
    # third is the metadata dictionary the scrub exists to remove.
    out += (b"trailer\n<< /Root %d 0 R /Size %d >>\nstartxref\n%d\n%%%%EOF\n"
            % (w.numbers[root.objgen], count, xref_offset))
    return bytes(out)
