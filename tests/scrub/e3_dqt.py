"""E3 — DQT producer-fingerprint experiment (reproduce Kornblum).

Hypothesis (p1 plan §2 E3): the JPEG quantization tables (DQT) classify the
*producer* independently of scene content, and survive metadata scrubbing at
F1/F2 — so A2@F1/F2 = fail, empirically, which is what lets the Pareto matrix
state "JPEG A2 requires F3" as a measured fact rather than a citation.

Method: a peer corpus of N producers x M scenes (built by build_e3_corpus).
For each condition (raw / F1 / F2) we scrub every file, extract the structural
feature channel (DQT/DHT/marker-skeleton/SOF via JpegPlugin.structural_features),
and run THE variance engine with grouping = producer. A feature that is
(near-)constant within each producer yet differs across producers is a
producer-fingerprint (leak == True). If the DQT is such a feature after F1/F2,
the fingerprint survived the scrub.

This is measurement, not assertion: if a producer's DQT turns out content-adaptive
(varies by scene), that shows up as within-group variance and the feature is NOT
flagged — the experiment records whatever is true.
"""
from __future__ import annotations

import glob
import os
import tempfile

from tests.harness.oracle import fields, variance
from tests.harness.plugins.jpeg import JpegPlugin

# The peer corpus lives inside the repo at tests/corpus/e3/ (git-ignored binaries
# — see that dir's README). Resolved relative to the repo root so the experiment
# is self-contained; group_corpus() returns {} on a fresh clone (no images) and
# callers fall back to not_tested honestly.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
DEFAULT_CORPUS = os.path.join(_REPO_ROOT, "tests", "corpus", "e3")

# structural features that are producer-choices (candidate fingerprints), vs
# content-derived facts (size/geometry) that legitimately vary by scene.
_STRUCT_ENCODER_KEYS = ("struct:dqt", "struct:dht", "struct:markers", "struct:sof")


def group_corpus(corpus_dir: str = DEFAULT_CORPUS) -> dict[str, list[str]]:
    """Map producer -> [scene paths], from `<producer>__scene<i>.jpg` names."""
    groups: dict[str, list[str]] = {}
    for path in sorted(glob.glob(os.path.join(corpus_dir, "*.jpg"))):
        base = os.path.basename(path)
        if "__scene" not in base:
            continue
        producer = base.split("__scene")[0]
        groups.setdefault(producer, []).append(path)
    return groups


def _feature_grid(groups: dict[str, list[str]], fidelity: str, plugin,
                  tmpdir: str) -> dict[tuple[str, int], dict]:
    """Scrub every file at `fidelity` ('raw' = no scrub) and extract its feature
    map. Grid key = (producer, scene index)."""
    from src.scrub import cli
    cells: dict[tuple[str, int], dict] = {}
    for producer, paths in groups.items():
        for r, src in enumerate(paths):
            if fidelity == "raw":
                target = src
            else:
                target = os.path.join(tmpdir, f"{producer}_{r}_{fidelity}.jpg")
                cli.scrub_file(src, target, fidelity)
            cells[(producer, r)] = fields.extract(target, plugin, "F3")
    return cells


def run_condition(fidelity: str, corpus_dir: str = DEFAULT_CORPUS) -> dict:
    """Run one condition and return the producer-fingerprint analysis."""
    plugin = JpegPlugin()
    groups = group_corpus(corpus_dir)
    if len(groups) < 2:
        raise SystemExit(f"need >=2 producers in {corpus_dir}, found {list(groups)}")
    producers = list(groups)
    repeats = list(range(min(len(v) for v in groups.values())))

    tmpdir = tempfile.mkdtemp(prefix=f"e3_{fidelity}_")
    cells = _feature_grid(groups, fidelity, plugin, tmpdir)
    verdicts = variance.decompose(cells, producers, repeats)

    fingerprints = {fid: v for fid, v in verdicts.items() if v.leak}
    struct_fps = {fid: v for fid, v in fingerprints.items() if fid.startswith("struct:")}
    return {
        "fidelity": fidelity,
        "producers": producers,
        "scenes": len(repeats),
        "all_fingerprints": fingerprints,
        "struct_fingerprints": struct_fps,
        "dqt": verdicts.get("struct:dqt"),
        # A2 verdict: fail if any encoder-structural feature still separates producers
        "a2_fail": any(fid in fingerprints for fid in _STRUCT_ENCODER_KEYS),
    }


def evaluate_cell(fidelity: str, corpus_dir: str = DEFAULT_CORPUS):
    """Run E3 at `fidelity` and return a Pareto-matrix Cell (A2). Fail = a
    producer's encoder structure (esp. DQT) still separates the peer set after
    scrubbing. Raises if the corpus has <2 producers (caller decides not_tested)."""
    from tests.harness.contract import Cell, Leak, Locus, V
    r = run_condition(fidelity, corpus_dir)
    leaks = []
    for fid, v in r["struct_fingerprints"].items():
        leaks.append(Leak(
            kind="source_fingerprint",
            locus=Locus(space="structural", feature_id=fid),
            correlates_with=f"producer (distinct across {v.n_between} of "
                            f"{len(r['producers'])})",
            evidence=f"{fid} constant within producer (max_within="
                     f"{v.max_within}), differs across ({v.n_between})"))
    if r["a2_fail"]:
        verdict = V.FAIL
        reason = ("dqt_producer_fingerprint_survives (Kornblum, E3): "
                  + ", ".join(sorted(r["struct_fingerprints"])))
    else:
        verdict = V.PASS
        # Pass-with-residuals: E3 covers the structural/DQT channel only. Signal
        # residuals the experiment does NOT probe so the matrix never over-claims.
        reason = ("no encoder-structural feature separates producers after scrub "
                  "(DQT normalized); residuals NOT tested here: PRNU sensor noise "
                  "(Lukas, structural impossibility), primary-quantization traces "
                  "(Sorell, E4)")
    return Cell("A2", fidelity, verdict, leaks=leaks, reason=reason)


def _fmt(v) -> str:
    if v is None:
        return "absent"
    tag = "FINGERPRINT" if v.leak else "no-sep"
    return f"{tag} (across={v.n_between}, within_max={v.max_within})"


def main(corpus_dir: str = DEFAULT_CORPUS) -> None:
    groups = group_corpus(corpus_dir)
    print(f"E3 corpus: {len(groups)} producers x "
          f"{min((len(v) for v in groups.values()), default=0)} scenes  ({corpus_dir})")
    for p, paths in groups.items():
        print(f"  {p}: {len(paths)} scenes")
    print()
    for fidelity in ("raw", "F1", "F2", "F3"):
        r = run_condition(fidelity, corpus_dir)
        print(f"[{fidelity:3}] DQT: {_fmt(r['dqt'])}   "
              f"| struct fingerprints: {sorted(r['struct_fingerprints'])}   "
              f"| A2 => {'FAIL (producer recoverable)' if r['a2_fail'] else 'PASS'}")


if __name__ == "__main__":
    import sys
    main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CORPUS)
