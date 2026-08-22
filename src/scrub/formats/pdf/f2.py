"""PDF F2 — lossless re-encode: one canonical spelling of the same page.

F1 closed the **serializer** channel (M3, §2.3): our own writer decides the header,
the object order, the xref style, so every producer's file leaves with the same
skeleton. What M3 measured still leaking is the **layout** channel — the content
stream itself — and F2 is the tier allowed to rewrite it, because rewriting the marks
is a re-encode rather than a byte-preserving edit.

Four normalisations, in the order W5 predicted they were reachable:

1. **One content stream per page.** A page may carry an array of streams that the
   reader concatenates; how a producer splits them is style with no rendering
   meaning, and it is `struct:stream_count` in the matrix.
2. **Canonical marks** (`canon.py`) — number spelling, `Td`/`TD`/`T*` folded into the
   absolute `Tm` they amount to, show runs merged into one `TJ`, canonical strings,
   names and whitespace. This is `struct:operators` and `struct:number_style`.
3. **One compression filter.** Producers pick different filters and different deflate
   levels for the same bytes, so every stream we can decode is re-emitted as
   `/FlateDecode` at one pinned level, predictors and filter chains gone.
4. **Canonical font subset tags.** `/ABCDEF+Helvetica` carries a six-letter tag that
   is arbitrary per producer (ISO 32000 §9.6.4 requires only that it be unique within
   the document), so tags are reassigned in first-appearance order. This is
   `struct:font_subsets`.

**What F2 does not do, on purpose.** Glyph positions, advances, line breaks and
subset composition are the typesetter's geometry: they cannot change without
re-flowing the page, so `struct:glyph_digest` is the predicted floor and M3's test
asserts it is still leaking. An F2 that quietly rounded coordinates until producers
collided would be defeating the measurement rather than the leak.

**The content promise.** F1 can assert byte-identity of the content streams; F2
cannot, since rewriting them is the whole job. What it asserts instead is
`canon.painted()` on both sides — the glyphs shown in order, every non-rewritten
operator with its operands, and the replayed absolute position and spacing at each
show. A rewrite that moved a line while still painting every glyph is precisely the
failure this tier risks, and that third component is what catches it.
"""
from __future__ import annotations

import io
import zlib

import pikepdf

from ...errors import ContentError, ParseError
from . import canon, f1
from . import serialize as ser

# Pinned, so deflate level stops being a producer tell — and pinned to **6**, which
# is measured rather than picked. The zlib header byte pair gives the level away
# (`78 9C` = 6, `78 DA` = 9, `78 01` = 1), and across the M3 peer corpus four of five
# producers emit level 6 and one emits level 1; **none** emits 9. Level 9 was the
# first choice here, copied from `png/f2.py`, and the fingerprint guard duly failed
# with ` >>\nstream\nx\xda` as our signature: a level nothing else in the corpus
# uses does not normalise our output, it labels it. Level 6 joins the largest crowd
# that actually exists, which is the same reasoning the Phase 6 decoy-metadata item
# records for mandatory timestamps.
_COMPRESS_LEVEL = 6

# Filters left exactly as they are. `/DCTDecode` and `/JPXDecode` are lossy, so
# decoding and re-deflating them would be an F3 re-encode wearing an F2 label; the
# two fax/bilevel codecs are lossless but re-encoding them to Flate multiplies the
# size of a scanned page many times over for a normalisation nobody asked for. Both
# groups are therefore residual filter-choice signal, and `limits()` names them.
_KEEP_FILTER = frozenset({"/DCTDecode", "/JPXDecode", "/JBIG2Decode",
                          "/CCITTFaxDecode"})

_SUBSET_TAG_LEN = 6


# --------------------------------------------------------------------------- #
# Content streams
# --------------------------------------------------------------------------- #
def _is_form(obj) -> bool:
    return (isinstance(obj, pikepdf.Stream)
            and str(obj.get("/Subtype") or "") == "/Form")


def _is_tiling_pattern(obj) -> bool:
    try:
        return (isinstance(obj, pikepdf.Stream)
                and int(obj.get("/PatternType") or 0) == 1)
    except (TypeError, ValueError):
        return False


def _charproc_streams(pdf: pikepdf.Pdf) -> list:
    """Type3 glyph procedures. They are content streams with no `/Subtype` saying so,
    reachable only through a font's `/CharProcs`, and a walk that keyed off `/Subtype`
    alone would leave every Type3 glyph un-canonicalised."""
    out = []
    for obj in f1._walk_objects(pdf):
        if not isinstance(obj, pikepdf.Dictionary):
            continue
        procs = obj.get("/CharProcs")
        if isinstance(procs, pikepdf.Dictionary):
            out.extend(v for v in procs.values() if isinstance(v, pikepdf.Stream))
    return out


def _merge_page_contents(pdf: pikepdf.Pdf) -> None:
    """Collapse each page's content array into one stream.

    ISO 32000 §7.8.2: the streams are concatenated *with intervening whitespace* and
    read as one, and a division may fall only at a token boundary — so joining them
    with a newline is exactly what a reader does, and it removes the producer's
    chunking decision from the file.
    """
    for page in pdf.pages:
        contents = page.obj.get("/Contents")
        if contents is None or not isinstance(contents, pikepdf.Array):
            continue
        streams = [s for s in contents if isinstance(s, pikepdf.Stream)]
        if len(streams) != len(list(contents)):
            raise ParseError("PDF: /Contents array holds a non-stream entry")
        merged = b"\n".join(s.read_bytes() for s in streams)
        page.obj["/Contents"] = pdf.make_stream(merged)


def _content_streams(pdf: pikepdf.Pdf) -> list[tuple]:
    """Every stream whose bytes are page-description marks, paired with whether it
    **inherits** its initial text state.

    A page's content stream starts in the default graphics state. A form, a tiling
    pattern and a Type3 glyph procedure are invoked from somewhere else and start in
    the caller's state, which `canon` has to be told about or it will normalise
    against a leading and a spacing that were never in force.
    """
    out: list[tuple] = []
    for page in pdf.pages:
        contents = page.obj.get("/Contents")
        if isinstance(contents, pikepdf.Stream):
            out.append((contents, False))
        elif isinstance(contents, pikepdf.Array):
            out.extend((s, False) for s in contents if isinstance(s, pikepdf.Stream))
    seen = {id(s) for s, _ in out}
    for obj in f1._walk_objects(pdf):
        if (_is_form(obj) or _is_tiling_pattern(obj)) and id(obj) not in seen:
            out.append((obj, True))
            seen.add(id(obj))
    for obj in _charproc_streams(pdf):
        if id(obj) not in seen:
            out.append((obj, True))
            seen.add(id(obj))
    return out


def canonical_or_none(body: bytes, inherits: bool) -> bytes | None:
    """The canonical form, or None when this stream cannot be resolved.

    The one case that reaches None: an invoked stream that advances a line with `T*`,
    `'` or `"` while its leading came from the caller. The absolute position depends
    on a number that is not in the stream, so `canon` refuses rather than guessing —
    and the right answer here is to leave that one stream exactly as it is, not to
    refuse the whole document over a construct the rest of the file does not use.
    Whatever is left un-canonicalised is reported by `residuals()`, so it fails the
    scrub loudly rather than passing quietly as normalised.
    """
    try:
        return canon.canonicalize(body, inherits)
    except ParseError as exc:
        if "inherited from the caller" in str(exc):
            return None
        raise


def _canonicalize_content(pdf: pikepdf.Pdf) -> None:
    for stream, inherits in _content_streams(pdf):
        canonical = canonical_or_none(stream.read_bytes(), inherits)
        if canonical is not None:
            stream.write(canonical)


# --------------------------------------------------------------------------- #
# Compression
# --------------------------------------------------------------------------- #
def _recompress(pdf: pikepdf.Pdf) -> None:
    """One filter, one level, no predictors — for every stream we may decode."""
    for obj in f1._walk_objects(pdf):
        if not isinstance(obj, pikepdf.Stream):
            continue
        if set(f1._filter_names(obj)) & _KEEP_FILTER:
            continue
        try:
            payload = obj.read_bytes()
        except Exception as exc:                     # unknown/broken filter
            raise ParseError(f"PDF: cannot decode stream for re-encode: {exc}") from exc
        obj.write(zlib.compress(payload, _COMPRESS_LEVEL),
                  filter=pikepdf.Name("/FlateDecode"))


# --------------------------------------------------------------------------- #
# Font subset tags
# --------------------------------------------------------------------------- #
def _subset_tag(name: str) -> str | None:
    """The `ABCDEF` of `/ABCDEF+Helvetica`, or None when the font is not subset."""
    body = name.lstrip("/")
    if len(body) > _SUBSET_TAG_LEN and body[_SUBSET_TAG_LEN] == "+":
        tag = body[:_SUBSET_TAG_LEN]
        if tag.isalpha() and tag.isupper():
            return tag
    return None


def _tag_for(index: int) -> str:
    """`AAAAAA`, `AAAAAB`, … — deterministic, dense, and 26**6 deep."""
    letters = []
    for _ in range(_SUBSET_TAG_LEN):
        index, rem = divmod(index, 26)
        letters.append(chr(ord("A") + rem))
    if index:
        raise ParseError("PDF: more than 26**6 font subsets; refusing to reuse a tag")
    return "".join(reversed(letters))


def _normalize_subset_tags(pdf: pikepdf.Pdf) -> None:
    """Reassign subset tags in first-appearance order.

    Renamed on `/BaseFont` and on the descriptor's `/FontName`, which ISO 32000
    §9.8.1 requires to agree. The name *inside* the embedded font program is not
    rewritten — a CFF `FontFile3` carries its own name index, and editing that means
    parsing and re-emitting the font. Readers use the PDF dictionary, so rendering is
    unaffected, but the original tag does survive in the font program and that is a
    measured residual, not a silent one: `residuals()` reports it.
    """
    assigned: dict[str, str] = {}
    for obj in sorted(f1._walk_objects(pdf),
                      key=lambda o: o.objgen if isinstance(o, pikepdf.Object) else (0, 0)):
        if not isinstance(obj, pikepdf.Dictionary):
            continue
        for key in ("/BaseFont", "/FontName"):
            value = obj.get(key)
            if not isinstance(value, pikepdf.Name):
                continue
            name = str(value)
            tag = _subset_tag(name)
            if tag is None:
                continue
            if tag not in assigned:
                assigned[tag] = _tag_for(len(assigned))
            obj[key] = pikepdf.Name("/" + assigned[tag] + name.lstrip("/")[_SUBSET_TAG_LEN:])


# --------------------------------------------------------------------------- #
# The content promise
# --------------------------------------------------------------------------- #
def _marks(data: bytes) -> tuple:
    """Every content stream's `canon.painted()`, as a sorted multiset.

    Sorted rather than ordered because the two sides are renumbered relative to each
    other — F1's serializer assigns its own object numbers — so position in a walk is
    not a stable identity. Order *within* a stream is what carries meaning, and
    `painted()` preserves it.
    """
    with pikepdf.open(io.BytesIO(data)) as pdf:
        _merge_page_contents(pdf)                    # compare like with like
        return tuple(sorted(canon.painted(s.read_bytes(), inherits)
                            for s, inherits in _content_streams(pdf)))


def _assert_paint_identity(original: bytes, scrubbed: bytes) -> None:
    before, after = _marks(original), _marks(scrubbed)
    if before != after:
        raise ContentError(
            "PDF F2: the page no longer paints what it did — "
            f"{len(before)} content stream(s) in, {len(after)} out, "
            "and their marks differ")


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def scrub(data: bytes) -> bytes:
    """F1's object-graph scrub, then the content normalisations F1 may not make."""
    f1._refuse_structure(f1.w.walk(data))
    try:
        pdf = pikepdf.open(io.BytesIO(data), attempt_recovery=False,
                           suppress_warnings=False)
    except pikepdf.PdfError as exc:
        raise ParseError(f"PDF: will not open without recovery: {exc}") from exc

    with pdf:
        f1._refuse(pdf)
        f1._strip_keys(pdf)
        f1._scrub_streams(pdf)
        _merge_page_contents(pdf)
        _canonicalize_content(pdf)
        _normalize_subset_tags(pdf)
        _recompress(pdf)
        out = ser.serialize(pdf)

    _assert_paint_identity(data, out)
    return out


def residuals(data: bytes) -> list[str]:
    """Re-read the output as an adversary would. Anything here **fails** the scrub.

    So this is not the place for the tier's documented limits — glyph geometry, the
    font program's internal subset name, the filters we deliberately do not touch.
    Those are measured residuals and they live in `docs/limits.md` and the Pareto
    matrix; listing them here would fail every clean F2 output and make the check
    useless. What it reports instead is a normalisation that did not happen, since a
    half-canonicalised stream is exactly the leak this tier exists to close.
    """
    out = list(f1.residuals(data))
    try:
        with pikepdf.open(io.BytesIO(data)) as pdf:
            for i, (stream, inherits) in enumerate(_content_streams(pdf)):
                body = stream.read_bytes()
                canonical = canonical_or_none(body, inherits)
                if canonical is None:
                    out.append(f"content stream {i} could not be canonicalised "
                               "(leading inherited from its caller)")
                elif canonical != body:
                    out.append(f"content stream {i} is not canonical")
            for obj in f1._walk_objects(pdf):
                if not isinstance(obj, pikepdf.Stream):
                    continue
                stray = [f for f in f1._filter_names(obj)
                         if f not in _KEEP_FILTER and f != "/FlateDecode"]
                if stray:
                    out.append(f"stream {obj.objgen[0]} kept filter(s) "
                               + ", ".join(stray))
            tags = sorted({t for obj in f1._walk_objects(pdf)
                           if isinstance(obj, pikepdf.Dictionary)
                           for k in ("/BaseFont", "/FontName")
                           if isinstance(obj.get(k), pikepdf.Name)
                           for t in [_subset_tag(str(obj.get(k)))] if t})
            expected = [_tag_for(i) for i in range(len(tags))]
            if tags != expected:
                out.append(f"font subset tags not canonical: {tags} != {expected}")
    except ParseError:
        raise
    except Exception as exc:
        out.append(f"F2 output could not be re-read for verification: {exc}")
    return out


def limits() -> list[str]:
    """What F2 leaves behind by design — the Pareto matrix's residual column.

    Separate from `residuals()` on purpose: these are measured properties of the
    tier, not failures of a run, and conflating the two is what made the first cut
    reject its own correct output.
    """
    return [
        "glyph positions, advances, line breaks and subset composition are unchanged "
        "— the typesetter's geometry, which F2 cannot alter without re-flowing the page",
        "embedded font programs keep their original internal subset name; only the "
        "PDF-level /BaseFont and /FontName tags are reassigned",
        "filter choice is not normalised for " + ", ".join(sorted(_KEEP_FILTER))
        + " — re-encoding them would be lossy or gratuitously large",
        "content-stream coordinates are quantised to 1E-8 user-space units, so F2 is "
        "lossless to well below a rendered dot rather than bit-exact in the numbers",
    ]
