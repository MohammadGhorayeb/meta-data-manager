"""Magic-number dispatch (Part A §11).

Dispatcher.resolve(path) reads the header and returns the first registered
FormatPlugin whose matches() is true, else GenericPlugin. The runner takes a
Scrubber (under test) and resolves a FormatPlugin (harness knowledge)
independently -- two contracts, two lifecycles (Part A §2).
"""
from __future__ import annotations

from .corpus import toy_format


class GenericPlugin:
    """Last-resort, format-agnostic defaults (contract.py / Part B §3)."""
    format_id = "generic"

    def matches(self, header: bytes, path: str) -> bool:
        return True

    def annotate(self, in_path: str, offset: int) -> str | None:
        return None

    def canonical_content(self, path: str) -> bytes:
        # whole-file bytes; the runner treats this as F1/F2-only.
        with open(path, "rb") as f:
            return f.read()

    def mandatory_constants(self) -> list[bytes]:
        return []


class ToyFormatPlugin:
    """Synthetic-format knowledge. TOYF is test infrastructure, so mapping its
    offsets here does NOT cross the no-real-format-parsing boundary."""
    format_id = "toyf"

    def matches(self, header: bytes, path: str) -> bool:
        return header[:4] == toy_format.MAGIC

    def annotate(self, in_path: str, offset: int) -> str | None:
        # Map known TOYF offsets to structure names (toy-only).
        if offset < 4:
            return "magic"
        if offset < 8:
            return "content_len"
        content, _ = toy_format.read(in_path)
        content_end = 8 + len(content)
        if offset < content_end:
            return "content"
        return f"tlv@{offset - content_end}"

    def canonical_content(self, path: str) -> bytes:
        content, _ = toy_format.read(path)
        return content

    def mandatory_constants(self) -> list[bytes]:
        return [toy_format.MAGIC]


class Dispatcher:
    def __init__(self, header_bytes: int = 16):
        self._plugins: list = []
        self._header_bytes = header_bytes

    def register(self, plugin) -> None:
        self._plugins.append(plugin)

    def resolve(self, path: str):
        with open(path, "rb") as f:
            header = f.read(self._header_bytes)
        for plugin in self._plugins:
            if plugin.matches(header, path):
                return plugin
        return GenericPlugin()
