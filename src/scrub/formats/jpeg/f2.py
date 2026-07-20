"""JPEG F2 — lossless re-encode via jpegtran (W5).

F2 rewrites the *entropy coding* while leaving the quantized DCT coefficients
untouched, so decoded pixels are bit-identical to the input (the F2 contract).
This is exactly what ``jpegtran -optimize`` does: it re-derives optimal Huffman
tables from the existing coefficients and re-emits the stream. We add
``-copy none`` (drop every APPn/COM marker — all metadata) and ``-restart 0``
(strip restart markers, an encoder tell) so the output is both metadata-free and
structurally normalized.

Why shell out (CLAUDE.md tool policy): jpegtran is called as a subprocess and
never linked, so its libjpeg-turbo licence does not bind the shipped tool. The
alternative — decode coefficients and re-Huffman in pure Python — is an E3-informed
follow-up (p1 plan §4 decision); jpegtran is the reference lossless optimizer and
what W9 benchmarks against, so F2 v1 uses it.

Fingerprint normalization achieved here:
  - DHT regenerated optimally  -> kills the Huffman-table fingerprint.
  - restart markers stripped   -> kills the DRI/restart-interval encoder tell.
  - all APPn/COM dropped        -> no metadata locus survives (A1@F2).
  - libjpeg emits one canonical JFIF APP0 (normalize-toward-the-crowd, W8).

Documented F2 residual (keeps A2 red — Kornblum, verified empirically by E3):
  - DQT survives by definition: quantization cannot change losslessly, so the
    source's quantization tables — the strongest producer fingerprint — remain.
  - Scan mode (baseline vs progressive) is preserved from the source; full
    scan-script normalization is a later refinement (p1 plan §4).

Fail-closed (CLAUDE.md hard-constraint #2): if jpegtran is absent, errors, or the
re-encode is not pixel-identical to the input, we raise rather than emit a file
we cannot vouch for.
"""
from __future__ import annotations

import io
import shutil
import subprocess

from ...errors import ContentError, ParseError, ScrubError
from . import segments as seg

# jpegtran invocation. Order is fixed for determinism/reproducibility.
#   -optimize   re-derive optimal Huffman tables (lossless; coefficients intact)
#   -copy none  drop all APPn/COM markers (every metadata locus)
#   -restart 0  emit no restart markers (normalize the DRI encoder tell)
_JPEGTRAN_ARGS = ("-optimize", "-copy", "none", "-restart", "0")


def _jpegtran() -> str:
    path = shutil.which("jpegtran")
    if path is None:
        raise ScrubError(
            "jpegtran not found on PATH; JPEG F2 requires libjpeg-turbo "
            "(brew install jpeg-turbo). F2 fails closed rather than fall back.")
    return path


def _decode_pixels(data: bytes) -> tuple[tuple[int, int], bytes]:
    """Decoded RGB pixels + size, the F2 content-identity primitive. Imported
    lazily so a Pillow-less environment can still import this module."""
    from PIL import Image
    import warnings
    with warnings.catch_warnings():
        # The torture/base corpus carries a fake MPF marker that Pillow flags as
        # a malformed MPO; irrelevant to pixel identity.
        warnings.filterwarnings("ignore", ".*malformed MPO.*")
        with Image.open(io.BytesIO(data)) as im:
            rgb = im.convert("RGB")
            return rgb.size, rgb.tobytes()


def scrub(data: bytes) -> bytes:
    """Lossless re-encode: strip metadata + normalize entropy coding, pixels
    intact. Raises ContentError if the round-trip is not pixel-identical."""
    # Parse first so a non-JPEG / malformed input fails closed with our error,
    # not an opaque jpegtran exit.
    seg.walk(data)

    proc = subprocess.run(
        [_jpegtran(), *_JPEGTRAN_ARGS],
        input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise ScrubError(
            f"jpegtran failed (exit {proc.returncode}): "
            f"{proc.stderr.decode('utf-8', 'replace').strip()}")
    out = proc.stdout

    # Fail-closed content check: F2 promises decoded pixels bit-identical.
    in_size, in_px = _decode_pixels(data)
    out_size, out_px = _decode_pixels(out)
    if in_size != out_size or in_px != out_px:
        raise ContentError(
            "JPEG F2 re-encode changed decoded pixels "
            f"(size {in_size}->{out_size}, "
            f"{'pixels differ' if in_px != out_px else 'size differs'})")
    return out


# Segment kinds F2 output may legitimately contain: image-defining markers plus
# the single canonical JFIF APP0 that libjpeg emits. Anything else is a residual.
def residuals(data: bytes) -> list[str]:
    """Re-walk scrubbed output and report anything that shouldn't survive F2: a
    metadata segment, a stray JFXX/Adobe, restart-interval DRI, or trailer bytes.
    Empty list = clean. Called by the CLI as a fail-closed post-condition."""
    out: list[str] = []
    try:
        structure = seg.walk(data)
    except ParseError as e:
        return [f"unparseable F2 output: {e}"]
    for s in structure.segments:
        if s.is_content:
            if s.kind == "dri":
                out.append(f"restart-interval DRI survived at {s.offset}")
            continue
        if s.kind == "app0_jfif":
            continue  # libjpeg's canonical JFIF — allowed
        out.append(f"residual {s.kind} segment at {s.offset}")
    if structure.trailer:
        out.append(f"{len(structure.trailer)} trailer byte(s) after EOI")
    return out
