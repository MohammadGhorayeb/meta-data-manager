"""Redaction-risk detector — warns, never fixes.

**Redaction is not metadata scrubbing, and this project does not do it.** A black
rectangle drawn over a name is a drawing operation; the name is still in the content
stream underneath, and `pdftotext` reads it out in full. Every tier here preserves
content by construction, so F1, F2 and F3 all preserve hidden text exactly as
faithfully as visible text — F3 included, because rasterising removes the *text layer*
but the words under the box were painted into the pixels only if they were visible in
the first place. A user who believes "scrubbed" means "redacted" is the failure mode
this module exists to prevent, and the honest answer is a warning rather than a
silent repair we cannot do correctly.

**Three checks, deliberately narrow.** Each is cheap and unambiguous:

1. **Invisible text** — rendering mode 3 (`Tr`) draws nothing, and mode 7 adds to the
   clip path without painting. Both are how a scanner's OCR layer is stored, and both
   are how text hidden behind an image is stored. Legitimate and dangerous look
   identical here, so it is reported, not judged.
2. **Text outside the crop box** — a reader shows only the `/CropBox`, so text placed
   outside it is invisible on screen and fully extractable.
3. **Text drawn before an opaque fill that covers it**, in the same content stream.
   This is the classic black-box redaction.

**What it deliberately does not attempt.** "Text under an opaque fill" in full
generality needs graphics state, blend modes, transparency groups, soft masks and
z-order — a small renderer, which would eat the phase. So check 3 is restricted to a
non-rotated fill in the same stream with no transparency in force, and everything
outside that is *not reported*. Under-reporting is the honest failure direction: a
warning that fires on everything gets ignored, and this one must not.
"""
from __future__ import annotations

import io
from decimal import Decimal

import pikepdf

from . import canon, f2
from . import content as ct

# Text rendering modes that paint no glyphs (ISO 32000 §9.3.6).
_INVISIBLE_MODES = frozenset({3, 7})


class Risk:
    """One finding. Plain wording: the audience is a user, not a format engineer."""

    __slots__ = ("kind", "page", "detail")

    def __init__(self, kind: str, page: int, detail: str) -> None:
        self.kind, self.page, self.detail = kind, page, detail

    def __repr__(self) -> str:
        return f"Risk({self.kind!r}, page={self.page}, {self.detail!r})"

    def __eq__(self, other) -> bool:
        return (isinstance(other, Risk) and self.kind == other.kind
                and self.page == other.page and self.detail == other.detail)

    def message(self) -> str:
        return f"page {self.page + 1}: {self.detail}"


def _numbers(op: ct.Operation) -> list[Decimal]:
    return [canon._decimal(t.raw) for t in op.operands if t.kind == "number"]


def _shown_text(op: ct.Operation) -> bytes:
    out = bytearray()
    for tok in op.operands:
        if tok.kind in ("string", "hexstring"):
            out += canon._string_value(tok.raw)
    return bytes(out)


def _rect_from_re(numbers: list[Decimal], matrix: tuple) -> tuple | None:
    """A `re` rectangle in user space, if the current matrix is axis-aligned.

    A rotated or skewed matrix would need a real polygon intersection, and check 3 is
    scoped to the case a black-box redaction actually uses. Anything else returns None
    and is simply not reported.
    """
    if len(numbers) != 4:
        return None
    a, b, c, d, e, f = matrix
    if b or c:                                   # rotation or skew present
        return None
    x, y, w, h = numbers
    x0, y0 = a * x + e, d * y + f
    x1, y1 = a * (x + w) + e, d * (y + h) + f
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def _covers(box: tuple, point: tuple) -> bool:
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def _page_risks(page_index: int, body: bytes, crop: tuple | None) -> list[Risk]:
    risks: list[Risk] = []
    state = canon._State()
    mode = 0
    ctm = (Decimal(1), Decimal(0), Decimal(0), Decimal(1), Decimal(0), Decimal(0))
    ctm_stack: list[tuple] = []
    alpha_stack: list[bool] = []
    transparent = False
    # Rectangles of the path being built, waiting to find out whether it is filled.
    pending: list[tuple] = []
    # Position and text of every visible show, so a fill later in the stream can be
    # tested against text drawn earlier — that ordering is what makes a box a redaction.
    shows: list[tuple[tuple, bytes]] = []
    invisible = bytearray()
    outside = bytearray()

    for item in ct.walk_ops(body):
        if isinstance(item, ct.InlineImage):
            continue
        name = item.operator
        numbers = _numbers(item)

        if name == b"q":
            ctm_stack.append(ctm)
            alpha_stack.append(transparent)
        elif name == b"Q":
            if ctm_stack:
                ctm = ctm_stack.pop()
            if alpha_stack:
                transparent = alpha_stack.pop()
        elif name == b"cm" and len(numbers) == 6:
            a, b, c, d, e, f = numbers
            A, B, C, D, E, F = ctm
            ctm = (a * A + b * C, a * B + b * D,
                   c * A + d * C, c * B + d * D,
                   e * A + f * C + E, e * B + f * D + F)
        elif name == b"gs":
            # A named graphics state may set /CA or /ca. Resolving the resource to
            # find out is more machinery than this advisory is scoped for, so we
            # assume it might and stop claiming any later fill is opaque — failing
            # toward silence, which is the chosen direction for a warning.
            transparent = True
        elif name == b"Tr" and numbers:
            mode = int(numbers[0])
        elif name == b"re":
            rect = _rect_from_re(numbers, ctm)
            if rect is not None:
                pending.append(rect)
        elif name in (b"f", b"F", b"f*", b"B", b"B*"):
            if not transparent:
                for box in pending:
                    hidden = [t for pos, t in shows if _covers(box, pos)]
                    if hidden:
                        risks.append(Risk(
                            "text_under_fill", page_index,
                            "text is drawn underneath a filled rectangle that covers "
                            "it — if this is a redaction, the words are still in the "
                            "file and fully readable: "
                            + _preview(b" ".join(hidden))))
            pending = []
        elif name in (b"n", b"S", b"s", b"W", b"W*"):
            pending = []
        elif name in canon.SHOW_OPS and state.tlm is not None:
            text = _shown_text(item)
            position = (state.tlm[4], state.tlm[5])
            if text.strip():
                if mode in _INVISIBLE_MODES:
                    invisible += text + b" "
                elif crop is not None and not _covers(crop, position):
                    outside += text + b" "
                else:
                    shows.append((position, text))

        state.apply(item)

    if invisible.strip():
        risks.append(Risk(
            "invisible_text", page_index,
            "text is present but painted invisibly (rendering mode 3 or 7). That is "
            "how a scanner stores its OCR layer and also how text is hidden behind an "
            "image — it is fully extractable either way: "
            + _preview(bytes(invisible))))
    if outside.strip():
        risks.append(Risk(
            "text_outside_crop", page_index,
            "text sits outside the visible crop area, so a reader never shows it "
            "while any extraction tool reads it: " + _preview(bytes(outside))))
    return risks


def _preview(text: bytes, limit: int = 60) -> str:
    shown = text.decode("latin-1", "replace").strip()
    shown = " ".join(shown.split())
    return repr(shown[:limit] + ("…" if len(shown) > limit else ""))


def detect(data: bytes) -> list[Risk]:
    """Every redaction risk we can identify cheaply and without ambiguity.

    An empty list means "none of these three patterns", **not** "this document is
    safely redacted" — see the module docstring for what is deliberately out of scope.
    """
    risks: list[Risk] = []
    with pikepdf.open(io.BytesIO(data)) as pdf:
        f2._merge_page_contents(pdf)
        for i, page in enumerate(pdf.pages):
            contents = page.obj.get("/Contents")
            if not isinstance(contents, pikepdf.Stream):
                continue
            box = page.obj.get("/CropBox") or page.obj.get("/MediaBox")
            crop = None
            if box is not None and len(box) == 4:
                values = [Decimal(str(float(v))) for v in box]
                crop = (min(values[0], values[2]), min(values[1], values[3]),
                        max(values[0], values[2]), max(values[1], values[3]))
            try:
                risks.extend(_page_risks(i, contents.read_bytes(), crop))
            except Exception:
                # A stream we cannot parse is not a finding; the scrub tiers refuse
                # such a file on their own terms and this advisory stays silent.
                continue
    return risks


def warnings(data: bytes) -> list[str]:
    """`detect()` as plain sentences, for the CLI to print."""
    return [r.message() for r in detect(data)]
