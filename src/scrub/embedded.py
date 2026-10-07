"""Scrub a file found inside another file, at its own format's F1.

Containers keep finding files inside themselves -- a photo inside an SVG, a
picture inside a Word document, every member of a ZIP -- and the invariant PDF F1
established holds for all of them: an embedded file is scrubbed by its OWN format's
handler, not passed through. Written once here, because a container that forgot one
format would be a leak (and Phase 6 has three containers: SVG, ZIP, EPUB).

Identification is by content (the dispatcher's magic numbers), never by the name a
container gives it: a name is a claim, the bytes are the file. Content no handler
claims is refused, naming where it was found -- the fail-closed contract, carried
into the container.
"""
from __future__ import annotations

from .errors import ParseError, UnsupportedFormatError


def scrub_bytes(body: bytes, where: str, dispatcher=None) -> bytes:
    from .dispatch import default_dispatcher
    d = dispatcher or default_dispatcher()
    try:
        handler = d.resolve(body)
    except UnsupportedFormatError:
        raise ParseError(f"{where}: embedded content of a type this project has no "
                         "handler for; refused rather than passed through "
                         "unscrubbed") from None
    if "F1" not in handler.fidelities:
        raise ParseError(f"{where}: {handler.format_id} has no F1 tier to apply")
    return handler.scrub(body, "F1")


def residuals(body: bytes, where: str, dispatcher=None) -> list[str]:
    """What the embedded file's own handler would still find in it."""
    from .dispatch import default_dispatcher
    d = dispatcher or default_dispatcher()
    try:
        handler = d.resolve(body)
    except UnsupportedFormatError:
        return [f"{where}: embedded content of an unhandled type"]
    verify = getattr(handler, "verify", None)
    return [f"{where}: {r}" for r in (verify(body, "F1") if verify else [])]
