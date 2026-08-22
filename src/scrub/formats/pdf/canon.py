"""PDF content-stream canonicalisation — the F2 half that F1 cannot do.

M3 measured the leak precisely: after F1 the **serializer** channel is closed and the
**layout** channel still separates all five producers. This module attacks the part
of that channel W5 says is normalisable *without re-typesetting the page* — the way
the marks are written, not where they land:

  * **number formatting** — `72`, `72.0`, `72.00000` and `+72` are one value written
    four ways, and producers differ sharply in which they emit;
  * **operator choice among equivalents** — `Td` / `TD` / `T*` are all "move the line
    start", and every one of them can be written as the absolute `Tm` it amounts to;
  * **show-operator granularity** — `(A) Tj (B) Tj` and `[(A)(B)] TJ` paint the same
    glyphs, and "how often does the producer break the run" is pure style;
  * **string and name spelling** — `(A)` vs `<41>`, `/Name` vs `/N#61me`;
  * **whitespace and token layout**, which is a producer's signature in the same way
    indentation is.

What it deliberately does **not** touch is where each glyph actually sits: the
numbers inside the emitted `Tm`, the kerning numbers inside `TJ`, and which glyphs
were selected. Those are the typesetter's geometry, they cannot change without
re-flowing the page, and M3 already named `struct:glyph_digest` as the key that will
report them. An F2 that "passed" by rounding coordinates until producers collided
would be breaking the measurement rather than the leak.

**The text-matrix rewrite needs no font metrics**, which is what makes it safe.
`Td`/`TD`/`T*` are all relative to the *line* matrix `Tlm`, and showing text advances
only `Tm` — so tracking `Tlm` exactly requires no glyph widths. The one place widths
would be needed is a second show operation after the first has advanced `Tm`, and
that case is handled by *merging* the run into a single `TJ` instead of computing
where it got to. When something interrupts a run that we cannot merge across (a font
change mid-line), the run is flushed with no `Tm` prefix and the advance is left to
the reader, exactly as the input left it.

**Leading is graphics state, not text-object state.** `TL` survives `ET` and is saved
and restored by `q`/`Q` (ISO 32000 §9.3), so a `T*` in one text object can depend on a
`TL` set in an earlier one. Tracking it per `BT` block — the obvious first cut, and
the one written here first — silently moves every line that relies on that, which is
why `_State` carries it across text objects and pushes it on the `q` stack.

**Exactness.** Matrix composition is done in `Decimal`, not float, so `72.1` stays
`72.1` instead of acquiring a binary-rounding tail. Composition can still grow the
digit count without bound over a long page, so an emitted coordinate is quantised to
`1E-8` user-space units — 1.7e-7 of a pixel at 1200 DPI, far below anything that can
change a rendered dot, but recorded here and in `docs/limits.md` as a real (if
absurdly small) value change rather than described as bit-exact.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext

from ...errors import ParseError
from . import content as ct
from .serialize import _NAME_SAFE

# Enough precision that composition is exact well past the point where quantisation
# takes over; `localcontext` keeps it from leaking into any other module's arithmetic.
_PRECISION = 60
_QUANTUM = Decimal("1E-8")

SHOW_OPS = frozenset({b"Tj", b"TJ", b"'", b'"'})
# Operators whose whole effect is on the text line matrix or the leading. Every one is
# rewritten into the absolute `Tm` it produces, so none but `Tm` survives into the
# output — a claim `test_pdf.py` enforces rather than leaves as a comment.
POSITION_OPS = frozenset({b"Td", b"TD", b"Tm", b"T*", b"TL"})
REWRITTEN_AWAY = POSITION_OPS - {b"Tm"}

_IDENTITY = (Decimal(1), Decimal(0), Decimal(0), Decimal(1), Decimal(0), Decimal(0))


# --------------------------------------------------------------------------- #
# Token rendering
# --------------------------------------------------------------------------- #
def _decimal(raw: bytes) -> Decimal:
    """A PDF number token as an exact `Decimal`.

    PDF allows spellings `Decimal` rejects outright — a trailing point (`4.`), a
    leading `+`, and a bare `.5` — so they are normalised into its grammar first
    rather than caught as an exception and guessed at.
    """
    text = raw.decode("ascii", "replace").strip()
    if text.endswith("."):
        text += "0"
    if text.startswith("+"):
        text = text[1:]
    if text.startswith("."):
        text = "0" + text
    elif text.startswith("-."):
        text = "-0" + text[1:]
    try:
        return Decimal(text)
    except InvalidOperation:
        raise ParseError(f"PDF content: malformed number {raw!r}") from None


def fmt_number(value: Decimal) -> bytes:
    """One spelling per value: no exponent, no trailing zeros, no `-0`, no `+`."""
    if -value.as_tuple().exponent > 8:
        value = value.quantize(_QUANTUM)
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".") or "0"
    if text in ("-0", ""):
        text = "0"
    return text.encode("ascii")


def _string_value(raw: bytes) -> bytes:
    r"""The bytes a `(…)` or `<…>` token denotes, escapes resolved.

    Needed because canonicalising the *spelling* of a string means first knowing what
    it says: `(A\(B)`, `(\101\(B)` and `<41284 2>` are three spellings of `A(B`.
    """
    if raw.startswith(b"<"):
        digits = bytes(c for c in raw[1:-1] if c not in ct.WHITESPACE)
        if len(digits) % 2:
            digits += b"0"                       # ISO 32000 §7.3.4.3: pad with zero
        try:
            return bytes.fromhex(digits.decode("ascii"))
        except ValueError:
            raise ParseError(f"PDF content: malformed hex string {raw[:32]!r}") from None

    out = bytearray()
    i, body = 0, raw[1:-1]
    while i < len(body):
        c = body[i]
        if c != 0x5C:                            # backslash
            out.append(c)
            i += 1
            continue
        i += 1
        if i >= len(body):
            break
        e = body[i]
        if e in b"01234567":
            octal = b""
            while i < len(body) and len(octal) < 3 and body[i] in b"01234567":
                octal += body[i:i + 1]
                i += 1
            out.append(int(octal, 8) & 0xFF)
        elif e in b"\r\n":                       # line continuation: emits nothing
            i += 1
            if e == 0x0D and i < len(body) and body[i] == 0x0A:
                i += 1
        else:
            out.append({0x6E: 0x0A, 0x72: 0x0D, 0x74: 0x09,
                        0x62: 0x08, 0x66: 0x0C}.get(e, e))
            i += 1
    return bytes(out)


def _fmt_string(value: bytes) -> bytes:
    """Literal when it reads as plain ASCII, hex otherwise — the rule
    `serialize._string` already applies to object strings, so a document has one
    string spelling throughout rather than one per layer."""
    if all(32 <= b <= 126 for b in value):
        esc = (value.replace(b"\\", b"\\\\")
                    .replace(b"(", b"\\(").replace(b")", b"\\)"))
        return b"(" + esc + b")"
    return b"<" + value.hex().upper().encode("ascii") + b">"


def _fmt_name(raw: bytes) -> bytes:
    """`#XX` escapes resolved, then re-applied by our own rule, so `/A#42` and `/AB`
    converge on one spelling."""
    body = raw[1:]
    value = bytearray()
    i = 0
    while i < len(body):
        if body[i] == 0x23 and i + 3 <= len(body):        # '#'
            try:
                value.append(int(body[i + 1:i + 3], 16))
                i += 3
                continue
            except ValueError:
                pass
        value.append(body[i])
        i += 1
    out = bytearray(b"/")
    for b in value:
        out += bytes([b]) if b in _NAME_SAFE else b"#%02X" % b
    return bytes(out)


def fmt_token(tok: ct.Token) -> bytes:
    if tok.kind == "number":
        return fmt_number(_decimal(tok.raw))
    if tok.kind in ("string", "hexstring"):
        return _fmt_string(_string_value(tok.raw))
    if tok.kind == "name":
        return _fmt_name(tok.raw)
    return tok.raw


# --------------------------------------------------------------------------- #
# The replayed state, shared by the rewriter and its verifier
# --------------------------------------------------------------------------- #
def _translate(tx: Decimal, ty: Decimal, m: tuple) -> tuple:
    """`[1 0 0 1 tx ty] × m` — the only matrix product `Td`/`TD`/`T*` ever need."""
    a, b, c, d, e, f = m
    return (a, b, c, d, tx * a + ty * c + e, tx * b + ty * d + f)


class _State:
    """Text position plus the text-state parameters this module rewrites.

    `tlm` is live only inside a text object; `leading`, `word` and `char` spacing are
    graphics state and outlive it, which is why `q`/`Q` push and pop them here.

    `inherits` is the difference between a page's content stream and a Form XObject's.
    A page starts in the default graphics state, so its leading and spacing are known
    to be zero. A form, a tiling pattern and a Type3 glyph procedure are *invoked*,
    and start in whatever state the caller was in — so theirs begin as `None`, meaning
    "inherited and unknown". Assuming zero there is not a small error: it makes this
    module emit a `0 Tw` that overrides the caller's spacing, and resolve a `T*`
    against the wrong leading, silently re-rendering a form that the peer corpus does
    not happen to contain.
    """

    def __init__(self, inherits: bool = False) -> None:
        self.tlm: tuple | None = None
        start = None if inherits else Decimal(0)
        self.leading = start
        self.word = start
        self.char = start
        self._stack: list[tuple] = []

    # -- graphics state ----------------------------------------------------- #
    def push(self) -> None:
        self._stack.append((self.leading, self.word, self.char))

    def pop(self) -> None:
        if self._stack:
            self.leading, self.word, self.char = self._stack.pop()

    # -- text object -------------------------------------------------------- #
    def begin_text(self) -> None:
        self.tlm = _IDENTITY

    def end_text(self) -> None:
        self.tlm = None

    def td(self, tx: Decimal, ty: Decimal) -> None:
        self.tlm = _translate(tx, ty, self.tlm or _IDENTITY)

    def next_line(self) -> None:
        if self.leading is None:
            raise ParseError(
                "PDF content: T* / ' / \" with a leading inherited from the caller — "
                "the absolute position cannot be resolved, so the stream is left alone")
        self.td(Decimal(0), -self.leading)

    @property
    def marker(self) -> tuple:
        """Everything a show operation's appearance depends on that this module may
        rewrite. Comparing these across a rewrite is what proves the rewrite moved
        nothing — `painted()` cannot see position, and position is the whole risk."""
        return (tuple(fmt_number(v) for v in (self.tlm or _IDENTITY)),
                None if self.word is None else fmt_number(self.word),
                None if self.char is None else fmt_number(self.char))

    def apply(self, op: ct.Operation) -> bool:
        """Fold one operator into the state. True when the operator is one this
        module rewrites (and so must not be emitted verbatim)."""
        name = op.operator
        if name == b"q":
            self.push()
            return False
        if name == b"Q":
            self.pop()
            return False
        if name == b"BT":
            self.begin_text()
            return False
        if name == b"ET":
            self.end_text()
            return False

        numbers = [_decimal(t.raw) for t in op.operands if t.kind == "number"]
        if name == b"TL":
            _expect(numbers, 1, name)
            self.leading = numbers[0]
            return True
        if name == b"Tw":
            _expect(numbers, 1, name)
            self.word = numbers[0]
            return True
        if name == b"Tc":
            _expect(numbers, 1, name)
            self.char = numbers[0]
            return True
        if self.tlm is None:
            # A positioning operator outside BT … ET is malformed; it is passed
            # through untouched rather than interpreted against a matrix that the
            # reader does not have either.
            return False
        if name == b"T*":
            self.next_line()
            return True
        if name in (b"Td", b"TD"):
            _expect(numbers, 2, name)
            if name == b"TD":
                self.leading = -numbers[1]
            self.td(*numbers)
            return True
        if name == b"Tm":
            _expect(numbers, 6, name)
            self.tlm = tuple(numbers)
            return True
        return False


def _expect(numbers: list, count: int, name: bytes) -> None:
    if len(numbers) != count:
        raise ParseError(
            f"PDF content: {name.decode('latin-1')} needs {count} numeric operand(s), "
            f"got {len(numbers)}")


# --------------------------------------------------------------------------- #
# Canonicalisation
# --------------------------------------------------------------------------- #
class _Canonicalizer:
    def __init__(self, inherits: bool = False) -> None:
        self.out: list[bytes] = []
        self.run: list[bytes] = []          # pending TJ elements
        self.state = _State(inherits)
        self.pending_marker: tuple | None = None   # state when the run began
        # What the *reader* of our output currently has for word and char spacing.
        # Tracked separately from `state` so a spacing operator is written only when
        # it actually changes something: emitting `0 Tw 0 Tc` before every run made
        # every file we produce share a constant we had no need to introduce, which
        # is the mistake `flac/f1.py` records — normalising a constant that can
        # simply be omitted. The `q` stack is mirrored because `Q` restores spacing
        # for the reader exactly as it does for us.
        self.shown_word = None if inherits else fmt_number(Decimal(0))
        self.shown_char = None if inherits else fmt_number(Decimal(0))
        self._shown_stack: list[tuple] = []

    # -- emission ----------------------------------------------------------- #
    def _emit(self, *parts: bytes) -> None:
        self.out.append(b" ".join(p for p in parts if p))

    def _flush(self) -> None:
        """Write the accumulated show-run as one `TJ`, restoring the spacing and the
        absolute position it began at.

        An **empty** run must leave `pending_marker` alone. Clearing it here was the
        first version's bug: a `Tf` between the `Td` and the text it positions would
        drop the pending move, the rewritten `Td` would never be emitted as a `Tm`,
        and the line would silently render at the previous position.
        """
        if not self.run:
            return
        if self.pending_marker is not None:
            matrix, word, char = self.pending_marker
            if word is not None and word != self.shown_word:
                self._emit(word, b"Tw")
                self.shown_word = word
            if char is not None and char != self.shown_char:
                self._emit(char, b"Tc")
                self.shown_char = char
            self._emit(b" ".join(matrix), b"Tm")
        self._emit(b"[" + b"".join(self.run) + b"]", b"TJ")
        self.run = []
        self.pending_marker = None

    # -- show operators ------------------------------------------------------ #
    def _show_elements(self, op: ct.Operation) -> list[bytes]:
        """The `TJ`-array elements a show operator contributes.

        `Tj`, `'` and `"` each show one string, so each becomes a one-element array;
        a `TJ` contributes its array's contents unchanged, kerning numbers included —
        those are geometry and are deliberately carried through untouched.
        """
        if op.operator == b"TJ":
            inner = [t for t in op.operands
                     if t.kind not in ("array_open", "array_close")]
            if len(inner) != len(op.operands) - 2:
                raise ParseError("PDF content: TJ operand is not a single array")
            return [fmt_token(t) for t in inner]
        if not op.operands or op.operands[-1].kind not in ("string", "hexstring"):
            raise ParseError(
                f"PDF content: {op.operator.decode('latin-1')} without a string operand")
        return [fmt_token(op.operands[-1])]

    def _show(self, op: ct.Operation) -> None:
        if op.operator == b'"':
            if len(op.operands) < 3:
                raise ParseError('PDF content: " needs aw, ac and a string')
            self._flush()
            self.state.word = _decimal(op.operands[0].raw)
            self.state.char = _decimal(op.operands[1].raw)
        if op.operator in (b"'", b'"'):
            self._flush()
            self.state.next_line()
        if not self.run:
            self.pending_marker = self.state.marker
        self.run.extend(self._show_elements(op))

    # -- driver -------------------------------------------------------------- #
    def _operation(self, op: ct.Operation) -> None:
        name = op.operator

        if name in SHOW_OPS and self.state.tlm is not None:
            self._show(op)
            return

        # Anything that is not a show has to land after the pending run, so the run
        # is closed first and the operator emitted (or absorbed) afterwards.
        self._flush()
        if name == b"q":
            self._shown_stack.append((self.shown_word, self.shown_char))
        elif name == b"Q" and self._shown_stack:
            self.shown_word, self.shown_char = self._shown_stack.pop()
        rewritten = self.state.apply(op)
        if not rewritten:
            self._emit(*[fmt_token(t) for t in op.operands], name)

    def _inline_image(self, img: ct.InlineImage) -> None:
        """Re-emitted with a sorted, canonically-spelled dictionary and a recomputed
        `/L`. The payload is copied byte for byte: F1 has already scrubbed it, and
        touching it here would be an F3 re-encode wearing an F2 label."""
        self._flush()
        parts = [b"BI"]
        for key in sorted(img.params):
            if key in ("/L", "/Length"):
                continue
            tok = ct.next_token(img.params[key], 0)
            parts.append(_fmt_name(key.encode("latin-1")))
            parts.append(fmt_token(tok) if tok else img.params[key])
        parts += [b"/L", b"%d" % len(img.data), b"ID"]
        self.out.append(b" ".join(parts) + b" " + img.data + b"\nEI")

    def run_stream(self, data: bytes) -> bytes:
        for item in ct.walk_ops(data):
            if isinstance(item, ct.InlineImage):
                self._inline_image(item)
            else:
                self._operation(item)
        self._flush()
        if self.state.tlm is not None:
            raise ParseError("PDF content: BT without ET")
        return b"\n".join(self.out) + (b"\n" if self.out else b"")


def canonicalize(data: bytes, inherits: bool = False) -> bytes:
    """One canonical spelling of the marks `data` paints.

    `inherits=True` for a stream that is invoked rather than laid on a page — a Form
    XObject, a tiling pattern, a Type3 glyph procedure — whose initial text state
    comes from its caller. See `_State`.
    """
    with localcontext() as ctx:
        ctx.prec = _PRECISION
        return _Canonicalizer(inherits).run_stream(data)


# --------------------------------------------------------------------------- #
# The invariant a rewrite must hold
# --------------------------------------------------------------------------- #
def painted(data: bytes, inherits: bool = False) -> tuple:
    """What the stream paints, in a form the rewrite must not change.

    Two halves, because neither alone is sufficient:

    * **the ink** — the glyph bytes shown in order, plus every non-rewritten operator
      with its operands, so a lost `re`/`f` or a reordered `Do` is caught;
    * **the geometry** — the absolute line matrix and the spacing in force at each
      show, replayed through the same `_State` the rewriter uses, so a mis-tracked
      `Tlm` that still paints every glyph in the right order is caught too. That is
      the failure mode `Td` → `Tm` actually risks, and the ink check is blind to it.

    Blind on purpose to everything the module is allowed to rewrite: which operator
    expressed a move, how a number was spelled, where a show run was broken.
    """
    shown = bytearray()
    ops: list[tuple] = []
    positions: list[tuple] = []
    state = _State(inherits)
    # One position per *run* of shows, not per show — because merging `(A) Tj (B) Tj`
    # into `[(A)(B)] TJ` is a rewrite this function must tolerate, and a per-show list
    # would differ in length between the two sides for that reason alone. Showing text
    # does not move `Tlm`, so every show in an uninterrupted run sits at one position
    # and recording it once loses nothing.
    run_open = False
    with localcontext() as ctx:
        ctx.prec = _PRECISION
        for item in ct.walk_ops(data):
            if isinstance(item, ct.InlineImage):
                # Identified by its dictionary, not its payload length: F1 strips the
                # EXIF out of a `/DCTDecode` inline image, so a length here would make
                # this invariant fail on the scrub it is meant to be checking. The
                # payload itself is already covered — `f1._jpeg_scan` asserts the
                # entropy-coded data survives byte for byte.
                ops.append(("BI", tuple(sorted(
                    (k, v) for k, v in item.params.items()
                    if k not in ("/L", "/Length")))))
                run_open = False
                continue
            name = item.operator
            if name in SHOW_OPS and state.tlm is not None:
                if name == b'"' and len(item.operands) >= 3:
                    state.word = _decimal(item.operands[0].raw)
                    state.char = _decimal(item.operands[1].raw)
                if name in (b"'", b'"'):
                    state.next_line()
                    run_open = False          # the advance breaks the run
                if not run_open:
                    positions.append(state.marker)
                    run_open = True
                for tok in item.operands:
                    if tok.kind in ("string", "hexstring"):
                        shown += _string_value(tok.raw)
                continue
            run_open = False
            if not state.apply(item):
                ops.append((name.decode("latin-1"),
                            tuple(fmt_token(t) for t in item.operands)))
    return bytes(shown), tuple(ops), tuple(positions)
