"""ZIP handler (F1): any archive the package handlers declined.

Registered after DOCX, which claims Word packages; everything else beginning
`PK\\x03\\x04` (or `PK\\x05\\x06`, an empty archive) reaches this handler, and the
packages it must not treat as plain archives are refused by name in `f1.scrub`.
"""
from __future__ import annotations

from ..base import BaseHandler
from . import f1


class ZipHandler(BaseHandler):
    format_id = "zip"
    magic = (b"PK\x03\x04", b"PK\x05\x06")
    fidelities = ("F1",)

    def __init__(self, keep_unknown: bool = False, check_memory: bool = True) -> None:
        self.keep_unknown = keep_unknown
        self.check_memory = check_memory

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
