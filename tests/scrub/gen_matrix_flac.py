"""Generate the FLAC Pareto matrix from real harness runs.

Honesty rule (CLAUDE.md): a cell is only pass/fail if we measured it.

Measured here:
  - A1@F1, A1@F2  differential leak oracle over same-audio / different-tag FLACs.
  - A2@F1, A2@F2  encoder-structure peer set (experiment E-FLAC): F1 keeps each
                  producer's block-size and framing choices (fail); F2 re-encodes
                  through one locked setting and normalizes them (pass) — with
                  BIT-IDENTICAL audio.
  - F3            not_applicable: FLAC is lossless, so a lossy tier would destroy
                  audio to buy nothing F2 has not already bought.

This is the audio counterpart of the PNG result: A2 defeated at zero quality cost,
the only place in Phase 2 where that is possible (MP3 needs a lossy F3 because its
signature is baked into the compressed audio).

Run:  ./.venv/bin/python -m tests.scrub.gen_matrix_flac
"""
from __future__ import annotations

import os
import tempfile

from tests.harness import config
from tests.harness.contract import Cell, V
from tests.harness.oracle import fingerprint_guard, leak
from tests.harness.plugins.flac import FlacPlugin
from tests.harness.runner import matrix
from tests.scrub import e_flac
from tests.scrub import flac_corpus as fc

TOOL = {
    "name": "irreversible_scrubber",
    "version": "0.1.0-p2",
    "invocation": "python -m src.scrub {in} {out} --fidelity {fidelity}",
}


def _scrubber():
    from tests.scrub.test_harness_a1 import InProcessScrubber
    return InProcessScrubber()


def _diverse(tmpdir, n=4):
    specs = [(330, 0.4, 44100), (440, 0.4, 22050), (550, 0.4, 44100), (660, 0.4, 48000)]
    paths = []
    for i, (freq, dur, rate) in enumerate(specs[:n]):
        p = os.path.join(tmpdir, f"div_{i}.flac")
        fc.base_flac(p, freq=freq, dur=dur, rate=rate)
        paths.append(p)
    return paths


def build_doc(tmpdir: str) -> dict:
    plugin = FlacPlugin()
    scrubber = _scrubber()

    # --- A1: same audio, tags differing only by a sentinel ---
    cells = []
    for fid in ("F1", "F2"):
        variants = fc.a1_variants(tmpdir, n_variants=3, n_repeats=5)
        cells.append(leak.evaluate_a1(scrubber, plugin, variants, fid, n=5,
                                      sentinel_field="metadata_variant",
                                      modality="bytes"))
    cells.append(Cell("A1", "F3", V.NOT_APPLICABLE,
                      reason="lossy_tier_not_applicable_to_lossless_format"))

    # --- A2: encoder-structure peer set ---
    sources = e_flac.build_sources(tmpdir, repeats=3)
    cells.append(e_flac.evaluate_cell("F1", sources, tmpdir))
    cells.append(e_flac.evaluate_cell("F2", sources, tmpdir))
    cells.append(Cell("A2", "F3", V.NOT_APPLICABLE,
                      reason="lossy_tier_not_applicable_to_lossless_format"))

    # --- fingerprint guard over diverse inputs ---
    diverse = _diverse(tmpdir, n=4)
    gv, gsig = fingerprint_guard.evaluate(scrubber, plugin, diverse, "F1",
                                          min_len=config.MIN_SIG_LEN)
    excluded = [{"bytes_hex": c.hex(), "decoded": c.decode("latin-1", "replace")}
                for c in plugin.mandatory_constants()]
    fp = matrix.fingerprint_block(gv, gsig, excluded)
    return matrix.assemble("flac", TOOL, cells, fp)


def main() -> str:
    tmpdir = tempfile.mkdtemp(prefix="flac_matrix_")
    doc = build_doc(tmpdir)
    out_path = os.path.join(str(config.RESULTS_DIR), f"flac_{TOOL['name']}.json")
    matrix.write(doc, out_path)
    return out_path


if __name__ == "__main__":
    print(main())
