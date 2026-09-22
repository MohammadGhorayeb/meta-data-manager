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

**This handler is deliberately not in `default_dispatcher()` yet.** DOCX's M8 set
the precedent and the reason holds: a registered handler is the tool advertising a
format it can scrub, and MP4 F1 does not exist until M3. Registering it now would
mean the CLI accepting a video and then failing, which is worse than declining it.
"""
from __future__ import annotations

from ...errors import FidelityError
from ..base import BaseHandler
from . import walker as w


class Mp4Handler(BaseHandler):
    format_id = "mp4"
    # The box-size word precedes `ftyp`, so there is no constant 4-byte prefix to
    # match on; `matches()` is overridden instead, exactly as HEIC's is.
    magic = ()
    fidelities = ()               # nothing offered until M3 lands F1

    def matches(self, header: bytes) -> bool:
        return len(header) >= 8 and header[4:8] == w.FTYP

    def claims(self, data: bytes) -> bool:
        return w.looks_like_mp4(data)

    def scrub_f1(self, data: bytes) -> bytes:
        raise FidelityError(
            "mp4 F1 is not built yet (Phase 4 M3). The walker and its refusal list "
            "are in place; nothing scrubs an MP4 until the offset patching is "
            "written and verified by decoding, not by parsing")
