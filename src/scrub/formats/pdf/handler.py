"""PDF handler — registers magic + dispatches fidelity to the tier modules.

F1 only for now. F2 (canonical re-serialisation plus content-stream canonicalisation)
and F3 (rasterise) are Phase 3 M4/M5; until the A2 measurement in M3 says which
channel actually leaks, an F2 built by guesswork would publish a residual that is an
artefact of unfinished work rather than a measured limit.

Magic note: ISO 32000 §7.5.2 permits up to 1024 bytes before `%PDF-`, which would put
the magic outside any fixed header window. The walker refuses such files outright —
their offsets are header-relative, so a scrubber that ignored the prefix would resolve
every object to the wrong place — so matching the prefix at offset 0 is the whole job.
"""
from __future__ import annotations

from ...errors import FidelityError
from ..base import BaseHandler
from . import f1

PDF_MAGIC = (b"%PDF-",)


class PdfHandler(BaseHandler):
    format_id = "pdf"
    magic = PDF_MAGIC
    fidelities = ("F1",)

    def scrub_f1(self, data: bytes) -> bytes:
        return f1.scrub(data)

    def scrub_f2(self, data: bytes) -> bytes:
        raise FidelityError("pdf F2 is not implemented yet (Phase 3 M4)")

    def scrub_f3(self, data: bytes) -> bytes:
        raise FidelityError("pdf F3 is not implemented yet (Phase 3 M5)")

    def verify(self, data: bytes, fidelity: str) -> list[str]:
        return f1.residuals(data) if fidelity == "F1" else []
