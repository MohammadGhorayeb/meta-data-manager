"""DOCX handler — identification and the refusal list.

Registered in `dispatch.py` as of M10, when F1 landed — deliberately not before,
so the tool never advertised a format it could not actually scrub.

Identification is the part that is genuinely new here. Every format so far has been
decided by its magic prefix; `PK\\x03\\x04` decides nothing, because XLSX, PPTX, ODT,
EPUB, JAR and a plain archive all begin with it. So `matches()` is only a cheap
gate and `claims()` does the real work by opening the central directory and looking
for the two parts that make a package a DOCX. That is exactly the two-stage design
the dispatcher already has for the ID3-prefixed audio formats, used harder.
"""
from __future__ import annotations

from ...errors import ParseError
from ..base import BaseHandler
from ..ooxml import opc
from . import f1, f2, f3

ZIP_MAGIC = (b"PK\x03\x04",)


class DocxHandler(BaseHandler):
    format_id = "docx"
    magic = ZIP_MAGIC
    fidelities = ("F1", "F2", "F3")

    def matches(self, header: bytes) -> bool:
        # A cheap gate only. An empty archive starts `PK\x05\x06` and a spanned one
        # `PK\x07\x08`; neither can be a DOCX, so the local-header prefix is the
        # right (and still insufficient) filter.
        return header.startswith(b"PK\x03\x04")

    def claims(self, data: bytes) -> bool:
        return opc.looks_like(data, "docx")

    def preflight(self, data: bytes) -> list[str]:
        """Reasons this package cannot be fully scrubbed, or [].

        Separate from `verify()` because these are properties of the *input* that
        make a scrub impossible, not residuals in an output. Fail-closed means the
        caller refuses on a non-empty list.
        """
        try:
            pkg = opc.parse(data)
        except ParseError as exc:
            return [str(exc)]
        return pkg.refusals()

    def scrub_f1(self, data: bytes) -> bytes:
        return f1.scrub(data)

    def scrub_f2(self, data: bytes) -> bytes:
        return f2.scrub(data)

    def scrub_f3(self, data: bytes) -> bytes:
        return f3.scrub(data)

    def verify(self, data: bytes, fidelity: str) -> list[str]:
        """Scrub FAILURES only. What F1 deliberately preserves is reported by
        `advisories()` through a separate path, on the W7 precedent — a property of
        the input is not a scrub failure, and refusing over one would mean refusing
        to clean any real document."""
        if fidelity == "F1":
            return f1.residuals(data)
        if fidelity in ("F2", "F3"):
            return f2.residuals(data)
        return []

    def advisories(self, data: bytes, fidelity: str = "F1") -> list[str]:
        if fidelity == "F3":
            return f3.advisories(data)
        if fidelity == "F2":
            return f2.advisories(data)
        return f1.advisories(data)
