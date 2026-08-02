"""FLAC handler — registers magic + dispatches fidelity to the tier modules.

F1 (bit-preserving block strip) and F2 (canonical lossless re-encode — the zero-cost
A2 defense, as with PNG). F3 is not_applicable: FLAC is lossless, so a lossy tier
would only destroy audio to buy nothing F2 has not already bought.

Magic note: a FLAC file may carry a leading ID3v2 tag (out of spec, common in the
wild), so `fLaC` is not always at offset 0 — the handler matches both, and the
walker accounts for the tag as a metadata region to drop.
"""
from __future__ import annotations

from ...errors import FidelityError, ParseError
from ..base import BaseHandler
from . import f1, f2
from . import walker as w

FLAC_MAGIC = (b"fLaC",)


class FlacHandler(BaseHandler):
    format_id = "flac"
    magic = FLAC_MAGIC
    fidelities = ("F1", "F2")   # F3 not_applicable (lossless format)

    def matches(self, header: bytes) -> bool:
        # An ID3v2 prefix is only a *candidate*: MP3 files carry one far more often
        # than FLAC files do, so claims() below decides by looking past the tag.
        return header[:4] == b"fLaC" or header[:3] == b"ID3"

    def claims(self, data: bytes) -> bool:
        try:
            return data[w._id3v2_len(data):][:4] == w.MAGIC
        except ParseError:
            return False

    def scrub_f1(self, data: bytes) -> bytes:
        return f1.scrub(data)

    def scrub_f2(self, data: bytes) -> bytes:
        return f2.scrub(data)

    def scrub_f3(self, data: bytes) -> bytes:
        raise FidelityError("flac F3 is not_applicable (lossless format; use F2)")

    def verify(self, data: bytes, fidelity: str) -> list[str]:
        if fidelity == "F1":
            return f1.residuals(data)
        if fidelity == "F2":
            return f2.residuals(data)
        return []
