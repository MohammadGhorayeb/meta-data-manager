"""PNG F2 — canonical lossless re-encode. The A2 defense, at zero quality cost.

PNG is lossless, so its producer fingerprint is not in the pixels (as JPEG's DQT
is) but in *how* the encoder packed them: the zlib deflate level/strategy, the
per-row filter choices, IDAT chunking, and chunk ordering. F2 decodes the pixels
and re-encodes them through ONE canonical pipeline (fixed compression, Pillow's
single-IDAT writer, canonical chunk order, no metadata), so every output has the
same structure regardless of producer — the deflate fingerprint is normalized
away. Unlike JPEG F3, this costs nothing: the pixels are bit-identical (F2), so
A2@F2 can pass *losslessly* — the headline P1 result (p1 plan W7 / experiment E5).

Metadata: only render-affecting info (transparency, gamma) is carried forward;
text/time/exif/icc/phys and every other ancillary locus is dropped. We do NOT
pass a pnginfo object, so no tEXt/zTXt/iTXt is written.

Fail-closed: if the re-encode is not pixel-identical (mode/size/RGBA raster), we
raise ContentError rather than ship an image we cannot vouch for.
"""
from __future__ import annotations

import io

from ...errors import ContentError
from . import chunks as ck

# Fixed => producer-independent deflate. Max compression, deterministic.
_COMPRESS_LEVEL = 9
# Only these info keys survive into the re-encode (render-affecting, not identifying).
_RENDER_INFO = ("transparency", "gamma")


def _open(data: bytes):
    from PIL import Image
    im = Image.open(io.BytesIO(data))
    im.load()
    return im


def scrub(data: bytes) -> bytes:
    """Decode + canonically re-encode the pixels losslessly; strip metadata.
    Raises ContentError if the round-trip is not pixel-identical."""
    # Parse first so a non-PNG / malformed input (or bad CRC) fails closed with
    # our error, before we hand anything to Pillow.
    ck.walk(data)

    im = _open(data)
    # Keep only render-affecting info; drop text/exif/icc/phys/time/etc.
    keep = {k: im.info[k] for k in _RENDER_INFO if k in im.info}
    im.info = keep

    buf = io.BytesIO()
    save_kwargs = {"optimize": False, "compress_level": _COMPRESS_LEVEL}
    if "transparency" in keep:
        save_kwargs["transparency"] = keep["transparency"]
    im.save(buf, "PNG", **save_kwargs)
    out = buf.getvalue()

    _check_lossless(data, out)
    return out


def _check_lossless(original: bytes, scrubbed: bytes) -> None:
    """F2 promises pixel-identical output. Compare in RGBA (captures colour +
    alpha across all modes)."""
    a = _open(original).convert("RGBA")
    b = _open(scrubbed).convert("RGBA")
    if a.size != b.size or a.tobytes() != b.tobytes():
        raise ContentError(
            f"PNG F2 re-encode changed pixels (size {a.size}->{b.size})")


def residuals(data: bytes) -> list[str]:
    """Re-walk scrubbed output and report anything that shouldn't survive F2: a
    metadata chunk or trailer bytes. The render-affecting keep-list (same as F1)
    is allowed. Empty list = clean."""
    from .f1 import KEEP
    out: list[str] = []
    structure = ck.walk(data)
    for c in structure.chunks:
        if c.ctype not in KEEP:
            out.append(f"residual {c.ctype} chunk at {c.offset}")
    if structure.trailer:
        out.append(f"{len(structure.trailer)} trailer byte(s) after IEND")
    return out
