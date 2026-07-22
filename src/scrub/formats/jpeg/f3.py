"""JPEG F3 — canonical lossy re-encode (W6). The A2 defense.

F1/F2 keep the original coefficients, so they are forced to keep the DQT — the
quantization table that fingerprints the producer (E3 proved this survives both).
F3 breaks that: it fully decodes the image and re-encodes it with ONE canonical
encoder and fixed settings, so **every output carries the same DQT** no matter
what produced the input. The producer fingerprint is normalized away.

The tradeoff, documented not hidden (p1 plan W6): the A2 defense is *anonymity
within the scrubbed class*, not invisibility. Every F3 output now looks like the
same generic libjpeg-turbo baseline encoder — the *original* producer is gone,
but "this file was F3-scrubbed by a libjpeg-class encoder" is knowable. That is
true of every F3 tool and belongs in the matrix notes. Further residuals: PRNU
sensor noise (structural impossibility, Lukas) and first-generation quantization
traces recoverable from coefficient histograms (Sorell, bounded by E4).

Canonical settings (fixed => content-independent DQT => A2 anonymity):
  quality 90, 4:2:0 chroma subsampling, optimized baseline Huffman, no metadata.
These are a locked policy choice (p1 plan §4); quality trades content fidelity
against the size of the anonymity set and can be re-confirmed, but must stay
fixed across all inputs or the DQT stops being uniform.

Content preservation (F3 is lossy, so NOT bit/pixel identical): a pHash distance
gate. Calibrated on the E3 corpus the re-encode floor was max=2; the gate is set
at 6 for headroom, tight and well under Krawetz's 10-bit ceiling. Exceeding it
raises ContentError — fail closed rather than ship a mangled image.
"""
from __future__ import annotations

import io
import warnings

from ...errors import ContentError, ParseError, ScrubError
from . import segments as seg

CANONICAL_QUALITY = 90
CANONICAL_SUBSAMPLING = "4:2:0"          # Pillow accepts the "4:2:0" spelling
# pHash Hamming gate. Calibrated: observed max 2 across the E3 corpus re-encode;
# 6 gives headroom for varied content while staying a tight single-digit gate.
PHASH_MAX_DISTANCE = 6


def _open_rgb(data: bytes):
    """Decode to a BARE RGB image — no carried info dict. Critical: Pillow stashes
    a source JPEG's COM/EXIF/ICC in ``im.info`` and re-emits them on save (a COM
    leak F3 must not ship), so we rebuild from raw pixels to drop all of it."""
    from PIL import Image
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", ".*malformed MPO.*")
        with Image.open(io.BytesIO(data)) as im:
            rgb = im.convert("RGB")
            return Image.frombytes("RGB", rgb.size, rgb.tobytes())


def scrub(data: bytes) -> bytes:
    """Decode and re-encode with the canonical encoder; strip all metadata.
    Raises ContentError if the re-encode diverges perceptually past the gate."""
    # Parse first so a non-JPEG / malformed input fails closed with our error.
    seg.walk(data)

    im = _open_rgb(data)
    buf = io.BytesIO()
    # No exif=/icc_profile= passed => output carries no metadata; fixed quality +
    # subsampling => a content-independent, producer-independent DQT.
    im.save(buf, "JPEG", quality=CANONICAL_QUALITY,
            subsampling=CANONICAL_SUBSAMPLING, optimize=True)
    out = buf.getvalue()

    _check_perceptual(data, out)
    return out


def _check_perceptual(original: bytes, scrubbed: bytes) -> None:
    """Fail-closed content gate: pHash Hamming distance must stay within the
    calibrated threshold, else the lossy re-encode mangled the image."""
    try:
        import imagehash
    except ImportError as e:  # pragma: no cover - imagehash is a pinned dep
        raise ScrubError("imagehash required for JPEG F3 content verification") from e
    h_in = imagehash.phash(_open_rgb(original))
    h_out = imagehash.phash(_open_rgb(scrubbed))
    distance = h_in - h_out
    if distance > PHASH_MAX_DISTANCE:
        raise ContentError(
            f"JPEG F3 re-encode diverged perceptually: pHash distance {distance} "
            f"> {PHASH_MAX_DISTANCE}")


def residuals(data: bytes) -> list[str]:
    """Re-walk scrubbed output and report anything that shouldn't survive F3: any
    metadata segment, a stray JFXX/Adobe, or trailer bytes. The canonical JFIF
    APP0 that libjpeg emits is allowed. Empty list = clean."""
    out: list[str] = []
    try:
        structure = seg.walk(data)
    except ParseError as e:
        return [f"unparseable F3 output: {e}"]
    for s in structure.segments:
        if s.is_content:
            continue
        if s.kind == "app0_jfif":
            continue
        out.append(f"residual {s.kind} segment at {s.offset}")
    if structure.trailer:
        out.append(f"{len(structure.trailer)} trailer byte(s) after EOI")
    return out
