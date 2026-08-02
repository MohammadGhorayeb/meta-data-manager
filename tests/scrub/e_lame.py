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


def build_sources(tmpdir: str, repeats: int = 3, rates=mc.RATES) -> dict:
    """Peer set spanning several sample rates by default.

    A single-rate peer set cannot see how F3 behaves at other rates, and that blind
    spot hid a real property of the tool: the canonical 192 kbps CBR is illegal for
    MPEG-2 sample rates, so those files emit at 160 instead. Every producer encodes
    at every rate, so the rate varies within each group and only genuine PRODUCER
    separation counts as a leak.
    """
    return mc.producers_mixed(tmpdir, repeats=repeats, rates=rates)


def per_rate_report(fidelity: str, tmpdir: str, repeats: int = 2,
                    rates=mc.RATES) -> dict:
    """The sharper test: hold sample rate FIXED and ask whether producers separate.

    Anonymity is claimed per rate group, so it has to be demonstrated per rate group.
    Aggregating rates could mask a leak inside one of them — a feature that separates
    producers at 22.05 kHz would be diluted by 44.1 kHz files that do not.
    """
    out = {}
    for rate in rates:
        sub = os.path.join(tmpdir, f"rate{rate}")
        os.makedirs(sub, exist_ok=True)
        sources = mc.producers(sub, repeats=repeats, rate=rate)
        out[rate] = run_condition(fidelity, sources, sub)
    return out


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
    # Anonymity is per sample-rate GROUP, so the per-group evidence goes in the cell
    # rather than being implied by an aggregate that could mask a leak in one group.
    per_rate = per_rate_report(fidelity, os.path.join(tmpdir, "perrate"), repeats=2)
    groups = "; ".join(
        f"{rate} Hz: {'FAIL' if g['a2_fail'] else 'pass'}"
        for rate, g in per_rate.items())
    if any(g["a2_fail"] for g in per_rate.values()):
        leaks = [Leak("source_fingerprint", Locus("structural", feature_id=fid),
                      "producer (within one sample-rate group)",
                      f"{fid} separates producers at {rate} Hz")
                 for rate, g in per_rate.items() if g["a2_fail"]
                 for fid in g["struct_fingerprints"]]
        return Cell("A2", fidelity, V.FAIL, leaks=leaks,
                    reason="encoder fingerprint survives inside a sample-rate group: "
                           + groups)
    return Cell("A2", fidelity, V.PASS,
                reason="encoder fingerprint normalized to the canonical LAME class; "
                       f"peer set = {', '.join(r['producers'])} ({engines}), "
                       f"sample rates {list(mc.RATES)}. Verified per rate group — "
                       f"{groups}."
                       + (" " + audio_note if audio_note else "")
                       + " Residual: anonymity is WITHIN a sample-rate group, not one "
                         "universal class — 192 kbps CBR is illegal for MPEG-2 rates, "
                         "so those files emit at 160 and form their own group. The "
                         "group follows the source's sample rate, which is content we "
                         "preserve by design, not a producer trait. Plus one "
                         "generation of loss.")


def main() -> None:
    tmpdir = tempfile.mkdtemp(prefix="e_lame_")
    sources = build_sources(tmpdir)
    print(f"E-LAME peer set: {list(sources)} x "
          f"{min(len(v) for v in sources.values())} each, "
          f"sample rates {list(mc.RATES)}\n")
    for fid in ("raw", "F1", "F3"):
        r = run_condition(fid, sources, tmpdir)
        fps = sorted(r["struct_fingerprints"])
        print(f"[{fid:3}] mixed-rate: fingerprints separating producers: "
              f"{fps or 'none'} -> A2 {'FAIL' if r['a2_fail'] else 'PASS'}")

    print("\nPer-rate (anonymity is claimed per group, so it is shown per group):")
    for fid in ("F1", "F3"):
        for rate, r in per_rate_report(fid, tmpdir).items():
            fps = sorted(r["struct_fingerprints"])
            print(f"[{fid:3}] {rate:>6} Hz: {fps or 'none'} "
                  f"-> A2 {'FAIL' if r['a2_fail'] else 'PASS'}")

    # What the groups actually are, so the claim is concrete rather than abstract.
    print("\nF3 output groups (what an adversary can still bucket by):")
    from src.scrub import cli
    from tests.harness.plugins.mp3 import MP3Plugin
    plugin = MP3Plugin()
    for rate in mc.RATES:
        sub = os.path.join(tmpdir, f"grp{rate}")
        os.makedirs(sub, exist_ok=True)
        src = list(mc.producers(sub, repeats=1, rate=rate).values())[0][0]
        out = os.path.join(sub, "out.mp3")
        cli.scrub_file(src, out, "F3")
        f = plugin.structural_features(out)
        print(f"  {rate:>6} Hz source -> {f['bitrate_hist']} kbps, "
              f"samplerate {f['samplerate']}")


if __name__ == "__main__":
    main()
