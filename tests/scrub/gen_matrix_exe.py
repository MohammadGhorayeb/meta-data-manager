"""Generate the programs (ELF, Mach-O, PE) Pareto matrix from real harness runs.

Honesty rule (CLAUDE.md): a cell is only pass/fail if we measured it.

Measured here:
  - A1@F1   differential leak oracle: the same program from three builders whose
            names have the same length, three copies each, for every corpus shape
            -- ELF (C, Go, 32-bit big-endian), Mach-O (thin, universal, Go), PE
            (MSVC, mingw, Go, DLL) -- plus real builds from three directories with
            whatever compiler this machine has (gcc and mingw-w64 on Linux, clang
            on a Mac). Every metadata-correlated byte has to collapse. Same-length
            names because a zeroed path keeps its length (limit #49); that channel
            is asserted separately in the tests.
  - A2@F1   E-EXE: one C source through two real compilers per format, three
            builders each. Expected to FAIL -- F1 keeps the code and the compiler
            is in the code -- and run for WHICH container traits still separate the
            compilers, since those are what an F2 could normalise.

Not measured, and said so rather than left blank:
  - F2      not built: dropping the zeroed parts and normalising the container
            (section order and names, header versions) without touching code.
  - F3      has no meaning for a program: re-compiling is not cleaning.

Generate on Linux (CI's platform): a Mac has one C compiler, so A2 cannot be
measured there and the cell would read not_tested.

Run:  ./.venv/bin/python -m tests.scrub.gen_matrix_exe
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

from tests.harness import config
from tests.harness.contract import Cell, Leak, Locus, V
from tests.harness.oracle import fingerprint_guard, leak
from tests.harness.plugins.exe import ExePlugin
from tests.harness.runner import matrix
from tests.scrub import e_exe
from tests.scrub import elf_corpus as ec
from tests.scrub import macho_corpus as mc
from tests.scrub import pe_corpus as pc

TOOL = {
    "name": "irreversible_scrubber",
    "version": "0.1.0-p5",
    "invocation": "python -m src.scrub {in} {out} --fidelity {fidelity}",
}
BUILDERS = e_exe.BUILDERS
SHAPES = {
    "elf-c": lambda u: ec.build(u),
    "elf-go": lambda u: ec.build(u, go=True),
    "elf-32be": lambda u: ec.build(u, bits=32, order=">", shared_tail=True),
    "macho-thin": lambda u: mc.build(u),
    "macho-universal": lambda u: mc.fat(u),
    "macho-go": lambda u: mc.build(u, go=True),
    "pe-msvc": lambda u: pc.build(u),
    "pe-mingw": lambda u: pc.build(u, shape="mingw"),
    "pe-go": lambda u: pc.build(u, shape="mingw", go=True),
    "pe-dll": lambda u: pc.build(u, dll=True),
}


def _scrubber():
    from tests.scrub.test_harness_a1 import InProcessScrubber
    return InProcessScrubber()


def _copies(tmpdir: str, tag: str, blobs: list[bytes], n_repeats: int = 3):
    groups = []
    for i, blob in enumerate(blobs):
        paths = []
        for r in range(n_repeats):
            path = os.path.join(tmpdir, f"a1_{tag}_v{i}_r{r}")
            with open(path, "wb") as f:
                f.write(blob)
            paths.append(path)
        groups.append(paths)
    return groups


def _real_builds(tmpdir: str) -> dict[str, list[bytes]]:
    """The same program from three build directories, per compiler present."""
    out = {}
    builds = []
    if sys.platform.startswith("linux"):
        builds += [("real-elf-gcc", ["gcc", "-g"], "hello"),
                   ("real-pe-mingw", ["x86_64-w64-mingw32-gcc", "-g"], "hello.exe")]
    if sys.platform == "darwin":
        builds += [("real-macho-clang", ["clang", "-g"], "hello")]
    for tag, cmd, name in builds:
        if shutil.which(cmd[0]) is None:
            continue
        blobs = []
        for who in BUILDERS:
            path = e_exe._compile(cmd, os.path.join(tmpdir, tag, who, "proj"), name)
            blobs.append(open(path, "rb").read())
        out[tag] = blobs
    return out


def _a1_cell(tmpdir: str) -> Cell:
    scrubber, plugin = _scrubber(), ExePlugin()
    layouts = {tag: [build(u) for u in BUILDERS] for tag, build in SHAPES.items()}
    layouts.update(_real_builds(tmpdir))
    failed, leaks = [], []
    for tag, blobs in layouts.items():
        cell = leak.evaluate_a1(scrubber, plugin, _copies(tmpdir, tag, blobs), "F1",
                                n=3, sentinel_field="builder", modality="bytes")
        if cell.verdict != V.PASS:
            failed.append(tag)
            leaks += cell.leaks
    if failed:
        return Cell("A1", "F1", V.FAIL, reason="builder-correlated bytes survive in "
                    + ", ".join(failed), leaks=leaks)
    real = [t for t in layouts if t.startswith("real-")]
    return Cell("A1", "F1", V.PASS,
                reason=f"no builder-correlated byte survives in any of {len(layouts)} "
                       f"layouts ({', '.join(layouts)}); real compilers here: "
                       + (", ".join(real) or "none"))


def _a2_cell(tmpdir: str) -> Cell:
    found = e_exe.measure(tmpdir)
    if not found:
        return Cell("A2", "F1", V.NOT_TESTED,
                    reason="exe_a2_not_measured: fewer than two real compilers for "
                           "any program format on this machine (a Mac has one C "
                           "compiler); generate on Linux, where CI re-measures it")
    parts, leaks, failing = [], [], False
    for fmt, r in found.items():
        survived = r["F1"]["struct_fingerprints"]
        closed = sorted(set(r["raw"]["struct_fingerprints"]) - set(survived))
        prods = ", ".join(r["F1"]["producers"])
        if survived:
            failing = True
            parts.append(f"{fmt} ({prods}): still separate on "
                         + ", ".join(k.removeprefix("struct:") for k in survived)
                         + ("; F1 closed " + ", ".join(k.removeprefix("struct:")
                                                       for k in closed)
                            if closed else ""))
            leaks += [Leak("source_fingerprint", Locus("structural", feature_id=k),
                           f"compiler ({fmt})", f"{k} constant within a compiler, "
                           "differs across") for k in survived]
        else:
            parts.append(f"{fmt} ({prods}): no container trait separates them")
    reason = ("compiler still identifiable from the container around the code "
              "(the code itself is excluded from this channel: F1 keeps it and it "
              "differs by definition). " if failing else "") + "; ".join(parts)
    return Cell("A2", "F1", V.FAIL if failing else V.PASS, reason=reason,
                leaks=leaks)


def _families(tmpdir: str) -> dict[str, list[str]]:
    """Diverse inputs per family, so a run common to every OUTPUT of a family is
    ours. Per family rather than one mixed set: a mark only Mach-O output carries
    could never be common to an ELF output too, so a mixed set would hide every
    format's own constants -- the guard would pass by never being able to fail.
    Mach-O inputs are all signed (as every arm64 program is), and Go is its own
    family across the three containers, for the same reason. Builders' names
    differ in length here (unlike A1): equal lengths would make the blanked paths
    themselves a common run."""
    families = {
        "elf": [ec.build("al", seed=1),
                ec.build("bruno", bits=32, order=">", seed=2),
                ec.build("christopher", shared_tail=True, text_path=True, seed=3)],
        "macho": [mc.build("al", seed=1), mc.fat("bruno", seed=2),
                  mc.build("christopher", filetype=mc.MH_DYLIB, sign_style="codesign",
                           seed=3)],
        "pe": [pc.build("al", seed=1),
               pc.build("bruno", shape="mingw", plus=False, seed=2),
               pc.build("christopher", dll=True, seed=3)],
        "go": [ec.build("al", go=True, seed=1), mc.build("bruno", go=True, seed=2),
               pc.build("christopher", shape="mingw", go=True, seed=3)],
    }
    out = {}
    for fam, blobs in families.items():
        out[fam] = []
        for i, blob in enumerate(blobs):
            path = os.path.join(tmpdir, f"div_{fam}_{i}")
            with open(path, "wb") as f:
                f.write(blob)
            out[fam].append(path)
    return out


def build_doc(tmpdir: str) -> dict:
    not_built = ("exe_f2_not_built: dropping the zeroed parts and normalising the "
                 "container (section order and names, header versions) without "
                 "touching code is not built")
    no_f3 = ("exe_f3_not_applicable: a lossy tier has no meaning for a program -- "
             "re-compiling it is not cleaning it")
    cells = [
        _a1_cell(tmpdir),
        Cell("A1", "F2", V.NOT_TESTED, reason=not_built),
        Cell("A1", "F3", V.NOT_TESTED, reason=no_f3),
        _a2_cell(tmpdir),
        Cell("A2", "F2", V.NOT_TESTED, reason=not_built),
        Cell("A2", "F3", V.NOT_TESTED, reason=no_f3),
    ]
    plugin = ExePlugin()
    verdicts, signatures = [], []
    for paths in _families(tmpdir).values():
        v, sigs = fingerprint_guard.evaluate(_scrubber(), plugin, paths, "F1",
                                             min_len=config.MIN_SIG_LEN)
        verdicts.append(v)
        signatures += sigs
    guard_verdict = next((v for v in verdicts if v != "pass"), "pass")
    excluded = [{"bytes_hex": c.hex(), "decoded": c.decode("latin-1", "replace")}
                for c in plugin.mandatory_constants()]
    fp = matrix.fingerprint_block(guard_verdict, signatures, excluded)
    return matrix.assemble("exe", TOOL, cells, fp)


def main() -> str:
    doc = build_doc(tempfile.mkdtemp(prefix="exe_matrix_"))
    out_path = os.path.join(str(config.RESULTS_DIR), f"exe_{TOOL['name']}.json")
    matrix.write(doc, out_path)
    return out_path


if __name__ == "__main__":
    print(main())
