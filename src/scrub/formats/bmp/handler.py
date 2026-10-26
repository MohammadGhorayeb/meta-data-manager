"""BMP handler (F1): Windows and OS/2 1.x bitmaps, uncompressed, bitfields or RLE."""
from __future__ import annotations

from ..base import BaseHandler
from . import f1


class BmpHandler(BaseHandler):
    format_id = "bmp"
    magic = (b"BM",)
    fidelities = ("F1",)

    def claims(self, data: bytes) -> bool:
        # `BM` is two bytes a text file can begin with; the header size says more.
        return f1.is_bmp(data)

    def scrub_f1(self, data: bytes) -> bytes:
        return f1.scrub(data)

    def verify(self, data: bytes, fidelity: str) -> list[str]:
        return f1.residuals(data) if fidelity == "F1" else []

    def describe(self, data: bytes) -> dict[str, str]:
        return f1.describe(data)

    def kept(self, data: bytes, fidelity: str) -> list[str]:
        out = ["Every pixel stays exactly as it was stored, with the palette's "
               "colours, and so does the picture's layout (header version, row "
               "order), which can tell the program that saved it apart from others."]
        if "ICC profile" in f1.describe(data):
            out.append("The colour profile stays: a standard one exactly as it was, "
                       "any other with the details of who made it removed.")
        return out
