"""Executables (Phase 5): ELF (Linux) and Mach-O (Mac); PE (Windows) is M3.

One handler for the family, because the formats answer the same question -- what
did the build leave that the program never reads -- and share the acceptance test,
"runs the same" (`docs/p5_executables_plan.md` §2, §6). F1 only: removing the
zeroed parts outright, rather than zeroing them in place, is F2's job.
"""
from __future__ import annotations

from ..base import BaseHandler
from . import elf, macho

_MACHO_MAGIC = (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe",     # 64- and 32-bit, LE
                b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce",     # big-endian (PowerPC)
                b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf")     # universal


class ExeHandler(BaseHandler):
    format_id = "exe"
    magic = (elf.MAGIC, *_MACHO_MAGIC)
    fidelities = ("F1",)

    def claims(self, data: bytes) -> bool:
        # `\xca\xfe\xba\xbe` also opens every Java class file; `is_macho` tells them
        # apart by the architecture count, which a class file's version exceeds.
        return elf.is_elf(data) or macho.is_macho(data)

    @staticmethod
    def _module(data: bytes):
        return elf if elf.is_elf(data) else macho

    def scrub_f1(self, data: bytes) -> bytes:
        return self._module(data).scrub(data)

    def verify(self, data: bytes, fidelity: str) -> list[str]:
        return self._module(data).residuals(data) if fidelity == "F1" else []

    def advise(self, data: bytes) -> list[str]:
        return self._module(data).advise(data)

    def describe(self, data: bytes) -> dict[str, str]:
        return self._module(data).describe(data)

    def kept(self, data: bytes, fidelity: str) -> list[str]:
        out = ["Everything the program loads is kept byte for byte, except its build "
               "IDs, which are recomputed from the cleaned file.",
               "Removed parts keep their size as zeros, so how much debug "
               "information there was -- and how long the removed paths were -- "
               "stays visible (limit #49)."]
        if macho.is_macho(data):
            out.append("The code signature is recomputed, so the program still runs "
                       "on Apple Silicon; it is an ad-hoc signature, as before.")
        return out
