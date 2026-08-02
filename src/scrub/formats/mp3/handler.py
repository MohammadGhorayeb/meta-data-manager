"""MP3 handler — registers magic + dispatches fidelity to the tier modules.

F1 (bit-preserving tag strip) is implemented. F3 (canonical lossy re-encode — the
A2 defense that normalizes the encoder fingerprint) arrives next. F2 is
NOT_APPLICABLE for MP3: unlike PNG's separable deflate layer, MP3's compressed
frames ARE the content, so there is no lossless re-encode that changes the
encoder fingerprint — F1 keeps the audio exact, F3 re-encodes.
"""
from __future__ import annotations

from ...errors import FidelityError
from ..base import BaseHandler
from . import f1, f3


class Mp3Handler(BaseHandler):
    format_id = "mp3"
    # No fixed magic prefix — matches() sniffs ID3 or an MPEG frame sync.
    magic = ()
    fidelities = ("F1", "F3")   # F2 = not_applicable (see module docstring)

    def matches(self, header: bytes) -> bool:
        if header[:3] == b"ID3":
            return True
        # MPEG audio frame sync: 0xFF followed by 111x xxxx (11 sync bits).
        return len(header) >= 2 and header[0] == 0xFF and (header[1] & 0xE0) == 0xE0

    def claims(self, data: bytes) -> bool:
        """An ID3v2 tag can prefix a FLAC file too (out of spec, but taggers do it),
        so having a tag is not proof of MP3. Look past it: a FLAC stream there means
        this is not ours, and claiming it would hand a FLAC file to a walker that
        fails closed on it."""
        if data[:3] != b"ID3":
            return True
        try:
            from ..flac import walker as fw
            return data[fw._id3v2_len(data):][:4] != fw.MAGIC
        except Exception:
            return True

    def scrub_f1(self, data: bytes) -> bytes:
        return f1.scrub(data)

    def scrub_f2(self, data: bytes) -> bytes:
        raise FidelityError(
            "mp3 F2 is not_applicable: MP3 frames are the content, so no lossless "
            "re-encode changes the encoder fingerprint — use F1 (keep audio) or F3")

    def scrub_f3(self, data: bytes) -> bytes:
        return f3.scrub(data)

    def verify(self, data: bytes, fidelity: str) -> list[str]:
        if fidelity == "F1":
            return f1.residuals(data)
        if fidelity == "F3":
            return f3.residuals(data)
        return []
