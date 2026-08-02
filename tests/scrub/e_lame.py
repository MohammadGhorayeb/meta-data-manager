"""E-LAME — MP3 encoder-fingerprint peer-set experiment (the DQT analogue for audio).

Hypothesis: the encoder signature (LAME/Xing version string + CBR/VBR flag +
per-frame bitrate contour) classifies the *producer* and survives F1 (metadata
strip keeps the audio frames), so A2@F1 = fail; F3 re-encodes through one canonical
LAME setting so all producers collapse to one signature, so A2@F3 = pass.

MEASUREMENT SCOPE (honest, per the design decision): this experiment lives in
HEADER space — the categorical structural channel (Xing/LAME string, bitrate
contour, channel mode). Its peer set now includes a genuinely different *engine*
(`shine_cbr`, fixed-point, emits no Xing/LAME header at all) alongside the two LAME
front-ends, so the collapse at F3 is measured across engines, not just front-ends.

The complementary question — whether the source engine is still recoverable from the
decoded *audio* after F3, since header normalization says nothing about the
waveform — is experiment E-ENGINE (`tests/scrub/e_engine.py`). Read both before
quoting the A2@F3 cell.
"""
from __future__ import annotations

import os
import tempfile

from tests.harness.oracle import fields, variance
from tests.harness.plugins.mp3 import MP3Plugin
from tests.scrub import mp3_corpus as mc

_ENCODER_KEYS = ("struct:lame_version", "struct:xing_magic", "struct:bitrate_hist")


def build_sources(tmpdir: str, repeats: int = 3) -> dict:
    return mc.producers(tmpdir, repeats=repeats)


def run_condition(fidelity: str, sources: dict, tmpdir: str) -> dict:
    from src.scrub import cli
    plugin = MP3Plugin()
    producers = list(sources)
    repeats = list(range(min(len(v) for v in sources.values())))
    cells = {}
    for prod, paths in sources.items():
        for r, src in enumerate(paths):
            if fidelity == "raw":
                target = src
            else:
                target = os.path.join(tmpdir, f"{prod}_{r}_{fidelity}.mp3")
                cli.scrub_file(src, target, fidelity)
            cells[(prod, r)] = fields.extract(target, plugin, "F3")
    verdicts = variance.decompose(cells, producers, repeats)
    fps = {fid: v for fid, v in verdicts.items()
           if v.leak and fid.startswith("struct:")}
    return {"fidelity": fidelity, "producers": producers,
            "struct_fingerprints": fps,
            "a2_fail": any(k in fps for k in _ENCODER_KEYS)}


def evaluate_cell(fidelity: str, sources: dict, tmpdir: str, audio_note: str = ""):
    from tests.harness.contract import Cell, Leak, Locus, V
    r = run_condition(fidelity, sources, tmpdir)
    if r["a2_fail"]:
        leaks = [Leak("source_fingerprint", Locus("structural", feature_id=fid),
                      f"producer (across {v.n_between})",
                      f"{fid} constant within producer, differs across")
                 for fid, v in r["struct_fingerprints"].items()]
        return Cell("A2", fidelity, V.FAIL, leaks=leaks,
                    reason="encoder_fingerprint_survives (LAME/Xing + bitrate "
                           "contour): " + ", ".join(sorted(r["struct_fingerprints"])))
    engines = "cross-engine" if any("shine" in p for p in r["producers"]) else \
              "front-ends of one engine (no second engine available)"
    return Cell("A2", fidelity, V.PASS,
                reason="encoder fingerprint normalized to the canonical LAME class; "
                       f"peer set = {', '.join(r['producers'])} ({engines})."
                       + (" " + audio_note if audio_note else "")
                       + " Residual: anonymity within the LAME-192-CBR class "
                         "+ one generation of loss.")


def main() -> None:
    tmpdir = tempfile.mkdtemp(prefix="e_lame_")
    sources = build_sources(tmpdir)
    print(f"E-LAME peer set: {list(sources)} x "
          f"{min(len(v) for v in sources.values())} each\n")
    for fid in ("raw", "F1", "F3"):
        r = run_condition(fid, sources, tmpdir)
        fps = sorted(r["struct_fingerprints"])
        print(f"[{fid:3}] encoder fingerprints separating producers: {fps or 'none'} "
              f"-> A2 {'FAIL' if r['a2_fail'] else 'PASS'}")


if __name__ == "__main__":
    main()
