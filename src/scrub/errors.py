"""Error hierarchy for the scrubber.

Design rule (CLAUDE.md hard-constraint #2 + p1 plan §5): **fail closed**. Any
condition where we cannot guarantee a fully-scrubbed output is an exception, and
the CLI turns it into a non-zero exit with *no* output file — never a
half-scrubbed file that looks clean but isn't.
"""
from __future__ import annotations


class ScrubError(Exception):
    """Base for every scrubber failure."""


class UnsupportedFormatError(ScrubError):
    """No registered handler matched the file's magic number."""


class ParseError(ScrubError):
    """Input is malformed, truncated, or violates the format spec such that we
    cannot account for every byte. We refuse rather than guess — an
    unaccounted-for region could carry metadata."""


class FidelityError(ScrubError):
    """Requested (format, fidelity) point is not offered by the handler, or the
    fidelity string is not one of F1/F2/F3."""


class ContentError(ScrubError):
    """Scrubbing would alter perceptual content beyond the fidelity contract
    (e.g. dropping a transform-critical APP14 or a render-affecting PNG chunk).
    Raised so the CLI refuses rather than silently corrupt the file."""
