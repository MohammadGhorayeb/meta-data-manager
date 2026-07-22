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
            if h.matches(header):
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
    return d
