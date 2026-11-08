"""JPEG handler — registers magic + dispatches fidelity to the tier modules.

All three tiers are implemented: F1 (bit-preserving segment surgery), F2
(lossless re-Huffman via jpegtran, DQT residual), and F3 (canonical lossy
re-encode that normalizes the DQT away — the A2 defense).
"""
from __future__ import annotations

from ..base import BaseHandler
from . import f1, f2, f3
from . import inspect as _inspect
from . import segments as seg

# SOI + first marker byte. FF D8 FF starts every JPEG/JFIF/Exif file.
JPEG_MAGIC = (b"\xff\xd8\xff",)


class JpegHandler(BaseHandler):
    format_id = "jpeg"
    magic = JPEG_MAGIC
    fidelities = ("F1", "F2", "F3")
    # Peak bytes per PIXEL of the levels that decode the picture, measured on flat,
    # drawn and noisy pictures: F2 18-21.6, F3 27-31.5, whatever the file's size (a
    # flat picture's file is 1,900x smaller than its need; a noisy one's 68x). About
    # 15% margin; held by test_memory_preflight.py.
    bytes_per_pixel = {"F2": 24, "F3": 36}

    def memory_need(self, data: bytes, fidelity: str) -> int | None:
        rate = self.bytes_per_pixel.get(fidelity)
        size = seg.frame_size(data) if rate else None
        if not size or size[0] * size[1] > seg.decode_limit():
            return None        # past the limit no amount of memory helps: the level
        return size[0] * size[1] * rate  # refuses with the reason (check_decodable_size)

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

    def describe(self, data: bytes) -> dict[str, str]:
        """What this file's metadata says — for the scrub report, never for a tier.

        Coverage is exactly this handler's coverage, which is the point: a locus we
        cannot model is absent from the report *and* from the scrub, so the report
        must never be read as "nothing else was in the file".
        """
        return _inspect.describe(data)
