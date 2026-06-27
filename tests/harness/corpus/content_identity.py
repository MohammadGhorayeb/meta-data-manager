"""Canonical-content extraction + equality check (Part B §6.3).

Admits/rejects corpus pairs and asserts the synthetic invariant. Exact-hash for
F1/F2 (content substream, raw pixels, or PCM); perceptual-equality for F3.
"""
from __future__ import annotations

import hashlib
import subprocess


def canonical_content(path: str, modality: str, plugin=None) -> bytes:
    if modality == "bytes":
        if plugin is not None:
            return plugin.canonical_content(path)
        with open(path, "rb") as f:
            return f.read()
    if modality == "image":
        from PIL import Image  # decode to raw pixels in a fixed mode
        with Image.open(path) as im:
            return im.convert("RGBA").tobytes()
    if modality == "audio":
        p = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", path, "-f", "s16le", "-ac", "2", "-ar", "44100", "-"],
            capture_output=True,
        )
        return p.stdout
    raise ValueError(f"unknown modality {modality!r}")


def _digest(path: str, modality: str, plugin=None) -> bytes:
    return hashlib.sha256(canonical_content(path, modality, plugin)).digest()


def same_content(a: str, b: str, modality: str, plugin=None,
                 perceptual_threshold: float | None = None) -> bool:
    """Exact-hash equality for F1/F2; perceptual equality for F3 (when a
    perceptual_threshold is supplied)."""
    if perceptual_threshold is None:
        return _digest(a, modality, plugin) == _digest(b, modality, plugin)
    from ..oracle import perceptual  # lazy: only F3 needs imagehash/fpcalc
    rep = perceptual.check_preserved(modality, a, b, perceptual_threshold)
    return bool(rep.preserved)
