"""Generate the HEIC Pareto matrix from real harness runs.

Honesty rule (CLAUDE.md): a cell is only pass/fail if we measured it.

Measured here:
  - A1@F1   differential leak oracle over content-identical HEICs whose metadata
            differs only by a sentinel — EXIF, XMP, the `uri ` plist, the thumbnail
            and the auxiliary mattes all have to collapse.
  - A2@F1   container-writer peer set: three profiles drawn from two real writers,
            wrapping BYTE-IDENTICAL HEVC tiles, so anything that separates them is
            the muxer and not the picture.

Not measured, and said so rather than left blank:
  - F2/F3   not built. A HEIC F2 means normalising the container losslessly —
            one canonical brand, one table order, one set of `iloc` widths, items
            renumbered — and F3 means re-emitting the HEVC tiles. Neither exists
            yet, so both tiers are `not_tested`, which is the honest verdict for a
            tier that was never run.

A2@F1 is expected to FAIL, and the point of running it is *which* features leak:
this is the Phase 3 method (E-PDF measured which producer channel leaked before F2
was designed) applied to a container format. The named leaks below are the
specification for a future F2.

Run:  ./.venv/bin/python -m tests.scrub.gen_matrix_heic
"""
from __future__ import annotations

import os
import tempfile

from tests.harness import config
from tests.harness.contract import Cell, Leak, Locus, V
from tests.harness.oracle import fields, fingerprint_guard, leak, variance
from tests.harness.plugins.heic import HeicPlugin
from tests.harness.runner import matrix
from tests.scrub import heic_corpus as hc

TOOL = {
    "name": "irreversible_scrubber",
    "version": "0.1.0-p4",
    "invocation": "python -m src.scrub {in} {out} --fidelity {fidelity}",
}


def _scrubber():
    from tests.scrub.test_harness_a1 import InProcessScrubber
    return InProcessScrubber()


def _a1_variants(tmpdir: str, n_variants: int = 3, n_repeats: int = 3):
    """Same tiles, same container, metadata differing only by a sentinel.

    Content is identical by construction — the tiles come from one encode — so every
    difference between two scrubbed outputs is metadata-correlated and nothing else.
    """
    groups = []
    for i in range(n_variants):
        marker = "-" + chr(65 + i) * 6
        path = os.path.join(tmpdir, f"a1_v{i}.heic")
        hc.handbuilt(path, variant=marker)
        blob = open(path, "rb").read()
        paths = []
        for r in range(n_repeats):
            copy = os.path.join(tmpdir, f"a1_v{i}_r{r}.heic")
            open(copy, "wb").write(blob)
            paths.append(copy)
        groups.append(paths)
    return groups


def _producers(tmpdir: str, repeats: int = 3) -> dict[str, list[str]]:
    """The A2 peer set: one picture, three container writers.

    The tiles are byte-identical across all three — same encoder, same quality, same
    source — so the only thing separating a `heic`-branded file with Apple's table
    order from a `mif1`-branded one with wider `iloc` fields is the software that
    wrote the container.
    """
    sources: dict[str, list[str]] = {}
    for name in hc.PRODUCERS:
        blob = None
        paths = []
        for r in range(repeats):
            path = os.path.join(tmpdir, f"{name}__r{r}.heic")
            if blob is None:
                hc.handbuilt(path, producer=name)
                blob = open(path, "rb").read()
            else:
                open(path, "wb").write(blob)
            paths.append(path)
        sources[name] = paths
    return sources


def _a2_cell(fidelity: str, sources: dict[str, list[str]], tmpdir: str) -> Cell:
    from src.scrub import cli
    plugin = HeicPlugin()
    producers = list(sources)
    repeats = list(range(min(len(v) for v in sources.values())))

    cells = {}
    for prod, paths in sources.items():
        for r, src in enumerate(paths):
            out = os.path.join(tmpdir, f"{prod}_{r}_{fidelity}.heic")
            cli.scrub_file(src, out, fidelity)
            cells[(prod, r)] = fields.extract(out, plugin, "F3")
    verdicts = variance.decompose(cells, producers, repeats)
    leaking = {fid: v for fid, v in verdicts.items()
               if v.leak and fid.startswith("struct:")}

    if not leaking:
        return Cell("A2", fidelity, V.PASS,
                    reason="container layout normalized; peer set = "
                           + ", ".join(producers))

    leaks = [Leak("source_fingerprint", Locus("structural", feature_id=fid),
                  f"producer (across {v.n_between})",
                  f"{fid} constant within producer, differs across")
             for fid, v in leaking.items()]
    return Cell("A2", fidelity, V.FAIL, leaks=leaks, reason=(
        "container layout survives F1 — this tier relocates items and rebuilds the "
        "tables, and deliberately changes nothing else, so every writer choice is "
        f"passed through: {', '.join(sorted(leaking))}. Peer set = "
        f"{', '.join(producers)} (container-writer profiles wrapping BYTE-IDENTICAL "
        "tiles, so the separation is the muxer and not the picture). The coded HEVC "
        "tiles separate producers too and are not counted here — F1 does not "
        "re-encode, so that is a property of the tier rather than a finding. Closing "
        "this cell needs an F2 that normalizes brand, table order, `iloc` widths and "
        "item numbering losslessly; the named features above are its specification."))


def _diverse(tmpdir: str, n: int = 4) -> list[str]:
    """Inputs that share nothing, so a run present in every output is ours."""
    paths = []
    names = list(hc.PRODUCERS)
    for i in range(n):
        path = os.path.join(tmpdir, f"div_{i}.heic")
        hc.handbuilt(path, producer=names[i % len(names)], seed=i * 37 + 1,
                     quality=50 + i * 10, tile=48 + i * 16,
                     with_aux=(i % 2 == 0), with_thumbnail=(i % 3 != 0),
                     with_uri=(i % 2 == 1), variant=f"-div{i}")
        paths.append(path)
    return paths


def build_doc(tmpdir: str) -> dict:
    plugin = HeicPlugin()
    scrubber = _scrubber()

    cells = [
        leak.evaluate_a1(scrubber, plugin, _a1_variants(tmpdir), "F1", n=3,
                         sentinel_field="metadata_variant", modality="bytes"),
        Cell("A1", "F2", V.NOT_TESTED, reason="heic_f2_not_built"),
        Cell("A1", "F3", V.NOT_TESTED, reason="heic_f3_not_built"),
        _a2_cell("F1", _producers(tmpdir), tmpdir),
        Cell("A2", "F2", V.NOT_TESTED, reason=(
            "heic_f2_not_built: a lossless container normalization (one brand, one "
            "table order, one set of `iloc` widths, canonical item numbering) is the "
            "next step and is what the A2@F1 leaks specify")),
        Cell("A2", "F3", V.NOT_TESTED, reason=(
            "heic_f3_not_built: would mean re-emitting the HEVC tiles, which Phase 4 "
            "has not measured")),
    ]

    diverse = _diverse(tmpdir, n=4)
    guard_verdict, signatures = fingerprint_guard.evaluate(
        scrubber, plugin, diverse, "F1", min_len=config.MIN_SIG_LEN)
    excluded = [{"bytes_hex": c.hex(), "decoded": c.decode("latin-1", "replace")}
                for c in plugin.mandatory_constants()]
    fp = matrix.fingerprint_block(guard_verdict, signatures, excluded)
    return matrix.assemble("heic", TOOL, cells, fp)


def main() -> str:
    tmpdir = tempfile.mkdtemp(prefix="heic_matrix_")
    doc = build_doc(tmpdir)
    out_path = os.path.join(str(config.RESULTS_DIR), f"heic_{TOOL['name']}.json")
    matrix.write(doc, out_path)
    return out_path


if __name__ == "__main__":
    print(main())
