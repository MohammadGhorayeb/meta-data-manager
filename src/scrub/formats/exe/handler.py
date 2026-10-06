"""Executables (Phase 5): ELF today; Mach-O and PE are M2 and M3.

One handler for the family, because the three formats answer the same question --
what did the build leave that the program never reads -- and share the acceptance
test, "runs the same" (`docs/p5_executables_plan.md` §2, §6). F1 only: removing the
zeroed sections outright, rather than zeroing them in place, is F2's job.
"""
from __future__ import annotations

from ..base import BaseHandler
from . import elf


class ExeHandler(BaseHandler):
    format_id = "exe"
    magic = (elf.MAGIC,)
    fidelities = ("F1",)

    def scrub_f1(self, data: bytes) -> bytes:
        return elf.scrub(data)

    def verify(self, data: bytes, fidelity: str) -> list[str]:
        return elf.residuals(data) if fidelity == "F1" else []

    def advise(self, data: bytes) -> list[str]:
        return elf.advise(data)

    def describe(self, data: bytes) -> dict[str, str]:
        return elf.describe(data)

    def kept(self, data: bytes, fidelity: str) -> list[str]:
        return ["Everything the program loads is kept byte for byte, except its "
                "build IDs, which are recomputed from the cleaned file.",
                "Removed sections keep their size as zeros, so how much debug "
                "information there was -- and how long the removed paths were -- "
                "stays visible (limit #49)."]
