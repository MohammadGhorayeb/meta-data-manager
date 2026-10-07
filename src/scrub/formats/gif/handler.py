"""GIF handler (F1): still and animated."""
from __future__ import annotations

from ..base import BaseHandler
from . import f1


class GifHandler(BaseHandler):
    format_id = "gif"
    magic = (b"GIF87a", b"GIF89a")
    fidelities = ("F1",)

    def scrub_f1(self, data: bytes) -> bytes:
        return f1.scrub(data)

    def verify(self, data: bytes, fidelity: str) -> list[str]:
        return f1.residuals(data) if fidelity == "F1" else []

    def describe(self, data: bytes) -> dict[str, str]:
        return f1.describe(data)

    def kept(self, data: bytes, fidelity: str) -> list[str]:
        return ["Every frame stays exactly as it was compressed, with its timing, "
                "transparency and the animation's loop."]
