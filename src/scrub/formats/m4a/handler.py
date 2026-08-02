"""M4A handler — ISOBMFF audio (AAC or ALAC in an MP4 container).

F1 is a bit-preserving strip: the audio samples in `mdat` are copied through
untouched and the sample tables are patched so they still point at them.

F2 re-muxes through one canonical muxer, normalising box order and inter-box slack —
the muxer fingerprint — while copying the coded audio stream, so it is lossless for
both AAC and ALAC. It does NOT reach the AAC encoder's own fingerprint inside that
stream, which is why F3 exists: a canonical re-encode, the direct analogue of MP3 F3.

Magic: ISOBMFF has no leading magic; the `ftyp` box sits at offset 4. Dispatch on
that, then confirm the brand is an audio one so this handler does not claim MP4 video
or HEIC images, which are the same container and belong to Phase 4.
"""
from __future__ import annotations

from ..base import BaseHandler
from . import f1, f2, f3

# Brands that mean "audio in an MP4 container". `mp42`/`isom` are generic, so they
# are accepted only when claims() finds an audio track and no video track.
AUDIO_BRANDS = (b"M4A ", b"M4B ", b"mp42", b"isom", b"M4P ")


class M4aHandler(BaseHandler):
    format_id = "m4a"
    magic = ()                     # no leading magic; see matches()
    fidelities = ("F1", "F2", "F3")

    def matches(self, header: bytes) -> bool:
        return len(header) >= 12 and header[4:8] == b"ftyp"

    def claims(self, data: bytes) -> bool:
        """Confirm this is AUDIO, not video or a HEIC image in the same container.

        The generic brands (`isom`, `mp42`) are used by everything, so brand alone
        cannot decide it. Look for a sound-media handler (`hdlr` type `soun`) and
        refuse if a video handler is present — an MP4 with a video track is Phase 4's
        problem, and claiming it here would strip its metadata with an audio-shaped
        set of assumptions.
        """
        try:
            from ...standards import isobmff as iso
            boxes = iso.parse(data)
            types = set()
            for root in boxes:
                for box in root.walk():
                    if box.type == b"hdlr" and len(box.payload) >= 12:
                        types.add(box.payload[8:12])
            if b"vide" in types:
                return False
            return b"soun" in types
        except Exception:
            return False

    def scrub_f1(self, data: bytes) -> bytes:
        return f1.scrub(data)

    def scrub_f2(self, data: bytes) -> bytes:
        return f2.scrub(data)

    def scrub_f3(self, data: bytes) -> bytes:
        return f3.scrub(data)

    def verify(self, data: bytes, fidelity: str) -> list[str]:
        if fidelity == "F1":
            return f1.residuals(data)
        if fidelity == "F2":
            return f2.residuals(data)
        if fidelity == "F3":
            return f3.residuals(data)
        return []
