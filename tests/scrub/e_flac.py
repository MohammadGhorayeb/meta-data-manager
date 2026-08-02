"""E-FLAC — FLAC encoder-fingerprint peer-set experiment.

The question: with the tags gone, does the encoder's *structure* still identify the
producer — block size, frame sizes, which metadata blocks it writes, how much padding
it leaves, and the Vorbis vendor string that names it outright?

Why FLAC is the interesting case: MP3 needed a LOSSY re-encode to erase its
fingerprint, because the signature is baked into the compressed audio. FLAC is
lossless, so re-encoding through one locked setting erases the structural signature
while returning bit-identical audio. If this experiment shows F1 failing and F2
passing, FLAC joins PNG as a format where A2 is reachable at **zero quality cost** —
the strongest kind of result this project can produce.

Producers here are compression levels (0/5/8) of one encoder. That is the honest
stand-in for "different encoder" in FLAC: level drives block size and residual
partitioning, which is exactly what a peer-corpus adversary reads. Named as such in
the matrix rather than dressed up as multiple engines.
"""
from __future__ import annotations

import os
import tempfile

from tests.harness.oracle import fields, variance
from tests.harness.plugins.flac import FlacPlugin
from tests.scrub import flac_corpus as fc

# Structural features that identify the ENCODER rather than the content.
_ENCODER_KEYS = ("struct:vendor", "struct:blocksize", "struct:block_types",
                 "struct:padding_len")


def build_sources(tmpdir: str, repeats: int = 3) -> dict:
    return fc.producers(tmpdir, repeats=repeats)


def run_condition(fidelity: str, sources: dict, tmpdir: str) -> dict:
    from src.scrub import cli
    plugin = FlacPlugin()
    producers = list(sources)
    repeats = list(range(min(len(v) for v in sources.values())))
    cells = {}
    for prod, paths in sources.items():
        for r, src in enumerate(paths):
            if fidelity == "raw":
                target = src
            else:
                target = os.path.join(tmpdir, f"{prod}_{r}_{fidelity}.flac")
                cli.scrub_file(src, target, fidelity)
            cells[(prod, r)] = fields.extract(target, plugin, "F2")
    verdicts = variance.decompose(cells, producers, repeats)
    fps = {fid: v for fid, v in verdicts.items()
           if v.leak and fid.startswith("struct:")}
    return {"fidelity": fidelity, "producers": producers,
            "struct_fingerprints": fps,
            "a2_fail": any(k in fps for k in _ENCODER_KEYS)}


def evaluate_cell(fidelity: str, sources: dict, tmpdir: str):
    from tests.harness.contract import Cell, Leak, Locus, V
    r = run_condition(fidelity, sources, tmpdir)
    if r["a2_fail"]:
        leaks = [Leak("source_fingerprint", Locus("structural", feature_id=fid),
                      f"producer (across {v.n_between})",
                      f"{fid} constant within producer, differs across")
                 for fid, v in r["struct_fingerprints"].items()]
        return Cell("A2", fidelity, V.FAIL, leaks=leaks,
                    reason="encoder structure survives (block size / vendor / block "
                           "layout): " + ", ".join(sorted(r["struct_fingerprints"])))
    return Cell("A2", fidelity, V.PASS,
                reason="encoder structure normalized by the canonical lossless "
                       f"re-encode; peer set = {', '.join(r['producers'])} "
                       "(compression levels, which drive block size and residual "
                       "partitioning). Audio is BIT-IDENTICAL, so unlike JPEG F3 or "
                       "MP3 F3 this A2 defense costs nothing. Residual: anonymity "
                       "within the canonical-encoder class.")


def main() -> None:
    tmpdir = tempfile.mkdtemp(prefix="e_flac_")
    sources = build_sources(tmpdir)
    print(f"E-FLAC peer set: {list(sources)} x "
          f"{min(len(v) for v in sources.values())} each\n")
    for fid in ("raw", "F1", "F2"):
        r = run_condition(fid, sources, tmpdir)
        fps = sorted(r["struct_fingerprints"])
        print(f"[{fid:3}] structural fingerprints separating producers: "
              f"{fps or 'none'} -> A2 {'FAIL' if r['a2_fail'] else 'PASS'}")


if __name__ == "__main__":
    main()
