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

from ...errors import ContentError, ParseError, ResourceError
from . import orientation
from . import segments as seg

CANONICAL_QUALITY = 90
CANONICAL_SUBSAMPLING = "4:2:0"          # Pillow accepts the "4:2:0" spelling
# Content gate: peak signal-to-noise ratio between the photo as displayed and its
# re-encode. Replaced a perceptual-hash gate (distance <= 6) in November, measured:
# the hash refused honest re-encodes of near-flat photos -- a sky at distance 24, a
# night shot at 22, at 51.5 and 46.9 dB -- because a flat picture's low-frequency
# coefficients sit at their median and noise flips the bits; and it PASSED a
# red/blue swap (4), grayscale (0), a 30% darkening (0), an 8-pixel shift (2) --
# the colour-space slips a re-encoder makes. Honest re-encodes measured 28.4-51.5 dB
# (28.4 is pure random noise), every geometric or colour mangle 4.6-20 dB; 25 sits in
# the gap (provisional,
# docs/november_plan.md N3).
MIN_PSNR_DB = 25.0


def _open_rgb(data: bytes):
    """Decode to a BARE RGB image — no carried info dict. Critical: Pillow stashes
    a source JPEG's COM/EXIF/ICC in ``im.info`` and re-emits them on save (a COM
    leak F3 must not ship), so we rebuild from raw pixels to drop all of it."""
    from PIL import Image, UnidentifiedImageError
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", ".*malformed MPO.*")
        try:
            with Image.open(io.BytesIO(data)) as im:
                rgb = im.convert("RGB")
                return Image.frombytes("RGB", rgb.size, rgb.tobytes())
        except Image.DecompressionBombError as exc:
            # Not an OSError: it escaped as "unexpected" (November catalogue).
            raise ResourceError(f"JPEG: too large to decode ({exc})") from None
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            # A JPEG whose entropy-coded scan is damaged parses far enough for the
            # segment walker to accept it and then fails in the decoder. Pillow's
            # own exception would reach the CLI as *unexpected* (exit 1); a file we
            # cannot decode is a parse failure (exit 4). Found by fuzzing.
            raise ParseError(f"JPEG: cannot decode the image ({exc})") from exc


def scrub(data: bytes) -> bytes:
    """Decode and re-encode with the canonical encoder; strip all metadata.
    Raises ContentError if the re-encode diverges perceptually past the gate."""
    # Parse first so a non-JPEG / malformed input fails closed with our error.
    seg.walk(data)
    seg.check_decodable_size(data, "F3")

    # A rotated photo is re-encoded the way it is displayed, so it needs no tag:
    # the EXIF orientation is applied to the pixels, as MAT2 does (orientation.py).
    im = orientation.apply(_open_rgb(data), orientation.read(data))
    buf = io.BytesIO()
    # No exif=/icc_profile= passed => output carries no metadata; fixed quality +
    # subsampling => a content-independent, producer-independent DQT.
    im.save(buf, "JPEG", quality=CANONICAL_QUALITY,
            subsampling=CANONICAL_SUBSAMPLING, optimize=True)
    out = buf.getvalue()

    _check_perceptual(data, out)
    return out


def psnr(a, b) -> float:
    """PSNR in dB of two RGB pictures (infinity when identical, minus infinity when
    their sizes differ). Pillow's difference and band statistics run in C and cost
    one 8-bit picture, not the float arrays that would need gigabytes at the size
    limit."""
    import math

    from PIL import ImageChops, ImageStat
    if a.size != b.size:
        return -math.inf
    sum2 = sum(ImageStat.Stat(ImageChops.difference(a, b)).sum2)
    if sum2 == 0:
        return math.inf
    return 10 * math.log10(255 ** 2 * a.width * a.height * 3 / sum2)


def _check_perceptual(original: bytes, scrubbed: bytes) -> None:
    """Fail-closed content gate: the re-encode must match the photo as displayed
    (orientation applied) to MIN_PSNR_DB, else the lossy re-encode mangled it."""
    shown = orientation.apply(_open_rgb(original), orientation.read(original))
    db = psnr(shown, _open_rgb(scrubbed))
    if db < MIN_PSNR_DB:
        raise ContentError(
            f"JPEG F3 re-encode diverged from the original: PSNR {db:.1f} dB "
            f"< {MIN_PSNR_DB:g} dB")


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
