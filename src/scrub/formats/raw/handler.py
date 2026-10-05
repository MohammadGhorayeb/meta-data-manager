"""Camera RAW handler: the TIFF family (DNG, CR2, NEF, ARW, ORF, RW2) and Canon's
ISOBMFF-based CR3 (F1 only).

`II*\\0` and `MM\\0*` open every TIFF, so the prefix is only a gate. A file is
claimed as a raw when it says so: the Olympus or Panasonic magic, a DNGVersion
tag, Canon's `CR` signature, or an image whose photometric interpretation is a
sensor mosaic (CFA) or linear raw. A plain TIFF picture is none of those and is
declined -- scrubbing it with raw-shaped assumptions would be the M4A-versus-MP4
mistake again.
"""
from __future__ import annotations

from ...errors import FidelityError
from ...standards import tiff_ifd as t
from ..base import BaseHandler
from . import cr3, f1
from . import inspect as _inspect

_PREFIXES = (b"II*\x00", b"MM\x00*", b"IIRO", b"IIRS", b"IIU\x00")


class RawHandler(BaseHandler):
    format_id = "raw"
    magic = _PREFIXES
    fidelities = ("F1",)

    def matches(self, header: bytes) -> bool:
        # CR3 opens like every ISOBMFF file; its brand is what says it is a raw.
        return super().matches(header) or cr3.is_cr3(header)

    def claims(self, data: bytes) -> bool:
        if cr3.is_cr3(data):
            return True
        try:
            tree = t.parse(data, strict=False, magics=t.RAW_MAGICS)
            if tree.magic != t.MAGIC_TIFF:
                return True                               # ORF, RW2
            ifd0 = tree.ifd("IFD0")
            if ifd0 is not None and ifd0.get(f1.TAG_DNG_VERSION) is not None:
                return True
            if data[8:10] == b"CR":                       # Canon CR2
                return True
            return any(f1._is_raw_ifd(i, False) for i in tree.ifds)
        except Exception:                                 # noqa: BLE001
            return False

    def scrub_f1(self, data: bytes) -> bytes:
        return cr3.scrub(data) if cr3.is_cr3(data) else f1.scrub(data)

    def scrub_f2(self, data: bytes) -> bytes:
        raise FidelityError("raw F2 is not built: there is no lossless re-encode of "
                            "a sensor mosaic that every raw decoder still reads")

    def verify(self, data: bytes, fidelity: str) -> list[str]:
        if fidelity != "F1":
            return []
        return cr3.residuals(data) if cr3.is_cr3(data) else f1.residuals(data)

    def describe(self, data: bytes) -> dict[str, str]:
        return cr3.describe(data) if cr3.is_cr3(data) else _inspect.describe(data)

    def kept(self, data: bytes, fidelity: str) -> list[str]:
        return ["Make, model and lens model stay: the decoder selects the camera's "
                "colour profile by them. Every other maker-note setting stays too.",
                "Nothing in a raw file can move, so a removed field keeps its length "
                "as zeros, and a blanked time zone reads +00:00."]
