"""Deep fuzz campaign over every handler (November plan N2a).

`tests/scrub/test_fuzz.py` runs a small seeded sweep on every push. This is the wide
one, run by hand after any change to a walker or a writer: SEEDS x N mutations per
corpus shape and fidelity tier, each through the REAL CLI path (`cli.scrub_file`:
memory preflight, dispatch, scrub, residual check, atomic write) rather than the
handler alone, so the whole fail-closed promise is what gets checked:

- the outcome is a success or a DECLARED refusal -- a `ScrubError` subclass the CLI
  maps to its own exit code. Anything else (another exception type, or the bare
  base class, which the CLI can only call "unexpected", exit 1) is an ESCAPE;
- a refusal writes nothing: no output file, no `*.tmp` beside it;
- nothing is left in the temp folder afterwards (F2/F3 copy the input there);
- each file finishes within a time limit (a HANG otherwise) and its peak memory is
  recorded.

Every escape, hang and residue is listed with what replays it (shape, tier, seed,
mutation index). Aggregates only: the inputs are synthetic corpora, but the habit is
the project's.

Usage:  ./.venv/bin/python scripts/fuzz_campaign.py [--n 1000] [--seeds 11 22 33]
            [--heavy-n 200] [--timeout 30] [--jobs 8] [--only jpeg pdf ...]
            [--out results.json]
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import random
import resource
import shutil
import signal
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Tiers that hand the file to an external program per scrub: run with --heavy-n.
HEAVY = {("mp3", "F3"), ("m4a", "F2"), ("m4a", "F3"), ("flac", "F2"),
         ("pdf", "F2"), ("pdf", "F3"), ("jpeg", "F3")}


def corpus(tmpdir: str) -> dict[str, tuple[bytes, tuple[str, ...]]]:
    """The suite's fuzz corpus plus shapes written by real producers."""
    from tests.scrub import e_office
    from tests.scrub.test_fuzz import _corpus
    out = _corpus(tmpdir)
    work = os.path.join(str(tmpdir), "producers")
    os.makedirs(work, exist_ok=True)
    try:
        for kind, producers in (("xlsx", e_office.xlsx_producers()),
                                ("pptx", e_office.pptx_producers())):
            for name, make in producers.items():
                dst = os.path.join(work, f"{kind}_{name}.{kind}")
                sub = os.path.join(work, f"{kind}_{name}")
                os.makedirs(sub, exist_ok=True)
                make(dst, sub)
                with open(dst, "rb") as f:
                    out[f"{kind}-{name}"] = (f.read(), ("F1",))
    except Exception as exc:                              # noqa: BLE001
        print(f"note: producer shapes skipped ({type(exc).__name__}: {exc})",
              file=sys.stderr)
    return out


# The system temp root, read once per process: jobs point `tempfile` at a folder
# of their own and delete it, and a reused worker must not start inside it.
_BASE_TMP = tempfile.gettempdir()
_BASE_ENV = os.environ.get("TMPDIR")


def _inner_mutations(data: bytes, rnd: random.Random, n: int):
    """Damage that gets PAST a checksum. A flipped byte in a ZIP member or a PNG
    chunk is refused at its CRC, so plain mutation only ever tests that check: here
    one member's decompressed bytes (or one PNG chunk's data) are damaged and the
    container is rebuilt with valid CRCs, so the parser behind them is reached."""
    import io
    import struct
    import zipfile
    import zlib

    from tests.scrub.test_fuzz import _mutations
    if data[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            members = [(i, z.read(i)) for i in z.infolist()]
        targets = [k for k, (i, b) in enumerate(members) if not i.is_dir() and b]
        for k in range(n):
            pick = rnd.choice(targets)
            info, body = members[pick]
            # One mutation of the member, drawn from the same shapes as the outer.
            gen = _mutations(body, rnd, 3)
            for _ in range(2 + rnd.randrange(3)):        # skip the two truncations
                label, damaged = next(gen, (None, body))
            out = io.BytesIO()
            with zipfile.ZipFile(out, "w") as z:
                for j, (i2, b2) in enumerate(members):
                    z.writestr(i2, damaged if j == pick else b2)
            yield f"member:{info.filename}:{label}-{k}", out.getvalue()
    elif data[:8] == b"\x89PNG\r\n\x1a\n":
        chunks, pos = [], 8
        while pos + 8 <= len(data):
            length, ctype = struct.unpack(">I4s", data[pos:pos + 8])
            chunks.append((ctype, data[pos + 8:pos + 8 + length]))
            pos += 12 + length
        for k in range(n):
            pick = rnd.randrange(len(chunks))
            ctype, body = chunks[pick]
            gen = _mutations(body or b"\0", rnd, 3)
            for _ in range(2 + rnd.randrange(3)):
                label, damaged = next(gen, (None, body))
            out = bytearray(data[:8])
            for j, (t, b) in enumerate(chunks):
                b = damaged if j == pick else b
                out += struct.pack(">I", len(b)) + t + b
                out += struct.pack(">I", zlib.crc32(t + b) & 0xFFFFFFFF)
            yield f"chunk:{ctype.decode('latin-1')}:{label}-{k}", bytes(out)


def mutations(blob: bytes, rnd: random.Random, n: int):
    from tests.scrub.test_fuzz import _mutations
    yield from _mutations(blob, rnd, n)
    yield from _inner_mutations(blob, rnd, n)


class _Hang(Exception):
    pass


def _alarm(_signum, _frame):
    raise _Hang()


def _peak_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1 << 20) if sys.platform == "darwin" else peak / 1024


def run_job(shape: str, tier: str, seed: int, n: int, blob: bytes,
            timeout: int) -> dict:
    """One (shape, tier, seed): every mutation through the CLI path."""
    from src.scrub import cli
    from src.scrub.errors import ScrubError
    work = tempfile.mkdtemp(prefix=f"fuzz_{shape}_{tier}_{seed}_", dir=_BASE_TMP)
    tmp = os.path.join(work, "tmp")
    os.makedirs(tmp)
    tempfile.tempdir = tmp                     # F2/F3 work dirs land here
    os.environ["TMPDIR"] = tmp                 # and subprocesses' ones
    signal.signal(signal.SIGALRM, _alarm)
    src, dst = os.path.join(work, "in.bin"), os.path.join(work, "out", "out.bin")
    os.makedirs(os.path.dirname(dst))
    res = {"shape": shape, "tier": tier, "seed": seed, "total": 0, "ok": 0,
           "refused": {}, "escapes": [], "hangs": [], "residue": [],
           "max_seconds": 0.0, "peak_mb": 0.0}
    rnd = random.Random(seed)
    for index, (label, data) in enumerate(mutations(blob, rnd, n)):
        with open(src, "wb") as f:
            f.write(data)
        if os.path.exists(dst):
            os.unlink(dst)
        res["total"] += 1
        where = {"seed": seed, "index": index, "label": label}
        start = time.monotonic()
        signal.alarm(timeout)
        try:
            cli.scrub_file(src, dst, tier)
            outcome = "ok"
        except _Hang:
            outcome = "hang"
            res["hangs"].append(where)
        except ScrubError as exc:
            code = cli.exit_code(exc)
            if code == 1:
                outcome = "escape"
                res["escapes"].append({**where, "type": "ScrubError (bare)",
                                       "message": str(exc)[:200]})
            else:
                outcome = "refused"
                res["refused"][type(exc).__name__] = \
                    res["refused"].get(type(exc).__name__, 0) + 1
        except Exception as exc:                          # noqa: BLE001
            outcome = "escape"
            res["escapes"].append({**where, "type": type(exc).__name__,
                                   "message": str(exc)[:200]})
        finally:
            signal.alarm(0)
        res["max_seconds"] = max(res["max_seconds"], time.monotonic() - start)
        if outcome == "ok":
            res["ok"] += 1
        else:
            left = os.listdir(os.path.dirname(dst))
            if left:
                res["residue"].append({**where, "kind": "output", "files": left})
                for name in left:
                    os.unlink(os.path.join(os.path.dirname(dst), name))
        # The private work root (`workdir.py`, `scrub-<uid>`) is made by every run
        # and stays; what must never stay is anything INSIDE it, or beside it.
        root = f"scrub-{os.getuid()}"
        leftover = [n for n in os.listdir(tmp) if n != root]
        if os.path.isdir(os.path.join(tmp, root)):
            leftover += [f"{root}/{n}" for n in os.listdir(os.path.join(tmp, root))]
        if leftover:
            res["residue"].append({**where, "kind": "temp", "files": leftover[:5],
                                   "outcome": outcome})
            for name in leftover:
                shutil.rmtree(os.path.join(tmp, name), ignore_errors=True)
    res["peak_mb"] = round(_peak_mb(), 1)
    tempfile.tempdir = None
    if _BASE_ENV is None:
        os.environ.pop("TMPDIR", None)
    else:
        os.environ["TMPDIR"] = _BASE_ENV
    shutil.rmtree(work, ignore_errors=True)
    return res


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--heavy-n", type=int, default=200)
    ap.add_argument("--seeds", type=int, nargs="+", default=[11, 22, 33])
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="fuzz_corpus_") as td:
        shapes = corpus(pathlib.Path(td))
    if args.only:
        shapes = {k: v for k, v in shapes.items()
                  if any(k == o or k.startswith(o + "-") for o in args.only)}
    jobs = [(s, t, seed, args.heavy_n if (s.split("-")[0], t) in HEAVY else args.n,
             blob, args.timeout)
            for s, (blob, tiers) in shapes.items() for t in tiers for seed in args.seeds]
    print(f"{len(shapes)} shapes, {len(jobs)} jobs, {args.jobs} processes",
          file=sys.stderr)
    results = []
    t0 = time.monotonic()
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(run_job, *j) for j in jobs]
        for fut in as_completed(futures):
            r = fut.result()
            results.append(r)
            bad = len(r["escapes"]) + len(r["hangs"]) + len(r["residue"])
            print(f"  {r['shape']:<16} {r['tier']} seed {r['seed']:<3} "
                  f"{r['total']:>5} files  {r['ok']:>5} ok  "
                  f"{sum(r['refused'].values()):>5} refused  "
                  f"{'FINDINGS ' + str(bad) if bad else ''}", file=sys.stderr)

    # Aggregate per (shape, tier).
    table: dict[tuple[str, str], dict] = {}
    for r in results:
        a = table.setdefault((r["shape"], r["tier"]), {
            "total": 0, "ok": 0, "refused": {}, "escapes": [], "hangs": [],
            "residue": [], "max_seconds": 0.0, "peak_mb": 0.0})
        for k in ("total", "ok"):
            a[k] += r[k]
        for k, v in r["refused"].items():
            a["refused"][k] = a["refused"].get(k, 0) + v
        for k in ("escapes", "hangs", "residue"):
            a[k] += r[k]
        a["max_seconds"] = round(max(a["max_seconds"], r["max_seconds"]), 2)
        a["peak_mb"] = max(a["peak_mb"], r["peak_mb"])
    total = sum(a["total"] for a in table.values())
    print(f"\n{total} files in {time.monotonic() - t0:.0f} s\n")
    print(f"{'shape':<18}{'tier':<5}{'files':>7}{'ok':>7}{'refused':>8}"
          f"{'escape':>7}{'hang':>5}{'resid':>6}{'max s':>7}{'peak MB':>9}")
    for (shape, tier), a in sorted(table.items()):
        print(f"{shape:<18}{tier:<5}{a['total']:>7}{a['ok']:>7}"
              f"{sum(a['refused'].values()):>8}{len(a['escapes']):>7}"
              f"{len(a['hangs']):>5}{len(a['residue']):>6}{a['max_seconds']:>7}"
              f"{a['peak_mb']:>9}")
    findings = [(k, a) for k, a in sorted(table.items())
                if a["escapes"] or a["hangs"] or a["residue"]]
    for (shape, tier), a in findings:
        for kind in ("escapes", "hangs", "residue"):
            for item in a[kind][:5]:
                print(f"  {kind[:-1] if kind != 'residue' else 'residue'}: "
                      f"{shape}/{tier} {item}")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({f"{s}/{t}": a for (s, t), a in table.items()}, f, indent=1)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
