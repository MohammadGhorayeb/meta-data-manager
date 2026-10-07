"""WebP handler (F1): still, lossless and animated."""
from __future__ import annotations

from ..base import BaseHandler
from . import f1


class WebpHandler(BaseHandler):
    format_id = "webp"
    magic = (b"RIFF",)
    fidelities = ("F1",)

    def claims(self, data: bytes) -> bool:
        # `RIFF` opens WAV and AVI too; the form type says which.
        return f1.is_webp(data)

    def scrub_f1(self, data: bytes) -> bytes:
        return f1.scrub(data)

    def verify(self, data: bytes, fidelity: str) -> list[str]:
        return f1.residuals(data) if fidelity == "F1" else []

    def describe(self, data: bytes) -> dict[str, str]:
        return f1.describe(data)

    def kept(self, data: bytes, fidelity: str) -> list[str]:
        return ["The picture is copied exactly as it was compressed, so a lossy one "
                "keeps the traces of the program that compressed it.",
                "The colour profile stays: a standard one exactly as it was, any "
                "other with the details of who made it removed."]
