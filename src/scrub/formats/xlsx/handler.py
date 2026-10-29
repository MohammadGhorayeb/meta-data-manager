"""XLSX handler — Excel workbooks (and templates) at F1.

Identified like DOCX: `PK\\x03\\x04` decides nothing, so `claims()` opens the
central directory and looks for the part that makes a package a workbook
(`xl/workbook.xml`). Registered after DOCX and before EPUB and the plain-archive
handler, which refuses every Office package it reaches by name.
"""
from __future__ import annotations

from ...errors import ParseError
from ..base import BaseHandler
from ..ooxml import opc
from . import f1
from . import inspect as _inspect


class XlsxHandler(BaseHandler):
    format_id = "xlsx"
    magic = (b"PK\x03\x04",)
    fidelities = ("F1",)

    def matches(self, header: bytes) -> bool:
        return header.startswith(b"PK\x03\x04")

    def claims(self, data: bytes) -> bool:
        return opc.looks_like(data, "xlsx")

    def preflight(self, data: bytes) -> list[str]:
        try:
            pkg = opc.parse(data)
        except ParseError as exc:
            return [str(exc)]
        return f1._preflight(pkg)

    def scrub_f1(self, data: bytes) -> bytes:
        return f1.scrub(data)

    def verify(self, data: bytes, fidelity: str) -> list[str]:
        return f1.residuals(data) if fidelity == "F1" else []

    def kept(self, data: bytes, fidelity: str = "F1") -> list[str]:
        return f1.advisories(data)

    def describe(self, data: bytes) -> dict[str, str]:
        return _inspect.describe(data)
