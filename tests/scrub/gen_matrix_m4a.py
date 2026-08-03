"""Generate the M4A Pareto matrix from real harness runs.

Honesty rule (CLAUDE.md): a cell is only pass/fail if we measured it.

Measured:
  - A1@F1/F2/F3   differential leak oracle over same-audio / different-tag M4As.
  - A2@F1         muxer layout AND coded audio both separate producers.
  - A2@F2         muxer layout normalized by the canonical re-mux (lossless: the
                  coded stream is copied); the AAC encoder fingerprint inside that
                  stream is untouched, so the cell still fails and says why.
  - A2@F3         canonical re-encode. Producers whose first-generation audio was
                  identical collapse to byte-identical output; a source encoded at a
                  different bitrate still leaves a primary-encoding trace, recorded
                  as an open residual rather than waved through.

MAT2 refuses M4A outright, so every one of these cells is a row the benchmark table
cannot currently fill.

Run:  ./.venv/bin/python -m tests.scrub.gen_matrix_m4a
"""
from __future__ import annotations

import os
import tempfile

from tests.harness import config
from tests.harness.oracle import fingerprint_guard, leak
from tests.harness.plugins.m4a import M4aPlugin
from tests.harness.runner import matrix
from tests.scrub import e_m4a
from tests.scrub import m4a_corpus as mc

TOOL = {
    "name": "irreversible_scrubber",
    "version": "0.1.0-p2",
    "invocation": "python -m src.scrub {in} {out} --fidelity {fidelity}",
}


def _scrubber():
    from tests.scrub.test_harness_a1 import InProcessScrubber
    return InProcessScrubber()


def _diverse(tmpdir, n=4):
    specs = [(330, 44100, False), (440, 22050, True), (550, 44100, True),
             (660, 48000, False)]
    paths = []
    for i, (freq, rate, fast) in enumerate(specs[:n]):
        p = os.path.join(tmpdir, f"div_{i}.m4a")
        mc.base_m4a(p, freq=freq, dur=0.6, rate=rate, faststart=fast)
        paths.append(p)
    return paths


def _audio_note(tmpdir: str) -> str:
    """Settle the audio-space question the container channel cannot answer: after
    F3, is the SOURCE's own encode still recoverable from the sound? Needs a second
    AAC engine to be a fair peer set; without one the cell says so rather than
    implying the question was answered."""
    from tests.scrub import e_m4a_audio
    if not e_m4a_audio.have_second_engine():
        return ("Audio-space residual NOT measured here (needs a second AAC engine; "
                "`aac_at` is macOS-only and no standard Linux ffmpeg ships one) — "
                "see E-M4A-AUDIO.")
    try:
        r = e_m4a_audio.run(tmpdir=os.path.join(tmpdir, "e_audio"))
    except Exception as exc:                      # never let an experiment break the matrix
        return f"Audio-space residual not measured (E-M4A-AUDIO failed: {exc})."
    if not e_m4a_audio.controls_valid(r):
        return ("Audio-space result INCONCLUSIVE: the classifier could not recover "
                "the source setting even from unscrubbed files, so its failure after "
                "F3 proves nothing.")
    raw, f3c = r["raw"], r["F3"]
    eng = r["raw"]["engine"]
    verdict = ("still recoverable" if f3c["recoverable"] else "falls to chance")
    return (f"Audio-space measurement (E-M4A-AUDIO): the source's own quality "
            f"setting is recoverable from the sound at {raw['accuracy']:.2f} on "
            f"unscrubbed and copy-tier files and {verdict} after F3 "
            f"({f3c['accuracy']:.2f} vs chance {f3c['chance']:.2f}). Engine identity "
            f"(ffmpeg AAC vs Apple AAC) is NOT separable even unscrubbed "
            f"({eng['accuracy']:.2f}) — those two encoders are near-twins, so this "
            f"corpus cannot answer the engine question either way. Scope: spectral "
            f"features; MDCT-domain classifiers untested.")


def build_doc(tmpdir: str) -> dict:
    plugin = M4aPlugin()
    scrubber = _scrubber()

    cells = []
    for fid in ("F1", "F2", "F3"):
        variants = mc.a1_variants(tmpdir, n_variants=3, n_repeats=3)
        cells.append(leak.evaluate_a1(scrubber, plugin, variants, fid, n=3,
                                      sentinel_field="metadata_variant",
                                      modality="bytes"))

    sources = e_m4a.build_sources(tmpdir, repeats=3)
    for fid in ("F1", "F2", "F3"):
        note = _audio_note(tmpdir) if fid == "F3" else ""
        cells.append(e_m4a.evaluate_cell(fid, sources, tmpdir, audio_note=note))

    diverse = _diverse(tmpdir, n=4)
    gv, gsig = fingerprint_guard.evaluate(scrubber, plugin, diverse, "F1",
                                          min_len=config.MIN_SIG_LEN)
    excluded = [{"bytes_hex": c.hex(), "decoded": c.decode("latin-1", "replace")}
                for c in plugin.mandatory_constants()]
    fp = matrix.fingerprint_block(gv, gsig, excluded)
    return matrix.assemble("m4a", TOOL, cells, fp)


def main() -> str:
    tmpdir = tempfile.mkdtemp(prefix="m4a_matrix_")
    doc = build_doc(tmpdir)
    out_path = os.path.join(str(config.RESULTS_DIR), f"m4a_{TOOL['name']}.json")
    matrix.write(doc, out_path)
    return out_path


if __name__ == "__main__":
    print(main())
