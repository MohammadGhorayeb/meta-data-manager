"""Running the external programs the deeper levels hand a file to.

ffmpeg, lame, jpegtran, poppler and LibreOffice read files built by strangers, and
none of the twelve calls that ran them had a time limit: a file crafted to make one
of them loop would hang the clean for ever, and folder mode with it (November plan
N2b, "pathological time"). Every call goes through `run`, which sets a limit that
grows with the size of what the program is given and turns an overrun into a
`ToolError` naming the program, so the CLI refuses with exit 9 and writes nothing.

The limit is deliberately generous -- five minutes, plus twenty seconds a megabyte
-- so it never stops a real file: it exists for the file that would never finish.
"""
from __future__ import annotations

import os
import subprocess

from .errors import ToolError

BASE_SECONDS = 300
SECONDS_PER_MB = 20


def timeout_for(nbytes: int) -> float:
    return BASE_SECONDS + SECONDS_PER_MB * nbytes / 1e6


def _size(cmd, kw) -> int:
    size = len(kw.get("input") or b"")
    for arg in cmd[1:]:
        if isinstance(arg, str) and os.path.isfile(arg):
            size += os.path.getsize(arg)
    return size


def run(cmd, **kw) -> subprocess.CompletedProcess:
    """`subprocess.run` with a time limit (unless the caller set one) and an
    overrun refused as a `ToolError`."""
    limit = kw.setdefault("timeout", timeout_for(_size(cmd, kw)))
    try:
        return subprocess.run(cmd, **kw)
    except subprocess.TimeoutExpired:
        name = os.path.basename(str(cmd[0]))
        raise ToolError(f"{name} ran past {limit:.0f} s on this file and was stopped; "
                        "nothing was written") from None
