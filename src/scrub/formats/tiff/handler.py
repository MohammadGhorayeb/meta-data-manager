"""Plain TIFF handler (F1): scans, exports, conversions.

Registered after camera RAW, which shares the `II*\\0` / `MM\\0*` prefix and claims a
file only when it says it is a raw (a raw magic, DNGVersion, Canon's signature, a
sensor-mosaic image). Everything else with a TIFF header is a plain TIFF.
"""
from __future__ import annotations

from ..base import BaseHandler
from . import f1


class TiffHandler(BaseHandler):
    format_id = "tiff"
    magic = (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")
    fidelities = ("F1",)

    def scrub_f1(self, data: bytes) -> bytes:
        return f1.scrub(data)

    def verify(self, data: bytes, fidelity: str) -> list[str]:
        return f1.residuals(data) if fidelity == "F1" else []

    def describe(self, data: bytes) -> dict[str, str]:
        return f1.describe(data)

    def kept(self, data: bytes, fidelity: str) -> list[str]:
        return ["The colour profile stays, because the picture's colours depend on "
                "it: a standard one (sRGB, Display P3...) exactly as it was, any other "
                "with the details of who made it removed.",
                "Nothing in a TIFF can move, so a removed value keeps its length as "
                "zeros."]
