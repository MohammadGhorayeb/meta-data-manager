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

    def memory_factor(self, header: bytes) -> float | None:
        """An UPPER BOUND on the memory a file with this header may need, as a
        multiple of its size: the largest factor among the handlers whose prefix
        matches. Every ISOBMFF format shares `....ftyp`, so for those it is
        `resources.factor_for()` that narrows it to the handler that will actually
        run, without reading the media."""
        factors = [h.memory_factor for h in self._handlers
                   if getattr(h, "memory_factor", None) and h.matches(header)]
        return max(factors) if factors else None

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
    from .formats.m4a.handler import M4aHandler
    d.register(M4aHandler())
    from .formats.mp3.handler import Mp3Handler
    d.register(Mp3Handler())
    from .formats.pdf.handler import PdfHandler
    d.register(PdfHandler())
    # HEIC after M4A: both are ISOBMFF and both start `....ftyp`, so the brand is
    # what separates them and the order makes the audio handler's narrower claim
    # (brand `M4A `) run first.
    from .formats.heic.handler import HeicHandler
    d.register(HeicHandler())
    # MP4/MOV after both: M4A keeps audio-only files and HEIC keeps the still-image
    # brands, so what reaches this handler is a known video brand with a `vide`
    # track -- and its brand list is a keep-list, so Canon's CR3 (also ISOBMFF, also
    # `vide`) is declined rather than scrubbed as a movie. Order is belt-and-braces
    # rather than the mechanism: M4A declines any file with a `vide` track and this
    # requires one, so the two claims are mutually exclusive whatever order they run
    # in, and a test asserts that.
    from .formats.mp4.handler import Mp4Handler
    d.register(Mp4Handler())
    # Camera RAW (TIFF family). Its prefix is TIFF's, shared with plain TIFF
    # pictures, so `claims()` requires the file to say it is a raw.
    from .formats.raw.handler import RawHandler
    d.register(RawHandler())
    # Plain TIFF after RAW: same prefix, and RAW claims only files that say they
    # are raws, so what reaches this handler is a scan, an export, a conversion.
    from .formats.tiff.handler import TiffHandler
    d.register(TiffHandler())
    # WebP: `RIFF` is shared with WAV and AVI, so it claims only the WEBP form.
    from .formats.webp.handler import WebpHandler
    d.register(WebpHandler())
    from .formats.gif.handler import GifHandler
    d.register(GifHandler())
    # SVG is text: it claims only a document whose root element is <svg>.
    from .formats.svg.handler import SvgHandler
    d.register(SvgHandler())
    # Executables (Phase 5). `\x7fELF` is shared with nothing else here.
    from .formats.exe.handler import ExeHandler
    d.register(ExeHandler())
    # DOCX last: its magic (`PK\x03\x04`) is the weakest of any handler here -- it
    # is shared with every ZIP ever made -- so it gets asked only after every format
    # with a distinctive prefix has declined.
    from .formats.docx.handler import DocxHandler
    d.register(DocxHandler())
    return d
