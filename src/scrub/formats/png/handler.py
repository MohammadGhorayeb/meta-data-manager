"""PNG handler — registers magic + dispatches fidelity to the tier modules.

F1 (bit-preserving chunk keep-list) and F2 (canonical lossless re-encode — the
zero-cost A2 defense) are implemented. F3 is not_applicable: PNG is lossless, so
a lossy tier adds nothing over F2 (p1 plan W7).
"""
from __future__ import annotations

from ...errors import FidelityError
from ..base import BaseHandler
from . import f1, f2
from . import inspect as _inspect

# PNG 8-byte signature.
PNG_MAGIC = (b"\x89PNG\r\n\x1a\n",)


class PngHandler(BaseHandler):
    format_id = "png"
    magic = PNG_MAGIC
    fidelities = ("F1", "F2")   # F3 not_applicable (lossless format)

    def scrub_f1(self, data: bytes) -> bytes:
        return f1.scrub(data)

    def scrub_f2(self, data: bytes) -> bytes:
        return f2.scrub(data)

    def scrub_f3(self, data: bytes) -> bytes:
        raise FidelityError("png F3 is not_applicable (lossless format; use F2)")

    def verify(self, data: bytes, fidelity: str) -> list[str]:
        """Post-scrub residual check (fail-closed guard for the CLI)."""
        if fidelity == "F1":
            return f1.residuals(data)
        if fidelity == "F2":
            return f2.residuals(data)
        return []

    def describe(self, data: bytes) -> dict[str, str]:
        """What this file's metadata says — for the scrub report, never for a tier.

        Coverage is exactly this handler's coverage, which is the point: a locus we
        cannot model is absent from the report *and* from the scrub, so the report
        must never be read as "nothing else was in the file".
        """
        return _inspect.describe(data)
