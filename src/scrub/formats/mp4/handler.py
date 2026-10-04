"""MP4 / MOV handler — identification and F1.

Identification is by brand, like HEIC's, because `....ftyp` is shared by every
ISOBMFF file this project handles:

  * M4A runs first and keeps audio-only files (it declines anything with `vide`).
  * HEIC runs first and keeps the HEIF still-image brands.
  * This handler claims a known VIDEO brand with a `vide` track. The brand list is a
    keep-list, not "anything else with video": Canon's CR3 raw format is ISOBMFF too
    (`crx `), with `vide` tracks, and scrubbing a camera raw with a video-shaped set
    of assumptions is exactly the mistake the M4A handler refuses to make with MP4.
"""
from __future__ import annotations

from ...errors import FidelityError
from ...standards import isobmff as iso
from ..base import BaseHandler
from . import f1
from . import inspect as _inspect

# Major or compatible brands meaning "a movie". QuickTime first: every iPhone video.
VIDEO_BRANDS = {
    b"qt  ", b"isom", b"iso2", b"iso4", b"iso5", b"iso6", b"mp41", b"mp42",
    b"avc1", b"M4V ", b"M4VH", b"M4VP", b"3gp4", b"3gp5", b"3gp6", b"3g2a",
    b"MSNV", b"XAVC",
}
REFUSED_BRANDS = {b"crx "}          # Canon CR3: camera raw, Phase 4's RAW block


def _brands(data: bytes) -> set[bytes]:
    size = int.from_bytes(data[:4], "big")
    body = data[8:min(size, len(data))]
    return {body[:4]} | {body[i:i + 4] for i in range(8, len(body) - 3, 4)}


class Mp4Handler(BaseHandler):
    format_id = "mp4"
    magic = ()                         # no leading magic; see matches()
    fidelities = ("F1",)
    # Measured 2.07-2.13x peak over the interpreter's baseline on real clips (32-56
    # MB): the input plus the output, nothing else. 2.5 leaves room; the test in
    # test_memory_preflight.py holds the declaration to a fresh measurement.
    memory_factor = 2.5

    def matches(self, header: bytes) -> bool:
        return len(header) >= 12 and header[4:8] == b"ftyp"

    def claims(self, data: bytes) -> bool:
        try:
            brands = _brands(data)
            if brands & REFUSED_BRANDS or not brands & VIDEO_BRANDS:
                return False
            moov = next((b for b in iso.scan(data) if b.type == b"moov"), None)
            if moov is None:
                return False
            root = iso.parse(data[moov.offset:moov.end])[0]
            return any(h.payload[8:12] == b"vide"
                       for trak in root.children if trak.type == b"trak"
                       for h in [trak.find(b"mdia/hdlr")] if h is not None)
        except Exception:                                 # noqa: BLE001
            return False

    # One tag, for the report's ExifTool cross-check. The file holds the brand code
    # `qt  `; "Apple QuickTime (.MOV/QT)" is ExifTool's NAME for that code, so the
    # removed `Make: Apple` "reappears" in text the file never contained. The brand
    # itself is the container format and F1 keeps it on purpose (an A2 feature, as
    # for HEIC). Exempting the tag, not the QuickTime group, which would wave through
    # every structural tag to excuse this one.
    expected_residual_tags = frozenset({"MajorBrand"})

    def scrub_f1(self, data: bytes) -> bytes:
        return f1.scrub(data)

    def scrub_f2(self, data: bytes) -> bytes:
        raise FidelityError(
            "mp4 F2 is not built yet: the candidate is removing the encoder's SEI "
            "settings string from the coded video, which is unmeasured (p4 plan §5.6)")

    def verify(self, data: bytes, fidelity: str) -> list[str]:
        return f1.residuals(data) if fidelity == "F1" else []

    def describe(self, data: bytes) -> dict[str, str]:
        return _inspect.describe(data)

    def kept(self, data: bytes, fidelity: str) -> list[str]:
        """What F1 knowingly leaves: it copies the coded video and audio bit for
        bit, so an encoder that wrote its settings INTO the stream is still named."""
        encoder = _inspect.describe(data).get("Encoder (inside the coded video)")
        if encoder is None:
            return []
        return [f"The video encoder names itself inside the coded video ({encoder}). "
                "F1 keeps every sample bit for bit, so this stays; removing it means "
                "editing the video stream (the F2 candidate, unmeasured)."]
