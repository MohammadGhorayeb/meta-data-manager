"""Generate the MP3 Pareto matrix from real harness runs.

Measured:
  - A1@F1, A1@F3   differential leak oracle over same-audio / different-tag MP3s.
  - A2@F1, A2@F3   encoder-fingerprint peer set (experiment E-LAME): F1 keeps each
                   producer's LAME/Xing signature (fail); F3 collapses all to the
                   canonical LAME-192-CBR class (pass). The peer set spans two LAME
                   front-ends AND a different engine (shineenc), and the A2@F3 cell
                   additionally carries E-ENGINE — the audio-space check that the
                   source engine is not still recoverable from the waveform once the
                   header is normalized.
  - F2 = not_applicable: MP3 frames are the content; no lossless re-encode changes
                   the encoder fingerprint.
F3/A2 cells need the `lame` CLI; without it they are honestly not_tested.

Run:  ./.venv/bin/python -m tests.scrub.gen_matrix_mp3
"""
from __future__ import annotations

import os
import tempfile

from tests.harness import config
from tests.harness.contract import Cell, V
from tests.harness.oracle import fingerprint_guard, leak
from tests.harness.plugins.mp3 import MP3Plugin
from tests.harness.runner import matrix
from tests.scrub import e_engine, e_lame
from tests.scrub import mp3_corpus as mc

TOOL = {
    "name": "irreversible_scrubber",
    "version": "0.1.0-p2",
    "invocation": "python -m src.scrub {in} {out} --fidelity {fidelity}",
}


def _scrubber():
    from tests.scrub.test_harness_a1 import InProcessScrubber
    return InProcessScrubber()


def _diverse(tmpdir, n=4):
    paths = []
    specs = [("ff", 330, 0.4), ("lm", 440, 0.4), ("ff", 550, 0.4), ("lm", 660, 0.4)]
    for i, (who, freq, dur) in enumerate(specs[:n]):
        p = os.path.join(tmpdir, f"div_{i}.mp3")
        if who == "lm" and mc.HAVE_LAME:
            mc.base_mp3_lame(p, freq=freq, dur=dur)
        else:
            mc.base_mp3_ffmpeg(p, freq=freq, dur=dur)
        paths.append(p)
    return paths


def _audio_note(tmpdir: str) -> str:
    """Header normalization says nothing about the waveform, so the A2@F3 claim also
    carries E-ENGINE: can a peer-corpus adversary still classify the source ENGINE
    from the decoded audio after F3? Needs a second engine (shineenc); without one
    the cell says so rather than implying the question was answered."""
    if not mc.HAVE_SHINE:
        return ("Audio-space cross-engine residual NOT measured here "
                "(no second engine available: install shineenc) — see E-ENGINE.")
    parts, bad = [], []
    for rate in mc.RATES:
        try:
            r = e_engine.run(fidelities=("raw", "F1", "F3"),
                             tmpdir=os.path.join(tmpdir, f"e_engine_{rate}"),
                             rate=rate)
        except Exception as exc:                  # never let an experiment break the matrix
            bad.append(f"{rate} Hz failed: {exc}")
            continue
        f3, raw = r["F3"], r["raw"]
        if not e_engine.controls_valid(r):
            bad.append(f"{rate} Hz INCONCLUSIVE (control accuracy "
                       f"{raw['accuracy']:.2f} vs chance {raw['chance']:.2f})")
        elif f3["recoverable"]:
            bad.append(f"{rate} Hz RESIDUAL: engine still recoverable after F3 at "
                       f"{f3['accuracy']:.2f} vs chance {f3['chance']:.2f}")
        else:
            parts.append(f"{rate} Hz: {raw['accuracy']:.2f} -> {f3['accuracy']:.2f} "
                         f"(chance {f3['chance']:.2f})")
    if bad:
        return "Audio-space cross-engine result NOT clean: " + "; ".join(bad + parts)
    return ("Cross-engine audio residual measured and NOT found, per sample-rate "
            "group — engine classification accuracy unscrubbed -> after F3, "
            + "; ".join(parts) +
            " (E-ENGINE, leave-one-content-out over broadband audio). Scope: "
            "spectral features; MDCT-domain classifiers untested.")


def build_doc(tmpdir: str) -> dict:
    plugin = MP3Plugin()
    scrubber = _scrubber()
    have_lame = mc.HAVE_LAME

    # --- A1 (bytes-space; F3's own NCC gate covers content, so skip the oracle's
    #     fpcalc-dependent audio perceptual path) ---
    v1 = mc.a1_variants(tmpdir, n_variants=3, n_repeats=5)
    a1f1 = leak.evaluate_a1(scrubber, plugin, v1, "F1", n=5,
                            sentinel_field="metadata_variant", modality="bytes")
    cells = [a1f1, Cell("A1", "F2", V.NOT_APPLICABLE,
                        reason="lossless_reencode_not_applicable_to_mp3")]
    if have_lame:
        v3 = mc.a1_variants(tmpdir, n_variants=3, n_repeats=5)
        a1f3 = leak.evaluate_a1(scrubber, plugin, v3, "F3", n=5,
                                sentinel_field="metadata_variant", modality="bytes")
        cells.append(a1f3)
    else:
        cells.append(Cell("A1", "F3", V.NOT_TESTED, reason="lame_unavailable"))

    # --- A2 (encoder-fingerprint peer set) ---
    cells.append(Cell("A2", "F2", V.NOT_APPLICABLE,
                      reason="lossless_reencode_not_applicable_to_mp3"))
    if have_lame:
        sources = e_lame.build_sources(tmpdir, repeats=3)
        cells.append(e_lame.evaluate_cell("F1", sources, tmpdir))
        cells.append(e_lame.evaluate_cell("F3", sources, tmpdir, audio_note=_audio_note(tmpdir)))
    else:
        cells.append(Cell("A2", "F1", V.NOT_TESTED, reason="lame_unavailable"))
        cells.append(Cell("A2", "F3", V.NOT_TESTED, reason="lame_unavailable"))

    # --- fingerprint guard over diverse short inputs (F1) ---
    diverse = _diverse(tmpdir, n=4)
    gv, gsig = fingerprint_guard.evaluate(scrubber, plugin, diverse, "F1",
                                          min_len=config.MIN_SIG_LEN)
    excluded = [{"bytes_hex": c.hex(), "decoded": c.decode("latin-1", "replace")}
                for c in plugin.mandatory_constants()]
    fp = matrix.fingerprint_block(gv, gsig, excluded)
    return matrix.assemble("mp3", TOOL, cells, fp)


def main() -> str:
    tmpdir = tempfile.mkdtemp(prefix="mp3_matrix_")
    doc = build_doc(tmpdir)
    out_path = os.path.join(str(config.RESULTS_DIR), f"mp3_{TOOL['name']}.json")
    matrix.write(doc, out_path)
    return out_path


if __name__ == "__main__":
    print(main())
