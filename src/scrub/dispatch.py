"""Magic-number dispatch: sniff the leading bytes, route to a handler.

Never trust the file extension — dispatch on content (CLAUDE.md: magic-number
detection). This is the scrubber-side twin of the harness's own dispatcher; the
two are independent (product vs. test infrastructure, harness README §2).
"""
from __future__ import annotations

from .errors import UnsupportedFormatError

# Widest magic prefix any handler inspects; read at least this many header bytes.
_HEADER_BYTES = 16


class Dispatcher:
    def __init__(self) -> None:
        self._handlers: list = []

    def register(self, handler) -> None:
        self._handlers.append(handler)

    def resolve(self, data: bytes):
        header = data[:_HEADER_BYTES]
        for h in self._handlers:
            # Two stages: cheap magic prefix, then an optional whole-buffer
            # confirmation for formats a prefix cannot tell apart (an ID3v2 tag can
            # prefix both MP3 and FLAC, and is far longer than any header window).
            if h.matches(header) and h.claims(data):
                return h
        raise UnsupportedFormatError(
            f"no handler for magic {header[:8].hex(' ')}")


def default_dispatcher() -> Dispatcher:
    """The production registry. Handlers are imported lazily so a broken/optional
    handler can't take down dispatch of the others."""
    d = Dispatcher()
    from .formats.jpeg.handler import JpegHandler
    d.register(JpegHandler())
    from .formats.png.handler import PngHandler
    d.register(PngHandler())
    from .formats.flac.handler import FlacHandler
    d.register(FlacHandler())
    from .formats.mp3.handler import Mp3Handler
    d.register(Mp3Handler())
    return d
