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

import pikepdf

from ...errors import ParseError
from ..base import BaseHandler
from . import f1, f2, f3, redaction
from . import inspect as _inspect

PDF_MAGIC = (b"%PDF-",)


class PdfHandler(BaseHandler):
    format_id = "pdf"
    magic = PDF_MAGIC
    fidelities = ("F1", "F2", "F3")

    def _tier(self, fn, data: bytes) -> bytes:
        """Run a tier, converting pikepdf's own exception into a ParseError.

        Opening the document without recovery is not enough on its own: pikepdf
        defers most parsing, so a damaged object only raises when something reaches
        it — `read_bytes()` on an unfilterable stream, deep in the object walk or in
        the content-identity check that re-reads the input afterwards. Those escaped
        as a bare `PdfError`, which `cli.py` classifies as **unexpected** (exit 1)
        rather than as a parse failure (exit 4). Fail-closed still held — no output
        was written — but this project documents its exit codes as stable and asserts
        on them, so the classification is part of the contract.

        Wrapped here, at the handler boundary, rather than at each call site: the
        first attempt patched the two spots the fuzzer had reached and missed a third
        in `_assert_content_identity`, which is exactly how this class of fix rots.
        Found by fuzzing (`tests/scrub/test_fuzz.py`).
        """
        try:
            return fn(data)
        except pikepdf.PdfError as exc:
            raise ParseError(f"PDF: damaged object reached during scrub: {exc}") \
                from exc

    def scrub_f1(self, data: bytes) -> bytes:
        return self._tier(f1.scrub, data)

    def scrub_f2(self, data: bytes) -> bytes:
        return self._tier(f2.scrub, data)

    def scrub_f3(self, data: bytes) -> bytes:
        return self._tier(f3.scrub, data)

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

    def describe(self, data: bytes) -> dict[str, str]:
        """What this file's metadata says — for the scrub report, never for a tier.

        Coverage is exactly this handler's coverage, which is the point: a locus we
        cannot model is absent from the report *and* from the scrub, so the report
        must never be read as "nothing else was in the file".
        """
        return _inspect.describe(data)
