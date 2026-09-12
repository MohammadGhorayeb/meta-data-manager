"""HEIC handler — brand-based identification and F1.

Identification is the interesting half, as it was for DOCX. `....ftyp` is shared by
every ISOBMFF file this project already handles: M4A is brand `M4A `, an MP4 is
`isom`, and a HEIC is `heic`/`heix`/`mif1`. So `matches()` is a cheap gate on the
`ftyp` marker and `claims()` reads the brand — and the M4A handler, registered
earlier, keeps its own files because its brand check runs first.
"""
from __future__ import annotations

from ...errors import FidelityError
from ..base import BaseHandler
from . import f1
from . import inspect as _inspect
from . import walker as w


class HeicHandler(BaseHandler):
    format_id = "heic"
    # The box-size word precedes `ftyp`, so there is no constant 4-byte prefix to
    # match on; `matches()` is overridden instead.
    magic = ()
    fidelities = ("F1",)          # F2/F3 would mean re-encoding HEVC: Phase 4 M5+

    def matches(self, header: bytes) -> bool:
        return len(header) >= 8 and header[4:8] == w.FTYP

    def claims(self, data: bytes) -> bool:
        return w.looks_like_heic(data)

    def scrub_f1(self, data: bytes) -> bytes:
        return f1.scrub(data)

    def scrub_f2(self, data: bytes) -> bytes:
        raise FidelityError(
            "heic F2 is not built yet: a lossless re-encode would mean re-emitting "
            "the HEVC tiles, which Phase 4 has not measured")

    def verify(self, data: bytes, fidelity: str) -> list[str]:
        return f1.residuals(data) if fidelity == "F1" else []

    def describe(self, data: bytes) -> dict[str, str]:
        return _inspect.describe(data)

    # ExifTool groups this tier documents as surviving, so the report's cross-check
    # reports a value found there as a known residual rather than as a failure.
    #
    # An ICC profile's *header* is sanitised (manufacturer, creator, date zeroed),
    # but its tag data -- which includes `ProfileCopyright: Copyright Apple Inc.` --
    # is the colour transform itself, and rewriting it would change the picture.
    # That is limit #14, already measured for JPEG and PDF. Without this the check
    # would flag `Apple` on every iPhone photo, and a check that cries wolf is one
    # people learn to ignore.
    expected_residual_groups = frozenset({
        "ICC_Profile", "ICC-header", "ICC-cicp", "ICC_Profile2", "ICC_Profile3",
        "ICC_Profile4", "ICC-header2", "ICC-header3", "ICC-header4",
        "ICC-cicp2", "ICC-cicp3", "ICC-cicp4",
    })
