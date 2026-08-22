"""PDF content-stream tokenizer.

A page's marks are a little postfix program — `BT /F1 12 Tf 72 720 Td (Hi) Tj ET` —
stored as an ordinary stream. Object-graph tools never look inside it, and that is
where **inline images** live:

    BI /W 16 /H 16 /CS /RGB /F /DCTDecode ID <binary…> EI

An inline image is image data with no object number, no `/Type /XObject`, and no
entry in any resource dictionary. pikepdf will not find it, `pdfimages` will not
extract it, and if its filter is `/DCTDecode` the bytes after `ID` are a complete
JPEG — **with whatever APP1/EXIF segment its author left in it**. So a PDF scrub
that only walks objects can leave a GPS-tagged photograph in the file.

The tokenizer is needed three times over (W1): here for F1's inline images, for F2's
content-stream canonicalisation, and for W7's redaction detector, which has to see
text-rendering-mode and rectangle operators.

The `ID … EI` delimiter is a genuine wart in the format: the binary payload can
contain the bytes `EI` by chance. `/L` (or `/Length`) gives the length outright when
present, which is the only exact answer; without it, the scan requires `EI` to be
surrounded by whitespace or delimiters, and anything ambiguous fails closed rather
than guessing a boundary and corrupting the page.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ...errors import ParseError

WHITESPACE = b"\x00\t\n\x0c\r "
DELIMITERS = b"()<>[]{}/%"


@dataclass
class Token:
    kind: str          # "number" | "name" | "string" | "hexstring" | "array_open" …
    raw: bytes         # the exact source bytes
    start: int
    end: int


@dataclass
class InlineImage:
    """One `BI … ID … EI` run, with byte offsets into the containing stream."""
    start: int                 # offset of `BI`
    end: int                   # offset just past `EI`
    params: dict[str, bytes]   # the inline dictionary, name -> raw token bytes
    data_start: int
    data: bytes = b""

    @property
    def filters(self) -> list[str]:
        raw = self.params.get("/F") or self.params.get("/Filter") or b""
        return [t.decode("latin-1") for t in raw.replace(b"[", b" ").replace(b"]", b" ").split()]

    @property
    def is_jpeg(self) -> bool:
        """DCT-filtered inline data is a complete JPEG, so it can carry EXIF, an
        embedded thumbnail, and everything else `jpeg/f1.py` exists to remove."""
        return any(f in ("/DCTDecode", "/DCT") for f in self.filters)


def _skip_ws(data: bytes, pos: int) -> int:
    while pos < len(data):
        c = data[pos:pos + 1]
        if c in WHITESPACE:
            pos += 1
        elif c == b"%":                                  # comment to end of line
            while pos < len(data) and data[pos] not in b"\r\n":
                pos += 1
        else:
            break
    return pos


def _literal_string(data: bytes, pos: int) -> int:
    """End offset of a `(…)` string. Parens nest; a backslash escapes the next byte."""
    depth, pos = 0, pos
    while pos < len(data):
        c = data[pos]
        if c == 0x5C:                                    # backslash
            pos += 2
            continue
        if c == 0x28:                                    # (
            depth += 1
        elif c == 0x29:                                  # )
            depth -= 1
            if depth == 0:
                return pos + 1
        pos += 1
    raise ParseError("PDF content: unterminated ( string")


def next_token(data: bytes, pos: int) -> Token | None:
    """The single token at or after `pos`, or None at end of input.

    Reading one token at a time is not a stylistic preference: an inline image's
    dictionary is followed immediately by raw binary, so anything that tokenizes a
    *window* to get the next token walks into that binary and fails. The dictionary
    reader below advances token by token and stops at `ID`.
    """
    while True:
        pos = _skip_ws(data, pos)
        if pos >= len(data):
            return None
        start = pos
        c = data[pos:pos + 1]

        if c == b"(":
            pos = _literal_string(data, pos)
            return Token("string", data[start:pos], start, pos)
        elif c == b"<":
            if data[pos:pos + 2] == b"<<":
                pos += 2
                return Token("dict_open", b"<<", start, pos)
            else:
                end = data.find(b">", pos)
                if end == -1:
                    raise ParseError("PDF content: unterminated < hex string")
                pos = end + 1
                return Token("hexstring", data[start:pos], start, pos)
        elif data[pos:pos + 2] == b">>":
            pos += 2
            return Token("dict_close", b">>", start, pos)
        elif c in b"[]":
            pos += 1
            return Token("array_open" if c == b"[" else "array_close",
                             c, start, pos)
        elif c == b"/":
            pos += 1
            while pos < len(data) and data[pos:pos + 1] not in WHITESPACE + DELIMITERS:
                pos += 1
            return Token("name", data[start:pos], start, pos)
        elif c in b"+-." or c.isdigit():
            pos += 1
            while pos < len(data) and data[pos:pos + 1] in b"0123456789.+-eE":
                pos += 1
            return Token("number", data[start:pos], start, pos)
        elif c in b"{}":                                 # PostScript function braces
            pos += 1
            return Token("brace", c, start, pos)
        else:
            pos += 1
            while pos < len(data) and data[pos:pos + 1] not in WHITESPACE + DELIMITERS:
                pos += 1
            word = data[start:pos]
            if not word:
                raise ParseError(f"PDF content: stuck at offset {start}")
            return Token("operator", word, start, pos)


def tokenize(data: bytes) -> list[Token]:
    """Flat token list. Operators keep their own kind so a caller can pair them with
    the operands that precede them (PDF is postfix)."""
    out: list[Token] = []
    pos = 0
    while True:
        tok = next_token(data, pos)
        if tok is None:
            return out
        out.append(tok)
        pos = tok.end


def _jpeg_eoi(data: bytes, pos: int) -> int | None:
    """Offset just past a DCT inline image's `EOI`, or None if it will not parse.

    Not the payload *length* — a JPEG may carry trailing bytes after `EOI`, and
    inside an inline image those are indistinguishable from the content stream that
    follows it (the format gives no way to tell). But it is an exact floor, and using
    it as the point to start scanning for `EI` removes the failure that matters: a
    whitespace-bounded `EI` occurring inside compressed image data. That happened on
    the first torture file built for this module, and terminating there truncates the
    image *and* leaves the rest of the page mis-parsed.
    """
    from ..jpeg import segments as jseg

    try:
        structure = jseg.walk(data[pos:])
    except Exception:
        return None
    return pos + structure.trailer_offset if structure.segments else None


def _inline_data_end(data: bytes, pos: int, declared: int | None,
                     is_jpeg: bool = False) -> tuple[int, int]:
    """(data end, `EI` end) for an inline image whose payload starts at `pos`."""
    if declared is not None:
        end = pos + declared
        after = _skip_ws(data, end)
        if data[after:after + 2] != b"EI":
            raise ParseError("PDF content: inline image length does not reach EI")
        return end, after + 2

    scan = pos
    if is_jpeg:
        scan = _jpeg_eoi(data, pos) or pos
    while True:
        idx = data.find(b"EI", scan)
        if idx == -1:
            raise ParseError("PDF content: inline image has no EI terminator")
        before_ok = idx > pos and data[idx - 1:idx] in WHITESPACE
        after = data[idx + 2:idx + 3]
        after_ok = after == b"" or after in WHITESPACE + DELIMITERS
        if before_ok and after_ok and _tokenizes(data[idx + 2:idx + 2 + 96]):
            return idx - 1, idx + 2
        scan = idx + 2


def _tokenizes(tail: bytes) -> bool:
    """Would the bytes after a candidate `EI` read as content-stream syntax?

    A weak check by construction — binary can tokenize by luck — but it removes the
    common false positive, where the bytes after a mid-image `EI` are raw compressed
    data that hits an unterminated string almost immediately.
    """
    try:
        tokenize(tail)
        return True
    except ParseError:
        return False


def inline_images(data: bytes) -> list[InlineImage]:
    """Every `BI … ID … EI` in a content stream, in file order."""
    out: list[InlineImage] = []
    pos = 0
    while True:
        bi = data.find(b"BI", pos)
        if bi == -1:
            return out
        # `BI` must stand alone as an operator; `/BitsPerComponent` and the like
        # contain it, and so does any text inside a string.
        before = data[bi - 1:bi]
        after = data[bi + 2:bi + 3]
        if (bi != 0 and before not in WHITESPACE) or (after and after not in WHITESPACE + DELIMITERS):
            pos = bi + 2
            continue

        params: dict[str, bytes] = {}
        key: bytes | None = None
        cursor = bi + 2
        while True:
            cursor = _skip_ws(data, cursor)
            if data[cursor:cursor + 2] == b"ID":
                cursor += 2
                break
            tok = next_token(data, cursor)
            if tok is None:
                raise ParseError("PDF content: inline image dictionary never ends")
            if key is None:
                if tok.kind != "name":
                    raise ParseError(
                        f"PDF content: inline image key is {tok.kind}, not a name")
                key = tok.raw
            else:
                params[key.decode("latin-1")] = tok.raw
                key = None
            cursor = tok.end

        data_start = cursor + 1                # exactly one whitespace byte after ID
        declared = params.get("/L") or params.get("/Length")
        image = InlineImage(start=bi, end=0, params=params, data_start=data_start)
        end, ei_end = _inline_data_end(data, data_start,
                                       int(declared) if declared else None,
                                       is_jpeg=image.is_jpeg)
        image.end, image.data = ei_end, data[data_start:end]
        out.append(image)
        pos = ei_end


def replace_inline_images(data: bytes, rewrite) -> bytes:
    """Rebuild a content stream with `rewrite(image) -> bytes` applied to each payload.

    The dictionary is re-emitted verbatim except for `/L`, which is recomputed when
    it was present — a stale length is a corrupt page, and it is the exact analogue
    of `m4a/f1.py` patching `stco` after boxes move.
    """
    images = inline_images(data)
    if not images:
        return data
    out = bytearray()
    cursor = 0
    for img in images:
        new = rewrite(img)
        out += data[cursor:img.start]
        head = data[img.start:img.data_start]
        if "/L" in img.params or "/Length" in img.params:
            key = b"/L" if "/L" in img.params else b"/Length"
            old = img.params.get("/L") or img.params["/Length"]
            head = head.replace(key + b" " + old, key + b" %d" % len(new), 1)
        out += head + new + b"\nEI"
        cursor = img.end
    out += data[cursor:]
    return bytes(out)


@dataclass
class Operation:
    """One operator with the operands that preceded it."""
    operator: bytes
    operands: list[Token] = field(default_factory=list)


def operations(data: bytes) -> list[Operation]:
    """Group the token stream into operator-with-operands. W7's detector reads text
    rendering mode (`Tr`) and filled rectangles (`re`/`f`) off this."""
    ops: list[Operation] = []
    pending: list[Token] = []
    for tok in tokenize(data):
        if tok.kind == "operator":
            if tok.raw in (b"BI", b"ID", b"EI"):
                pending = []                   # inline images have their own reader
                continue
            ops.append(Operation(tok.raw, pending))
            pending = []
        else:
            pending.append(tok)
    return ops


def walk_ops(data: bytes):
    """Operations **and** inline images, in file order.

    `operations()` above drops `BI … ID … EI` on the floor, which is right for the
    harness plugin and for W7 — both only read operators. F2's canonicaliser rewrites
    the stream and therefore has to put every inline image back exactly where it was,
    so it needs the two interleaved. Kept separate rather than adding a flag to
    `operations()`, because the plugin's feature vector must not shift under it.
    """
    images = {img.start: img for img in inline_images(data)}
    out: list[Operation | InlineImage] = []
    pending: list[Token] = []
    pos = 0
    while True:
        pos = _skip_ws(data, pos)
        img = images.get(pos)
        if img is not None:
            # `BI` takes no operands; anything pending belongs to no operator and is
            # a malformed stream rather than something to silently attach.
            pending = []
            out.append(img)
            pos = img.end
            continue
        tok = next_token(data, pos)
        if tok is None:
            return out
        pos = tok.end
        if tok.kind == "operator":
            out.append(Operation(tok.raw, pending))
            pending = []
        else:
            pending.append(tok)
