"""JPEG handler — registers magic + dispatches fidelity to the tier modules.

F1 (bit-preserving segment surgery) and F2 (lossless re-Huffman via jpegtran,
DQT residual) are implemented. F3 (canonical lossy re-encode) arrives in M4; it
raises FidelityError until then so the CLI fails closed rather than emitting an
unscrubbed file.
"""
from __future__ import annotations

from ...errors import FidelityError
from ..base import BaseHandler
from . import f1, f2

# SOI + first marker byte. FF D8 FF starts every JPEG/JFIF/Exif file.
JPEG_MAGIC = (b"\xff\xd8\xff",)


class JpegHandler(BaseHandler):
    format_id = "jpeg"
    magic = JPEG_MAGIC
    fidelities = ("F1", "F2")   # F3 lands in M4

    def scrub_f1(self, data: bytes) -> bytes:
        return f1.scrub(data)

    def scrub_f2(self, data: bytes) -> bytes:
        return f2.scrub(data)

    def verify(self, data: bytes, fidelity: str) -> list[str]:
        """Post-scrub residual check (fail-closed guard for the CLI). F3's
        residual check arrives with that handler (M4)."""
        if fidelity == "F1":
            return f1.residuals(data)
        if fidelity == "F2":
            return f2.residuals(data)
        return []

    def scrub_f3(self, data: bytes) -> bytes:
        raise FidelityError("jpeg F3 (canonical re-encode) not implemented (M4)")
