"""Windows metafile handler (F1): EMF, with the EMF+ inside it, and WMF."""
from __future__ import annotations

from ..base import BaseHandler
from . import emf, wmf


class MetafileHandler(BaseHandler):
    format_id = "metafile"
    # EMF opens with its header record (type 1); its signature sits at byte 40,
    # past the dispatcher's window, so `claims` confirms it. WMF: the placeable
    # key, or the bare header (memory or disk type, nine-word header).
    magic = (b"\x01\x00\x00\x00", wmf.PLACEABLE_KEY, b"\x01\x00\x09\x00",
             b"\x02\x00\x09\x00")
    fidelities = ("F1",)

    def claims(self, data: bytes) -> bool:
        return emf.is_emf(data) or wmf.is_wmf(data)

    def _module(self, data):
        return emf if emf.is_emf(data) else wmf

    def scrub_f1(self, data: bytes) -> bytes:
        return self._module(data).scrub(data)

    def verify(self, data: bytes, fidelity: str) -> list[str]:
        return self._module(data).residuals(data) if fidelity == "F1" else []

    def describe(self, data: bytes) -> dict[str, str]:
        out = {"Kind": "EMF" if emf.is_emf(data) else "WMF"}
        out.update(self._module(data).describe(data))
        return out

    def kept(self, data: bytes, fidelity: str) -> list[str]:
        return ["Every drawing record stays byte for byte, in order -- shapes, text, "
                "fonts, bitmaps -- and the reference device the coordinates are "
                "expressed in (a screen's or printer's size and resolution)."]
