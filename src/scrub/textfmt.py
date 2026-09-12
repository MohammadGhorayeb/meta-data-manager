"""Display helpers shared by the scrub report and its cross-check.

Extracted into their own module for a boring but real reason: `report` imports
`crosscheck` to render the second opinion, and `crosscheck` wanted `clip` to shorten
the values it quotes. Importing back gave a cycle that only failed in one direction —
`import crosscheck` first worked, `import report` first raised — which is the kind of
latent breakage that surfaces later as an unrelated-looking error.
"""
from __future__ import annotations

# Long values are truncated: a report is a summary, and an embedded XMP packet or a
# base64 thumbnail would otherwise fill the terminal with the very data we removed.
MAX_VALUE = 58


def clip(value: object, limit: int = MAX_VALUE) -> str:
    """One line, printable, bounded.

    Whitespace collapses FIRST. Doing it the other way round turns the newlines and
    tabs of a legitimate multi-line comment into dots before they can become spaces,
    so `two\\nlines` renders as `two.lines` -- caught by its own test.

    Control characters do not survive: a crafted file must not be able to write
    escape sequences to the terminal of someone auditing its metadata.
    """
    text = value if isinstance(value, str) else repr(value)
    text = " ".join(text.split())
    text = "".join(ch if ch.isprintable() else "." for ch in text)
    return text if len(text) <= limit else text[:limit - 1] + "…"


def human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"
