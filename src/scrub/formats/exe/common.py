"""Rules every executable format shares (`docs/p5_executables_plan.md` §5, §6)."""
from __future__ import annotations

import re

from ...errors import ParseError

USER_DIR = re.compile(
    rb"(?:/home/|/Users/)[^/\x00\n]{1,64}/|[A-Za-z]:\\Users\\[^\\\x00\n]{1,64}\\")


def user_dirs_in(data, ranges: list[tuple[str, int, int]]) -> dict[str, str]:
    """User directories found in the given (name, start, end) ranges, first place
    each was seen. Used on what the program loads: its own text (survey §5)."""
    found: dict[str, str] = {}
    for name, start, end in ranges:
        for m in USER_DIR.finditer(data, start, end):
            found.setdefault(m.group().decode("latin-1"), name)
    return found


def program_text_advice(found: dict[str, str]) -> list[str]:
    if not found:
        return []
    where = ", ".join(f"{p}... in {sec}" for p, sec in list(found.items())[:3])
    return ["the program's own text contains a user directory path, which it can "
            f"print (an assert, panic or stack trace), so it is kept: {where}. "
            "Rebuilding with path remapping removes it (limit #50)."]


def check_unchanged(src, out, loaded: list[tuple[int, int]],
                    allowed: list[tuple[int, int]], label: str) -> None:
    """The acceptance rule, enforced on every scrub: inside what the program loads,
    only the `allowed` ranges may differ. Anything else is a bug in the handler and
    the program would run differently -- so nothing is written."""
    probe = bytearray(out)
    for a, b in allowed:
        probe[a:b] = src[a:b]
    view_src, view_out = memoryview(src), memoryview(probe)
    for start, end in loaded:
        if view_src[start:end] != view_out[start:end]:
            first = next(i for i in range(start, end) if src[i] != probe[i])
            raise ParseError(f"{label} F1 changed loaded byte {first:#x}; refused")
