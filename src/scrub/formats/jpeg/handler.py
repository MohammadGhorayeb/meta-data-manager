"""JPEG handler — registers magic + dispatches fidelity to the tier modules.

F1 is implemented (f1.py). F2 (lossless re-Huffman via jpegtran, DQT residual)
and F3 (canonical lossy re-encode) arrive in M3/M4; they raise FidelityError
until then so the CLI fails closed rather than emitting an unscrubbed file.
"""
from __future__ import annotations

from ...errors import FidelityError
from ..base import BaseHandler
from . import f1

# SOI + first marker byte. FF D8 FF starts every JPEG/JFIF/Exif file.
JPEG_MAGIC = (b"\xff\xd8\xff",)


class JpegHandler(BaseHandler):
    format_id = "jpeg"
    magic = JPEG_MAGIC
    fidelities = ("F1",)   # F2/F3 land in M3/M4

    def scrub_f1(self, data: bytes) -> bytes:
        return f1.scrub(data)

    def verify(self, data: bytes, fidelity: str) -> list[str]:
        """Post-scrub residual check (fail-closed guard for the CLI). F1 only for
        now; F2/F3 residual checks arrive with those handlers."""
        if fidelity == "F1":
            return f1.residuals(data)
        return []

    def scrub_f2(self, data: bytes) -> bytes:
        raise FidelityError("jpeg F2 (lossless re-Huffman) not implemented (M3)")

    def scrub_f3(self, data: bytes) -> bytes:
        raise FidelityError("jpeg F3 (canonical re-encode) not implemented (M4)")
