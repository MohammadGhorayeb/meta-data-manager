"""EPUB handler (F1): a ZIP whose `mimetype` says `application/epub+zip`.

Registered after DOCX and before the plain-archive handler, which refuses every
OCF package by name -- so an EPUB is never cleaned as a plain archive, where its
package document would be kept as text.
"""
from __future__ import annotations

from ...errors import ParseError
from ..base import BaseHandler
from ..ooxml import zipread
from . import f1


class EpubHandler(BaseHandler):
    format_id = "epub"
    magic = (b"PK\x03\x04",)
    fidelities = ("F1",)

    def __init__(self, keep_unknown: bool = False, check_memory: bool = True) -> None:
        self.keep_unknown = keep_unknown
        self.check_memory = check_memory

    def claims(self, data: bytes) -> bool:
        try:
            return f1.is_epub(zipread.read(data))
        except ParseError:
            return False

    def scrub_f1(self, data: bytes) -> bytes:
        return f1.scrub(data, keep_unknown=self.keep_unknown,
                        check_memory=self.check_memory)

    def verify(self, data: bytes, fidelity: str) -> list[str]:
        return f1.residuals(data) if fidelity == "F1" else []

    def describe(self, data: bytes) -> dict[str, str]:
        return f1.describe(data)

    def kept(self, data: bytes, fidelity: str) -> list[str]:
        return f1.kept(data)

    def advise(self, data: bytes) -> list[str]:
        return f1.advise(data)
