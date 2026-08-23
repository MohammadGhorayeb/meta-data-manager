"""PDF F3 — rasterise and rebuild. The structural A2 defence, at a real cost.

Every page is rendered to pixels by one renderer at one pinned resolution and the
document is rebuilt around the images. Nothing of the original's construction
survives: no fonts, no content-stream operators, no object graph beyond the page
tree our own serializer writes. Whatever a producer's typesetter chose to embed is
gone because the page is now a photograph of itself.

**This tier's technique is not ours.** MAT2's default PDF path and Dangerzone both do
exactly this, and the plan says so plainly: Phase 3's contribution is F1 and F2. F3 is
here because it is a real option with a real cost that a user should be allowed to
choose, not because it is novel.

**The cost is not "quality" in the way F3 means it for JPEG or MP3.** Hard constraint
#1 is satisfied — the visible page is preserved, pixel for pixel at the render
resolution. What is destroyed is everything a reader does with a document that is not
looking at it: the text cannot be searched, selected or copied, and a screen reader
finds nothing at all. That is an accessibility regression, not a compression
artefact, and `docs/limits.md` records it as one.

**What F3 does not remove**, and this is the point of measuring rather than asserting:
glyph geometry. Where each glyph sits, how far it advances, where the lines break —
all of it is inherited from the original typesetter and all of it is still visible in
the pixels. Rasterising moves the residual from operator space into pixel space; it
does not delete it. Poppler's own hinting and antialiasing *are* uniform across every
file we emit, so that part is anonymity-within-class exactly as `jpeg/f3.py`'s libjpeg
quantisation tables are. E-PDF-RASTER is what turns the rest into a number, and the
DPI knob is what might turn an impossibility into a Pareto trade.

**Why poppler as a subprocess.** Same footing as `ffmpeg`, `lame` and `jpegtran`
elsewhere in this project: never linked, always shelled out to, so its licence never
touches ours and a missing binary fails closed with a message rather than silently
downgrading the tier.
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile

import pikepdf

from ...errors import ContentError, ParseError, ScrubError
from ..jpeg import f1 as jpeg_f1
from . import f1
from . import serialize as ser

# One resolution for every output, so the choice is ours and constant rather than the
# input's. 150 DPI is the point where body text stays comfortably legible on screen
# and in print at 100%; E-PDF-RASTER measures whether it is also low enough to blur
# the typesetter's sub-pixel geometry, which is the question the knob exists for.
DEFAULT_DPI = 150

# The page image is a JPEG at one pinned quality, then run through the Phase 1 JPEG
# handler so it carries no APP segment of its own. Quality is high enough that the
# rasterised text has no visible ringing; the tier is already lossy by construction,
# so the honest thing is to pick one setting and never vary it per input.
JPEG_QUALITY = 90

_MAX_PAGES = 2000


def _tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise ScrubError(
            f"{name} required for PDF F3 (brew install poppler). F3 fails closed "
            "rather than emit a file it did not rasterise.")
    return path


def _page_count(data: bytes) -> int:
    with pikepdf.open(io.BytesIO(data)) as pdf:
        return len(pdf.pages)


def _render(data: bytes, dpi: int, workdir: str) -> list[str]:
    """Every page as a PNG, in page order.

    `-r` fixes the resolution rather than `-scale-to`, because a pinned DPI makes the
    output's pixel dimensions a function of the page's own size — which is content —
    instead of a constant we would be imposing on every document.
    """
    src = os.path.join(workdir, "in.pdf")
    with open(src, "wb") as f:
        f.write(data)
    stem = os.path.join(workdir, "page")
    result = subprocess.run(
        [_tool("pdftoppm"), "-r", str(dpi), "-png", src, stem],
        capture_output=True)
    if result.returncode != 0:
        raise ScrubError(
            "PDF F3: pdftoppm failed to render the document: "
            f"{result.stderr.decode('utf-8', 'replace').strip()[:200]}")
    pages = sorted(p for p in os.listdir(workdir)
                   if p.startswith("page-") and p.endswith(".png"))
    if not pages:
        raise ScrubError("PDF F3: pdftoppm produced no pages")
    return [os.path.join(workdir, p) for p in pages]


def _jpeg(png_path: str) -> tuple[bytes, int, int]:
    """One page image as a metadata-free JPEG, with its pixel dimensions."""
    from PIL import Image

    with Image.open(png_path) as im:
        im.load()
        rgb = im.convert("RGB")
        buf = io.BytesIO()
        rgb.save(buf, "JPEG", quality=JPEG_QUALITY, optimize=False,
                 progressive=False, subsampling=0)
        size = rgb.size
    # Pillow writes its own JFIF APP0 and nothing else, but routing through the Phase 1
    # handler is what keeps that a checked fact rather than an assumption — and it is
    # the same module every other embedded JPEG in this project goes through.
    return jpeg_f1.scrub(buf.getvalue()), size[0], size[1]


def _rebuild(pages: list[str], dpi: int) -> pikepdf.Pdf:
    """A new document whose every page is one full-bleed image.

    The content stream is written here rather than by `canon`, because there is
    nothing to canonicalise: it is the same nine tokens on every page of every file we
    emit, which is precisely the structural uniformity this tier buys.
    """
    pdf = pikepdf.new()
    for path in pages:
        jpeg, width_px, height_px = _jpeg(path)
        # Back to PDF user space: 72 units to the inch, whatever the render DPI was.
        width = width_px * 72.0 / dpi
        height = height_px * 72.0 / dpi

        image = pikepdf.Stream(pdf, jpeg)
        image.Type = pikepdf.Name.XObject
        image.Subtype = pikepdf.Name.Image
        image.Width, image.Height = width_px, height_px
        image.ColorSpace = pikepdf.Name.DeviceRGB
        image.BitsPerComponent = 8
        image.Filter = pikepdf.Name.DCTDecode

        page = pikepdf.Dictionary(
            Type=pikepdf.Name.Page,
            MediaBox=[0, 0, round(width, 4), round(height, 4)],
            Resources=pikepdf.Dictionary(
                XObject=pikepdf.Dictionary(Im0=pdf.make_indirect(image))),
            Contents=pdf.make_stream(
                b"q %g 0 0 %g 0 0 cm /Im0 Do Q\n" % (width, height)))
        pdf.pages.append(pikepdf.Page(pdf.make_indirect(page)))
    return pdf


def scrub(data: bytes, dpi: int = DEFAULT_DPI) -> bytes:
    """Render every page at `dpi` and rebuild the document around the images.

    `dpi` is a parameter rather than a constant so E-PDF-RASTER can sweep it. The CLI
    pins `DEFAULT_DPI`: a per-file resolution would put the *user's* choice into the
    output, which is a channel of exactly the kind this tier exists to close.
    """
    if dpi <= 0:
        raise ParseError(f"PDF F3: nonsensical render resolution {dpi}")
    # The same refusals F1 makes, for the same reasons — an encrypted or signed file
    # is not something to quietly rasterise either.
    f1._refuse_structure(f1.w.walk(data))
    try:
        with pikepdf.open(io.BytesIO(data), attempt_recovery=False,
                          suppress_warnings=False) as pdf:
            f1._refuse(pdf)
    except pikepdf.PdfError as exc:
        raise ParseError(f"PDF: will not open without recovery: {exc}") from exc

    expected = _page_count(data)
    if expected > _MAX_PAGES:
        raise ScrubError(f"PDF F3: {expected} pages exceeds the {_MAX_PAGES}-page cap")

    with tempfile.TemporaryDirectory(prefix="pdf_f3_") as workdir:
        pages = _render(data, dpi, workdir)
        if len(pages) != expected:
            raise ContentError(
                f"PDF F3: the document has {expected} page(s) but pdftoppm rendered "
                f"{len(pages)} — refusing rather than shipping a truncated document")
        with _rebuild(pages, dpi) as rebuilt:
            return ser.serialize(rebuilt)


def residuals(data: bytes) -> list[str]:
    """Re-read the output as an adversary would. Anything here **fails** the scrub.

    F3's output is built rather than edited, so the checks are about what we emitted:
    every page must be one image and nothing else. A surviving font or content
    operator would mean a page was not actually rasterised — the silent failure that
    matters here, because such a file still *looks* scrubbed.
    """
    out = list(f1.residuals(data))
    try:
        with pikepdf.open(io.BytesIO(data)) as pdf:
            for i, page in enumerate(pdf.pages):
                resources = page.obj.get("/Resources") or pikepdf.Dictionary()
                if "/Font" in resources:
                    out.append(f"page {i} still carries a font — it was not rasterised")
                contents = page.obj.get("/Contents")
                if not isinstance(contents, pikepdf.Stream):
                    out.append(f"page {i} has no single content stream")
                    continue
                body = contents.read_bytes()
                if b"BT" in body or b"Tj" in body or b"TJ" in body:
                    out.append(f"page {i} still carries text operators")
            for obj in f1._walk_objects(pdf):
                if isinstance(obj, pikepdf.Stream) and "/FontFile" in str(obj.keys()):
                    out.append("an embedded font program survived rasterisation")
    except Exception as exc:
        out.append(f"F3 output could not be re-read for verification: {exc}")
    return out


def limits() -> list[str]:
    """What F3 leaves behind, or costs, by design — the matrix's residual column."""
    return [
        "the text layer is destroyed: a scrubbed file cannot be searched, selected, "
        "copied from, or read by a screen reader. That is an accessibility "
        "regression, not a compression artefact",
        "glyph geometry survives into pixel space — where glyphs sit, how they "
        "advance and where lines break are still visible in the rendered page, and "
        "rasterising relocates that residual rather than removing it",
        f"every output is rendered by one poppler build at {DEFAULT_DPI} DPI and "
        f"encoded by one JPEG setting, so anonymity is *within* that class, exactly "
        "as jpeg/f3.py's libjpeg quantisation tables are",
        "a scanned page was already an image, so PRNU sensor noise passes through "
        "untouched — inherited from Phase 1, not new here",
    ]
