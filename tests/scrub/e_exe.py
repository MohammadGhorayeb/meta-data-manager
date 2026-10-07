"""E-EXE — can a cleaned program still be traced to the compiler that built it?

The A2 question for programs, asked the way E-MP4 asks it: the same source built by
different real compilers (the producers), each by three builders in directories
whose names have the same length (the repeats), scrubbed at F1, and the surviving
structure decomposed into what varies within a compiler and what separates them.

What this experiment can and cannot say. F1 keeps the machine code by definition,
and two compilers' code differs by definition, so A2@F1 failing is not in doubt --
the compiler is in the code itself, as the encoder is in a JPEG's quantization
tables. The categorical channel therefore leaves the code out (`ExePlugin`) and the
cell names the CONTAINER traits that still separate compilers: those are what an F2
could normalise without touching a single instruction, so they are its
specification, and everything else is the code.

Producers are real compilers compiling one C source, per format:

- ELF: gcc and clang (Linux).
- PE: mingw-w64 gcc and clang with lld (Linux, cross-compiling).
- Mach-O: Apple clang and a real GCC, when one is installed (rarely).

A format with fewer than two producers on this machine is reported unmeasured,
never clean. Generate the published matrix on Linux, where CI re-measures it.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

from tests.harness.oracle import fields, variance
from tests.harness.plugins.exe import ExePlugin
from tests.scrub import elf_corpus as ec

BUILDERS = ("alice", "carol", "bruno")          # equal length: A1 holds for these


def _compile(cmd: list[str], workdir: str, out: str) -> str:
    os.makedirs(workdir, exist_ok=True)
    with open(os.path.join(workdir, "hello.c"), "w") as f:
        f.write(ec.HELLO_C)
    subprocess.run([*cmd, "hello.c", "-o", out], cwd=workdir, check=True,
                   capture_output=True, timeout=300)
    return os.path.join(workdir, out)


def _works(cmd: list[str], out: str) -> bool:
    """A producer counts only if it actually builds here."""
    if shutil.which(cmd[0]) is None:
        return False
    try:
        with tempfile.TemporaryDirectory() as td:
            _compile(cmd, td, out)
        return True
    except (subprocess.SubprocessError, OSError):
        return False


def producers() -> dict[str, dict[str, list[str]]]:
    """format -> producer -> compile command, for what works on this machine."""
    linux = sys.platform.startswith("linux")
    candidates = {
        "elf": {"gcc": ["gcc", "-O2", "-g"], "clang": ["clang", "-O2", "-g"]}
        if linux else {},
        "pe": {"mingw-gcc": ["x86_64-w64-mingw32-gcc", "-O2", "-g"],
               "clang-lld": ["clang", "--target=x86_64-w64-mingw32", "-fuse-ld=lld",
                             "-O2", "-g"]} if linux else {},
        "macho": {"apple-clang": ["clang", "-O2", "-g"],
                  **{f"gcc-{v}": [f"gcc-{v}", "-O2", "-g"] for v in range(11, 16)
                     if shutil.which(f"gcc-{v}")}}
        if sys.platform == "darwin" else {},
    }
    out = {}
    for fmt, cands in candidates.items():
        name = "hello.exe" if fmt == "pe" else "hello"
        working = {p: c for p, c in cands.items() if _works(c, name)}
        if working:
            out[fmt] = working
    return out


def build_sources(tmpdir: str, fmt: str, cmds: dict[str, list[str]]) -> dict:
    name = "hello.exe" if fmt == "pe" else "hello"
    return {prod: [_compile(cmd, os.path.join(tmpdir, fmt, prod, who, "proj"), name)
                   for who in BUILDERS]
            for prod, cmd in cmds.items()}


def run_condition(fidelity: str, sources: dict, tmpdir: str) -> dict:
    from src.scrub import cli
    plugin = ExePlugin()
    prods = list(sources)
    repeats = list(range(min(len(v) for v in sources.values())))
    cells = {}
    for prod, paths in sources.items():
        for r, src in enumerate(paths):
            target = src
            if fidelity != "raw":
                target = os.path.join(tmpdir, f"{prod}_{r}_{fidelity}")
                cli.scrub_file(src, target, fidelity)
            cells[(prod, r)] = fields.extract(target, plugin, "F1")
    verdicts = variance.decompose(cells, prods, repeats)
    return {"fidelity": fidelity, "producers": prods,
            "struct_fingerprints": sorted(fid for fid, v in verdicts.items()
                                          if v.leak and fid.startswith("struct:")),
            "within_varies": sorted(fid for fid, v in verdicts.items()
                                    if v.floor and fid.startswith("struct:"))}


def measure(tmpdir: str) -> dict[str, dict]:
    """Per format with at least two producers: the raw and F1 conditions."""
    out = {}
    for fmt, cmds in producers().items():
        if len(cmds) < 2:
            continue
        sources = build_sources(tmpdir, fmt, cmds)
        out[fmt] = {"raw": run_condition("raw", sources, tmpdir),
                    "F1": run_condition("F1", sources, tmpdir)}
    return out


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        found = measure(td)
        if not found:
            print("fewer than two compilers for every format here: nothing measured")
        for fmt, r in found.items():
            print(f"\n{fmt}: producers {', '.join(r['F1']['producers'])}")
            for cond in ("raw", "F1"):
                keys = ", ".join(r[cond]["struct_fingerprints"]) or "nothing"
                print(f"  {cond}: separates on {keys}")
                if r[cond]["within_varies"]:
                    print(f"        varies within a compiler: "
                          f"{', '.join(r[cond]['within_varies'])}")


if __name__ == "__main__":
    main()
