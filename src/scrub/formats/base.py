"""Handler interface + magic-number registration.

A handler owns one format family. It declares the magic-number prefixes it
claims and implements scrub() for the fidelity tiers it supports. Fail-closed is
the contract: scrub() either produces a fully-scrubbed file or raises a
ScrubError (the CLI then writes no output).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..errors import FidelityError


@runtime_checkable
class ImageHandler(Protocol):
    format_id: str
    magic: tuple[bytes, ...]           # magic-number prefixes this handler claims
    fidelities: tuple[str, ...]        # e.g. ("F1", "F2", "F3")

    def matches(self, header: bytes) -> bool: ...
    def claims(self, data: bytes) -> bool: ...
    def scrub(self, data: bytes, fidelity: str) -> bytes: ...


class BaseHandler:
    """Mixin: magic matching + fidelity dispatch to scrub_f1/f2/f3 methods."""
    format_id: str = "base"
    magic: tuple[bytes, ...] = ()
    fidelities: tuple[str, ...] = ()

    def matches(self, header: bytes) -> bool:
        return any(header.startswith(m) for m in self.magic)

    def claims(self, data: bytes) -> bool:
        """Second-stage confirmation over the WHOLE buffer, for formats a header
        prefix cannot separate. Both MP3 and FLAC may carry a leading ID3v2 tag, and
        that tag can be kilobytes long, so the bytes that actually identify the
        format sit far past any fixed-size header. Default: the prefix was enough."""
        return True

    def scrub(self, data: bytes, fidelity: str) -> bytes:
        if fidelity not in self.fidelities:
            raise FidelityError(
                f"{self.format_id} does not offer {fidelity} "
                f"(has {self.fidelities})")
        method = getattr(self, f"scrub_{fidelity.lower()}", None)
        if method is None:
            raise FidelityError(f"{self.format_id}: {fidelity} not implemented")
        return method(data)
