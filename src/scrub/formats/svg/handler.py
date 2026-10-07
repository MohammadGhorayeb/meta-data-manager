"""SVG handler (F1): plain and gzipped (.svgz).

SVG has no binary magic: it is text that starts with `<` (after a byte-order mark
or whitespace), or a gzip stream. The prefix test is only a gate; `claims()` reads
far enough to find an `<svg>` root, so HTML, other XML and other gzip files are
declined.
"""
from __future__ import annotations

from ..base import BaseHandler
from . import f1


class SvgHandler(BaseHandler):
    format_id = "svg"
    magic = (b"<", b"\xef\xbb\xbf", b"\x1f\x8b")
    fidelities = ("F1",)

    def matches(self, header: bytes) -> bool:
        return header[:2] == b"\x1f\x8b" or header.lstrip(
            b"\xef\xbb\xbf \t\r\n").startswith(b"<")

    def claims(self, data: bytes) -> bool:
        return f1.is_svg(data)

    def scrub_f1(self, data: bytes) -> bytes:
        return f1.scrub(data)

    def verify(self, data: bytes, fidelity: str) -> list[str]:
        return f1.residuals(data) if fidelity == "F1" else []

    def advise(self, data: bytes) -> list[str]:
        return f1.advise(data)

    def describe(self, data: bytes) -> dict[str, str]:
        return f1.describe(data)

    def kept(self, data: bytes, fidelity: str) -> list[str]:
        return ["Everything that draws stays exactly as it was written; pictures "
                "inside the drawing are cleaned by their own format's rules and put "
                "back in place."]
