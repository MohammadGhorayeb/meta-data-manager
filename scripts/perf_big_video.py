"""A video past 4 GiB: streamed, and the same bytes as the in-memory clean (N3).

N3's target was a clip of several gigabytes cleaned within the memory target, with
output byte-identical to the path that holds the whole file. Past 4 GiB a QuickTime
file's chunk offsets become 64-bit (`co64`), which is where a streamed writer that
patches offsets would go wrong. This builds such a clip (perf_bench's test-pattern
video), cleans it the CLI's streamed way in a fresh process (time and physical peak),
cleans it again in memory the way an archive's member is cleaned, and compares:

- the two outputs, by sha256 (must be equal);
- the input's and the output's coded packets, by ffmpeg's streamhash (no decode).

Needs ffmpeg and about 3x the clip's size free on disk; the in-memory clean needs
about 2x the clip in memory. Everything it writes is deleted at the end.

Usage:  ./.venv/bin/python scripts/perf_big_video.py [--mb 4500] [--work DIR] [--runs 3]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))
import perf_bench  # noqa: E402

_IN_MEMORY = r"""
import os, resource, sys, time
sys.path.insert(0, os.environ["REPO"])
from src.scrub.dispatch import default_dispatcher
d = default_dispatcher()
t0 = time.perf_counter()
with open(sys.argv[1], "rb") as f:
    data = f.read()
out = d.resolve(data).scrub(data, "F1")
with open(sys.argv[2], "wb") as f:
    f.write(out)
peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
print(time.perf_counter() - t0, peak if sys.platform == "darwin" else peak * 1024)
"""


def _moov(path: str) -> bytes:
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        pos = 0
        while pos < size:
            f.seek(pos)
            hdr = f.read(16)
            n, kind = struct.unpack(">I4s", hdr[:8])
            if n == 1:
                n = struct.unpack(">Q", hdr[8:16])[0]
            elif n == 0:
                n = size - pos
            if kind == b"moov":
                f.seek(pos)
                return f.read(n)
            pos += n
    return b""


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 24), b""):
            h.update(block)
    return h.hexdigest()


def _packets(path: str) -> list[str]:
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-map", "0", "-c", "copy",
                        "-f", "streamhash", "-hash", "sha256", "-"],
                       capture_output=True, text=True, check=True)
    return p.stdout.strip().splitlines()


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--mb", type=float, default=4500)
    ap.add_argument("--work", default=None)
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args(argv)
    work = args.work or tempfile.mkdtemp(prefix="bigvid_")
    os.makedirs(work, exist_ok=True)
    src, streamed, in_mem = (os.path.join(work, n) for n in ("in.mov", "s.mov", "m.mov"))
    res: dict = {}
    try:
        perf_bench.make("mp4", args.mb, src, work)
        res["size"] = os.path.getsize(src)
        res["co64"] = b"co64" in _moov(src)
        res["streamed"] = []
        for _ in range(args.runs):
            r = perf_bench.measure(src, "F1", streamed, 7200)
            if "error" in r:
                raise SystemExit(f"streamed clean failed: {r['error']}")
            res["streamed"].append({"seconds": round(r["seconds"], 2),
                                    "peak_mb": round(r["peak"] / 1e6)})
        p = subprocess.run([sys.executable, "-c", _IN_MEMORY, src, in_mem],
                           capture_output=True, text=True, check=True,
                           env=dict(os.environ, REPO=REPO))
        secs, peak = p.stdout.split()
        res["in_memory"] = {"seconds": round(float(secs), 2),
                            "peak_mb": round(int(peak) / 1e6)}
        res["byte_identical"] = _sha(streamed) == _sha(in_mem)
        res["packets_identical"] = _packets(src) == _packets(streamed)
        res["output_co64"] = b"co64" in _moov(streamed)
    finally:
        for path in (src, streamed, in_mem):
            if os.path.exists(path):
                os.unlink(path)
    print(json.dumps(res, indent=1))
    return 0 if res.get("byte_identical") and res.get("packets_identical") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
