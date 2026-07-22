"""JPEG handler — registers magic + dispatches fidelity to the tier modules.

All three tiers are implemented: F1 (bit-preserving segment surgery), F2
(lossless re-Huffman via jpegtran, DQT residual), and F3 (canonical lossy
re-encode that normalizes the DQT away — the A2 defense).
"""
from __future__ import annotations

from ..base import BaseHandler
from . import f1, f2, f3

# SOI + first marker byte. FF D8 FF starts every JPEG/JFIF/Exif file.
JPEG_MAGIC = (b"\xff\xd8\xff",)


class JpegHandler(BaseHandler):
    format_id = "jpeg"
    magic = JPEG_MAGIC
    fidelities = ("F1", "F2", "F3")

    def scrub_f1(self, data: bytes) -> bytes:
        return f1.scrub(data)

    def scrub_f2(self, data: bytes) -> bytes:
        return f2.scrub(data)

    def scrub_f3(self, data: bytes) -> bytes:
        return f3.scrub(data)

    def verify(self, data: bytes, fidelity: str) -> list[str]:
        """Post-scrub residual check (fail-closed guard for the CLI)."""
        if fidelity == "F1":
            return f1.residuals(data)
        if fidelity == "F2":
            return f2.residuals(data)
        if fidelity == "F3":
            return f3.residuals(data)
        return []
