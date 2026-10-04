"""Memory preflight: refuse a file the machine cannot hold, before reading it.

Every handler works on the whole file in memory, so a scrub needs a multiple of the
file's size. For most formats that is never a problem; for video it is, because
phone videos reach gigabytes (limit #38). Until scrubbing streams from disk
(November's hardening), the honest answer is to check first: estimate the need from
the file size and the handler's **measured** memory factor, compare it with what the
machine has available, and refuse with both numbers in the message rather than
start a scrub that ends minutes later in swapping or a kill.

The factor is the measured peak resident memory of one scrub divided by the file
size, with a margin, declared on the handler as `memory_factor` and held to its
measurement by a test that scrubs a large file and reads the peak back. Handlers
that never reach a problematic size declare nothing and are never checked.

If available memory cannot be read (an unrecognised platform), the check passes:
it guards against a predictable failure, not against a leak, and refusing every
scrub on a machine we cannot measure would protect nobody.
"""
from __future__ import annotations

import os
import subprocess
import sys

from .errors import ResourceError, ScrubError

# A top-level box other than `mdat` bigger than this is not read for identification.
_SKELETON_BOX_LIMIT = 64 << 20


def available_memory() -> int | None:
    """Bytes the system could give this process without swapping, or None."""
    try:
        if sys.platform.startswith("linux"):
            with open("/proc/meminfo", encoding="ascii") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        return int(line.split()[1]) * 1024
            return None
        if sys.platform == "darwin":
            # The kernel's own free-memory percentage -- the number behind macOS's
            # memory-pressure gauge, which counts what the compressor and the file
            # cache would give back. Summing `vm_stat`'s free and inactive pages
            # instead read 8.9 GB on a 32 GB Mac the kernel called 50% free, and
            # would have refused videos the machine handles comfortably.
            out = subprocess.run(["sysctl", "-n", "kern.memorystatus_level",
                                  "hw.memsize"], capture_output=True, text=True,
                                 timeout=5, check=True).stdout.split()
            level, total = int(out[0]), int(out[1])
            return total * level // 100 if 0 < level <= 100 else None
    except Exception:                                     # noqa: BLE001
        return None
    return None


def _isobmff_skeleton(path: str) -> bytes | None:
    """Every top-level box of an ISOBMFF file EXCEPT `mdat`, read with seeks.

    Enough for the dispatcher to identify the file (brand, track handlers) without
    reading the media -- the one thing this check must not do. The index of a long
    video is megabytes against gigabytes of media. None if the box walk does not
    add up, in which case the caller falls back to the upper bound.
    """
    parts = []
    with open(path, "rb") as f:
        size = os.fstat(f.fileno()).st_size
        pos = 0
        while pos + 8 <= size:
            f.seek(pos)
            head = f.read(16)
            box, kind = int.from_bytes(head[:4], "big"), head[4:8]
            if box == 1 and len(head) == 16:
                box = int.from_bytes(head[8:16], "big")
            elif box == 0:
                box = size - pos
            if box < 8 or pos + box > size:
                return None
            if kind != b"mdat" and box <= _SKELETON_BOX_LIMIT:
                f.seek(pos)
                parts.append(f.read(box))
            pos += box
    return b"".join(parts) or None


def factor_for(path: str, dispatcher) -> float | None:
    """The measured memory factor of the handler that will scrub `path`, decided
    without reading the file's media.

    The prefix alone gives an upper bound, and for most formats that is exact: one
    handler matches. ISOBMFF is the exception -- M4A (4.5x), HEIC (unchecked) and
    MP4 (2.5x) all start `....ftyp` -- and the upper bound would price every video
    at the audio handler's factor, refusing the very files this check is for. So
    for those the dispatcher resolves the file's skeleton instead.
    """
    with open(path, "rb") as f:
        head = f.read(16)
    bound = dispatcher.memory_factor(head)
    if bound is None or head[4:8] != b"ftyp":
        return bound
    skeleton = _isobmff_skeleton(path)
    if skeleton is None:
        return bound
    try:
        return getattr(dispatcher.resolve(skeleton), "memory_factor", None)
    except ScrubError:
        return bound


def _human(n: float) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"


def preflight(path: str, factor: float | None, available: int | None = None) -> None:
    """Raise ResourceError if scrubbing `path` would need more memory than is free.

    `available` is injectable for tests; by default it is read from the system.
    """
    if not factor:
        return
    size = os.path.getsize(path)
    need = size * factor
    free = available_memory() if available is None else available
    if free is None or need <= free:
        return
    raise ResourceError(
        f"this {_human(size)} file needs about {_human(need)} of memory to scrub "
        f"and this machine has {_human(free)} available. Nothing was read or "
        "written. Close other applications and try again, use a machine with more "
        "memory, or pass --skip-memory-check to try anyway (the scrub still never "
        "writes a partial file).")
