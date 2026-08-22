"""PDF handler — registers magic + dispatches fidelity to the tier modules.

F1 and F2. F3 (rasterise) is Phase 3 M5; it is the one tier whose contribution is
not ours — it is MAT2's and Dangerzone's technique — and it costs the document its
selectable text, so it is offered rather than assumed.

Magic note: ISO 32000 §7.5.2 permits up to 1024 bytes before `%PDF-`, which would put
the magic outside any fixed header window. The walker refuses such files outright —
their offsets are header-relative, so a scrubber that ignored the prefix would resolve
every object to the wrong place — so matching the prefix at offset 0 is the whole job.
"""
from __future__ import annotations

from ...errors import FidelityError
from ..base import BaseHandler
from . import f1, f2

PDF_MAGIC = (b"%PDF-",)


class PdfHandler(BaseHandler):
    format_id = "pdf"
    magic = PDF_MAGIC
    fidelities = ("F1", "F2")

    def scrub_f1(self, data: bytes) -> bytes:
        return f1.scrub(data)

    def scrub_f2(self, data: bytes) -> bytes:
        return f2.scrub(data)

    def scrub_f3(self, data: bytes) -> bytes:
        raise FidelityError("pdf F3 is not implemented yet (Phase 3 M5)")

    def verify(self, data: bytes, fidelity: str) -> list[str]:
        if fidelity == "F1":
            return f1.residuals(data)
        return f2.residuals(data) if fidelity == "F2" else []
