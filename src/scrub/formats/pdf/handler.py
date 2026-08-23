"""PDF handler — registers magic + dispatches fidelity to the tier modules.

All three tiers. F3 (rasterise) is the one whose contribution is not ours — it is
MAT2's and Dangerzone's technique — and it costs the document its selectable text, so
it is offered rather than assumed.

Magic note: ISO 32000 §7.5.2 permits up to 1024 bytes before `%PDF-`, which would put
the magic outside any fixed header window. The walker refuses such files outright —
their offsets are header-relative, so a scrubber that ignored the prefix would resolve
every object to the wrong place — so matching the prefix at offset 0 is the whole job.
"""
from __future__ import annotations

from ..base import BaseHandler
from . import f1, f2, f3, redaction

PDF_MAGIC = (b"%PDF-",)


class PdfHandler(BaseHandler):
    format_id = "pdf"
    magic = PDF_MAGIC
    fidelities = ("F1", "F2", "F3")

    def scrub_f1(self, data: bytes) -> bytes:
        return f1.scrub(data)

    def scrub_f2(self, data: bytes) -> bytes:
        return f2.scrub(data)

    def scrub_f3(self, data: bytes) -> bytes:
        return f3.scrub(data)

    def verify(self, data: bytes, fidelity: str) -> list[str]:
        return {"F1": f1.residuals, "F2": f2.residuals,
                "F3": f3.residuals}[fidelity](data)

    def advise(self, data: bytes) -> list[str]:
        """Redaction risks in the **input** — a warning, never a failure.

        Every tier here preserves content, so text hidden under a black box survives
        the scrub exactly as faithfully as visible text does. A user who reads
        "scrubbed" as "redacted" is the one this tool could most easily mislead, and
        this is the only place they get told. It does not fix anything; see
        `redaction.py` for why fixing is out of scope.
        """
        return [f"possible redaction failure — {note}"
                for note in redaction.warnings(data)]
