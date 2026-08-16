"""PDF F1 — rebuild to a single revision, recursively, preserving content.

Two things happen here, and keeping them distinct matters because only the first is
what defeats the leak this phase is named for:

1. **The rewrite.** The document is re-emitted from scratch through our own
   serializer, so only what is reachable from `/Root` survives. Incremental-update
   history dies by construction — superseded objects are simply never reached, so
   there is no deletion pass that could miss one. E-PDF-HISTORY measured a rewrite
   alone, stripping nothing, already recovering nothing; that experiment exists so
   this tier cannot take credit for the scrub's half of the work.
   The corollary is W3's rule: a scrub must never *append*. ExifTool appends and
   therefore removes nothing at all.

2. **The scrub**, which is recursive from day one. The invariant is **not** "raw
   stream bytes identical" — that reading would leave every embedded JPEG's EXIF and
   thumbnail in place (failure mode #4) while looking rigorous. The invariant is:
   *each embedded leaf's content-bearing payload is bit-identical per that leaf
   format's own F1 definition.* So a `DCTDecode` image goes through `jpeg.f1.scrub`
   and its entropy-coded scan comes back byte-for-byte; only its metadata is gone.

Where this tier fails closed rather than guessing (each is a real document that we
decline instead of half-scrubbing):

  * **encrypted** files — the strings and streams are ciphertext;
  * **hybrid-reference** files (`/XRefStm`) — two cross-reference structures mean old
    and new readers see different documents, so "the document" is not well defined;
  * **signed** files — any rewrite invalidates the signature, and the `/Contents`
    PKCS#7 blob carries the signer's whole certificate chain. Silently handing back
    an invalidated signature is worse than refusing;
  * **XFA forms** and **embedded file attachments** — both are whole documents in
    their own right (`<xfa:datasets>` carries filled data and the template author; an
    attachment can be any format, including another PDF). Recursion through the
    dispatcher is the right answer and is not built yet, so they are refused rather
    than passed through unexamined.

Known residual, documented rather than silently accepted: **font internals**. Embedded
`/FontFile2` / `/FontFile3` programs carry `OS/2.achVendID`, `head.created`, `post`
and CFF `Notice`/`FullName`, and the subset tag names the subsetting tool. Those
identify the *producer*, not the document's author, so they are an A2 channel and
belong with F2's normalisation work — but they are in the output today and the matrix
must say so.
"""
from __future__ import annotations

import io

import pikepdf

from ...errors import ContentError, ParseError
from ..jpeg import f1 as jpeg_f1
from ..jpeg import segments as jseg
from . import content as ct
from . import serialize as ser
from . import walker as w

# Deleted wherever they appear, on any object in the graph.
#
# `/Metadata` is first for a reason: XMP is not a document-level thing. It can hang
# off an XObject, a font, or the catalog, and PDFs routinely carry BOTH `/Info` and
# an XMP packet with the same values — clearing one is the classic half-scrub.
# `xmpMM:History` and `xmpMM:DerivedFrom` are a full provenance chain.
DROP_ANYWHERE = frozenset({
    "/Metadata",        # XMP, on any object
    "/PieceInfo",       # Illustrator/InDesign stash the entire private source doc
    "/LastModified",    # paired with /PieceInfo
    "/SpiderInfo",      # web capture: source URL and capture time, per page
    "/Thumb",           # per-page embedded preview — the JPEG-thumbnail analogue
    "/CreatorInfo",     # /OCProperties → /OCGs → /Usage: names the creating app
})

# Deleted from annotation dictionaries: the commenter's name, when they wrote it,
# and the rich-text copy of the comment.
ANNOT_DROP = frozenset({"/T", "/M", "/RC", "/CreationDate", "/NM", "/Popup", "/IRT"})

# Actions carrying a local filesystem path or a launch target.
ACTION_DROP_TYPES = frozenset({"/Launch", "/GoToR", "/ImportData", "/JavaScript"})

_MAX_DEPTH = 64


def _refuse_structure(layout: w.Layout) -> None:
    """Refusals decidable from the bytes, checked *before* opening the document.

    Encryption belongs here rather than after the open: pikepdf raises
    `PasswordError` on an encrypted file, so a check that ran later would never be
    reached and the user would get a password complaint instead of the real reason.
    """
    if layout.encrypted:
        raise ParseError("PDF: encrypted documents are not supported (decrypt first)")
    if layout.hybrid:
        raise ParseError(
            "PDF: hybrid-reference file (/XRefStm) — two cross-reference structures "
            "can describe two different documents; refusing rather than picking one")


def _refuse(pdf: pikepdf.Pdf) -> None:
    if pdf.is_encrypted:
        raise ParseError("PDF: encrypted documents are not supported (decrypt first)")
    root = pdf.Root
    acro = root.get("/AcroForm")
    if acro is not None and "/XFA" in acro:
        raise ParseError(
            "PDF: XFA form — <xfa:datasets> carries filled data and the template "
            "author, and needs its own XML scrub (not built)")
    if acro is not None and acro.get("/SigFlags"):
        raise ParseError(
            "PDF: signed document — any rewrite invalidates the signature, and the "
            "/Contents PKCS#7 blob carries the signer's certificate chain")
    names = root.get("/Names")
    if names is not None and "/EmbeddedFiles" in names:
        raise ParseError(
            "PDF: embedded file attachments — an attachment is a whole document of "
            "its own and needs recursive dispatch (not built)")


def _walk_objects(pdf: pikepdf.Pdf):
    """Every indirect object reachable from the catalog, each yielded once."""
    for obj, indirect in _walk_all(pdf):
        if indirect:
            yield obj


def _walk_dicts(pdf: pikepdf.Pdf):
    """**Every** dictionary and stream in the graph — direct ones included.

    The distinction is not academic. `/OCProperties → /OCGs → /Usage → /CreatorInfo`
    and an annotation's `/A` launch action are ordinarily *direct* sub-dictionaries,
    so a pass that only visits indirect objects deletes neither. Both survived the
    first version of this module and were caught by the torture corpus; the residual
    check uses this same generator, because a verifier with less reach than the
    scrubber cannot see what the scrubber missed.
    """
    for obj, _ in _walk_all(pdf):
        if isinstance(obj, pikepdf.Dictionary | pikepdf.Stream):
            yield obj


def _walk_all(pdf: pikepdf.Pdf):
    """(object, is_indirect) for the whole graph. Indirect objects are visited once;
    direct containers are walked wherever they appear."""
    seen: set[tuple[int, int]] = set()
    stack = [(pdf.Root, 0)]
    while stack:
        obj, depth = stack.pop()
        if depth > _MAX_DEPTH:
            raise ParseError("PDF: object graph deeper than 64; refusing")
        indirect = isinstance(obj, pikepdf.Object) and obj.is_indirect
        if indirect:
            if obj.objgen in seen:
                continue
            seen.add(obj.objgen)
        if isinstance(obj, pikepdf.Dictionary | pikepdf.Stream | pikepdf.Array):
            yield obj, indirect
        if isinstance(obj, pikepdf.Dictionary | pikepdf.Stream):
            stack.extend((obj[k], depth + 1) for k in sorted(obj.keys()))
        elif isinstance(obj, pikepdf.Array):
            stack.extend((v, depth + 1) for v in obj)


def _strip_keys(pdf: pikepdf.Pdf) -> None:
    """Delete the metadata keys, wherever in the graph they turn up."""
    for obj in _walk_dicts(pdf):
        for key in sorted(set(obj.keys()) & DROP_ANYWHERE):
            del obj[key]
        if str(obj.get("/Type") or "") == "/Annot":
            for key in sorted(set(obj.keys()) & ANNOT_DROP):
                del obj[key]
        if str(obj.get("/S") or "") in ACTION_DROP_TYPES:
            # A /Launch or /GoToR target is a full local filesystem path, and
            # /ImportData and /JavaScript carry one too.
            for key in sorted(set(obj.keys()) & {"/F", "/UF", "/Win", "/D", "/JS"}):
                del obj[key]

    # Annotations are reached through pages, and an annotation dictionary need not
    # carry /Type — so clear them by their structural position as well as by type.
    for page in pdf.pages:
        for annot in (page.obj.get("/Annots") or []):
            if isinstance(annot, pikepdf.Dictionary):
                for key in sorted(set(annot.keys()) & ANNOT_DROP):
                    del annot[key]


def _scrub_streams(pdf: pikepdf.Pdf) -> None:
    """Recurse into embedded leaves — the half that makes this F1 and not a tag wipe."""
    from ...standards import icc

    for obj in _walk_objects(pdf):
        if not isinstance(obj, pikepdf.Stream):
            continue
        filters = _filter_names(obj)
        subtype = str(obj.get("/Subtype") or "")

        if "/DCTDecode" in filters:
            raw = obj.read_raw_bytes()
            cleaned = jpeg_f1.scrub(raw)
            if _jpeg_scan(cleaned) != _jpeg_scan(raw):
                raise ContentError("PDF: embedded JPEG scan changed during scrub")
            obj.write(cleaned, filter=obj.get("/Filter"),
                      decode_parms=obj.get("/DecodeParms"))
        elif subtype == "/Image":
            # Non-DCT images decode to pixels; nothing metadata-bearing rides along,
            # and re-encoding would break F1's bit-preserving promise.
            continue
        elif obj.get("/N") is not None and "/Length1" not in obj:
            # An /ICCBased colour-space stream: /N is its component count. The header
            # carries CMM, platform, manufacturer, creator and a creation date;
            # standards/icc.py already zeroes those and recomputes the profile ID, so
            # the colour data — the tag table and everything after the header — is
            # untouched and rendering does not change.
            profile = obj.read_bytes()
            if len(profile) >= icc.HEADER_LEN and profile[36:40] == b"acsp":
                obj.write(icc.sanitize(profile))

    for page in pdf.pages:
        _scrub_page_content(page)


def _filter_names(obj) -> list[str]:
    f = obj.get("/Filter")
    if f is None:
        return []
    return [str(f)] if isinstance(f, pikepdf.Name) else [str(x) for x in f]


def _jpeg_scan(data: bytes) -> bytes:
    """The entropy-coded image data — what must survive a JPEG F1 byte for byte."""
    s = jseg.walk(data)
    return b"".join(data[sg.offset:sg.end] for sg in s.segments if sg.marker == jseg.SOS)


def _scrub_page_content(page) -> None:
    """Clean inline images, which no object-graph walk can see.

    `BI /F /DCTDecode ID <binary> EI` is a complete JPEG with no object number and no
    resource entry — so a scrubber that only walks objects leaves a GPS-tagged
    photograph sitting in the page description.
    """
    contents = page.obj.get("/Contents")
    if contents is None:
        return
    streams = list(contents) if isinstance(contents, pikepdf.Array) else [contents]
    for stream in streams:
        if not isinstance(stream, pikepdf.Stream):
            continue
        data = stream.read_bytes()
        if b"BI" not in data:
            continue
        cleaned = ct.replace_inline_images(
            data, lambda img: jpeg_f1.scrub(img.data) if img.is_jpeg else img.data)
        if cleaned != data:
            stream.write(cleaned)


def scrub(data: bytes) -> bytes:
    _refuse_structure(w.walk(data))
    # W0: pikepdf's defaults are fail-open (attempt_recovery=True,
    # suppress_warnings=True), which is the opposite of this project's doctrine —
    # a recovered file is one whose real structure we did not read.
    try:
        pdf = pikepdf.open(io.BytesIO(data), attempt_recovery=False,
                           suppress_warnings=False)
    except pikepdf.PdfError as exc:
        raise ParseError(f"PDF: will not open without recovery: {exc}") from exc

    with pdf:
        _refuse(pdf)
        # Raw delete, never open_metadata(): W0 measured that stamping `pikepdf
        # 10.8.0` into both XMP and /Info, plus a wall-clock xmp:MetadataDate.
        if "/Metadata" in pdf.Root:
            del pdf.Root["/Metadata"]
        _strip_keys(pdf)
        _scrub_streams(pdf)
        out = ser.serialize(pdf)

    _assert_content_identity(data, out)
    return out


# --------------------------------------------------------------------------- #
# Content identity
# --------------------------------------------------------------------------- #
def _content_fingerprints(data: bytes) -> list[tuple[str, bytes]]:
    """An ordered list of the document's content-bearing payloads.

    Ordered and compared **pairwise**, not as a multiset: a transposition — page 2's
    image ending up on page 1 — leaves the multiset identical and would pass a
    set-based check while being a real content change. Same shape as `m4a/f1.py`'s
    `out_mdat.payload != audio` assertion, extended to a document's many payloads.
    """
    out: list[tuple[str, bytes]] = []
    with pikepdf.open(io.BytesIO(data)) as pdf:
        for i, page in enumerate(pdf.pages):
            contents = page.obj.get("/Contents")
            streams = ([] if contents is None
                       else list(contents) if isinstance(contents, pikepdf.Array)
                       else [contents])
            body = b"".join(s.read_bytes() for s in streams
                            if isinstance(s, pikepdf.Stream))
            # Inline-image payloads are compared as JPEG scans, since F1 strips their
            # metadata exactly as it does a DCT XObject's.
            for img in ct.inline_images(body):
                body = body.replace(img.data, _jpeg_scan(img.data) if img.is_jpeg
                                    else img.data)
            out.append((f"page{i}:content", body))

            resources = page.obj.get("/Resources") or {}
            xobjects = resources.get("/XObject") or {}
            for name in sorted(xobjects.keys()):
                xo = xobjects[name]
                if not isinstance(xo, pikepdf.Stream):
                    continue
                if "/DCTDecode" in _filter_names(xo):
                    out.append((f"page{i}:{name}", _jpeg_scan(xo.read_raw_bytes())))
                else:
                    out.append((f"page{i}:{name}", xo.read_bytes()))
    return out


def _assert_content_identity(before: bytes, after: bytes) -> None:
    a, b = _content_fingerprints(before), _content_fingerprints(after)
    if len(a) != len(b):
        raise ContentError(
            f"PDF: content stream count changed ({len(a)} -> {len(b)})")
    for (ka, va), (kb, vb) in zip(a, b, strict=True):
        if va != vb:
            raise ContentError(
                f"PDF: content changed at {ka} (vs {kb}): "
                f"{len(va)} -> {len(vb)} bytes")


# --------------------------------------------------------------------------- #
# Residuals
# --------------------------------------------------------------------------- #
def residuals(data: bytes) -> list[str]:
    """Re-read the output as an adversary would. Anything here fails the scrub."""
    out: list[str] = []
    layout = w.walk(data)

    if len(layout.revisions) != 1:
        out.append(f"{len(layout.revisions)} revisions survived (history is readable)")
    if layout.superseded:
        out.append(f"superseded object definitions survived: {layout.superseded}")
    if layout.binary_comment is not None:
        out.append(f"binary header comment survived: {layout.binary_comment!r}")

    tail = data[data.rfind(b"trailer"):]
    for key in (b"/Info", b"/ID", b"/Prev", b"/Encrypt"):
        if key in tail:
            out.append(f"trailer still carries {key.decode()}")

    with pikepdf.open(io.BytesIO(data)) as pdf:
        for obj in _walk_dicts(pdf):
            for key in sorted(set(obj.keys()) & DROP_ANYWHERE):
                out.append(f"{key} survived on object {obj.objgen[0]}")
            if str(obj.get("/S") or "") in ACTION_DROP_TYPES and "/F" in obj:
                out.append(f"{obj.get('/S')} action target survived")
        for page in pdf.pages:
            for annot in (page.obj.get("/Annots") or []):
                if isinstance(annot, pikepdf.Dictionary):
                    for key in sorted(set(annot.keys()) & ANNOT_DROP):
                        out.append(f"annotation {key} survived")
        out.extend(_stream_residuals(pdf))
    return out


def _stream_residuals(pdf: pikepdf.Pdf) -> list[str]:
    """Scan **decoded stream content, per stream, classified by role**.

    Deliberately not the whole-file magic scan that `flac/f1.py` and `m4a/f1.py` use:
    `\\xff\\xd8\\xff` occurs inside any Flate stream by chance *and* at the head of
    every legitimately embedded JPEG, so a file-wide scan would be both false-positive
    and false-negative here. Same lesson `m4a/f1.py:137` records for its
    metadata-region-only scan.
    """
    out: list[str] = []
    for obj in _walk_objects(pdf):
        if not isinstance(obj, pikepdf.Stream):
            continue
        if "/DCTDecode" in _filter_names(obj):
            leftover = jpeg_f1.residuals(obj.read_raw_bytes())
            out.extend(f"embedded JPEG: {r}" for r in leftover)
            continue
        subtype = str(obj.get("/Subtype") or "")
        if subtype in ("/Image", "/Form") or obj.get("/Type") == "/Page":
            continue
        try:
            decoded = obj.read_bytes()
        except Exception:
            continue
        if decoded[:5] in (b"<?xpa", b"<x:xm") or b"<x:xmpmeta" in decoded[:512]:
            out.append(f"XMP packet survived in stream {obj.objgen[0]}")
    for page in pdf.pages:
        contents = page.obj.get("/Contents")
        streams = ([] if contents is None
                   else list(contents) if isinstance(contents, pikepdf.Array)
                   else [contents])
        for stream in streams:
            if not isinstance(stream, pikepdf.Stream):
                continue
            for img in ct.inline_images(stream.read_bytes()):
                if img.is_jpeg:
                    out.extend(f"inline image: {r}"
                               for r in jpeg_f1.residuals(img.data))
    return out
