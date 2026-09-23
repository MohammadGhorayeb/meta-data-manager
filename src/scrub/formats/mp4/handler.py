"""MP4 handler — brand-and-track identification, ahead of any scrubbing.

Identification is the whole of M2, and it is harder here than for HEIC. HEIC could
decide on the brand alone: `heic`/`heix`/`mif1` belong to nobody else. MP4 cannot —
`isom` and `mp42` are declared by audio-only M4A files too, and the M4A handler has
claimed those since Phase 2. So the two handlers are separated by what is *inside*
the file rather than by what it calls itself: M4A claims a file with a `soun` track
and **refuses** one with a `vide` track; this claims a file with a `vide` track. The
two predicates cannot both be true, which is a stronger guarantee than registration
order alone would give — and a test asserts it on every corpus file rather than
trusting the reasoning.

Registered in `default_dispatcher()` as of M3, which is when F1 landed — DOCX's M8
precedent is that a handler goes in only once it can actually scrub, so that the
tool never advertises a format it will then fail on. It is registered **after** M4A
and HEIC, whose claims are narrower.
"""
from __future__ import annotations

from ...errors import FidelityError
from ..base import BaseHandler
from . import f1
from . import inspect as _inspect
from . import walker as w


class Mp4Handler(BaseHandler):
    format_id = "mp4"
    # The box-size word precedes `ftyp`, so there is no constant 4-byte prefix to
    # match on; `matches()` is overridden instead, exactly as HEIC's is.
    magic = ()
    # F1 only. F2 would mean re-muxing through one canonical muxer and F3
    # re-encoding the video; neither is measured, and a tier that was never run
    # has no verdict — the same position HEIC's matrix takes.
    fidelities = ("F1",)

    def matches(self, header: bytes) -> bool:
        return len(header) >= 8 and header[4:8] == w.FTYP

    def claims(self, data: bytes) -> bool:
        return w.looks_like_mp4(data)

    def scrub_f1(self, data: bytes) -> bytes:
        return f1.scrub(data)

    def scrub_f2(self, data: bytes) -> bytes:
        raise FidelityError(
            "mp4 F2 is not built yet: a lossless re-mux would mean re-emitting the "
            "container through one canonical muxer, which Phase 4 has not measured")

    def verify(self, data: bytes, fidelity: str) -> list[str]:
        return f1.residuals(data) if fidelity == "F1" else []

    def describe(self, data: bytes) -> dict[str, str]:
        return _inspect.describe(data)
