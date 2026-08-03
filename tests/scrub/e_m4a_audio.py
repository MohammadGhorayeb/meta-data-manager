"""E-M4A-AUDIO — cross-engine A2 residual in the *audio* space (M4A).

Closes the question E-M4A had to leave open. E-M4A works in container space and shows
that F2 normalises the muxer layout while F3 additionally collapses same-source
producers to byte-identical output — but a trace of the source's own first encode
survives in the coded audio, and container-space features cannot say whether that
trace actually identifies a *producer*. This does, the same way E-ENGINE did for MP3.

Classes are ENGINES: ffmpeg's native `aac` versus Apple's AudioToolbox `aac_at`.
Genuinely different implementations, not two front-ends of one library — the M4A
counterpart of shine-vs-libmp3lame. Chance is 1/2.

Everything else is E-ENGINE's machinery, reused rather than re-implemented: the same
content-independent spectral features, the same leave-one-CONTENT-out nearest
centroid, the same rule that `raw` and the copy tiers are CONTROLS whose failure
makes the F3 number meaningless.

SCOPE, stated because it constrains where this result can be quoted: `aac_at` is
macOS-only (AudioToolbox), and no second AAC encoder ships in a standard Linux
ffmpeg, so CI cannot currently reproduce this. The run reports "not measured" there
rather than inheriting a number it did not compute.

Run:  ./.venv/bin/python -m tests.scrub.e_m4a_audio
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

from tests.scrub import e_engine

# producer -> (engine class, ffmpeg encoder name, bitrate)
PRODUCERS = {
    "ffmpeg_aac_128": ("ffmpeg_aac", "aac", "128k"),
    "ffmpeg_aac_192": ("ffmpeg_aac", "aac", "192k"),
    "apple_aac_128": ("apple_aac", "aac_at", "128k"),
    "apple_aac_192": ("apple_aac", "aac_at", "192k"),
}

RECOVERABLE_MARGIN = e_engine.RECOVERABLE_MARGIN


def _run(cmd):
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {p.stderr.decode('utf-8','replace')[:300]}")


def have_second_engine() -> bool:
    """Is a non-ffmpeg AAC encoder available? Without one there is no cross-ENGINE
    question to ask, only a cross-settings one, and the two must not be confused."""
    if shutil.which("ffmpeg") is None:
        return False
    out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                         capture_output=True).stdout.decode("utf-8", "replace")
    return " aac_at " in out


def available_producers() -> list[str]:
    if not have_second_engine():
        return [p for p, (_, enc, _) in PRODUCERS.items() if enc == "aac"]
    return list(PRODUCERS)


def class_of(producer: str) -> str:
    """Engine class: ffmpeg's AAC vs Apple's."""
    return PRODUCERS[producer][0]


def bitrate_class_of(producer: str) -> str:
    """Class by the SOURCE's own quality setting.

    This is the trace the M4A residual is actually about: not "which program", but
    "the original was encoded at a different quality, and re-encoding cannot restore
    what that first encode discarded". It is also the question this corpus can
    actually answer — the two available AAC engines turn out to be near-twins
    spectrally (99.5%-energy bandwidth 15075 Hz vs 15427 Hz at the same bitrate),
    so an engine classifier is weak even on UNTOUCHED files, whereas bitrate is
    plainly visible there (15.1 kHz vs 16.9 kHz). Reporting both keeps the
    distinction: one question the evidence can settle, one it cannot.
    """
    return PRODUCERS[producer][2]


def build_corpus(tmpdir: str, producers: list[str], rate: int = 44100) -> dict:
    """{(producer, content): path} — same content, different source encoder."""
    paths = {}
    for cname, lavfi in e_engine.CONTENTS:
        wav = os.path.join(tmpdir, f"{cname}_{rate}.wav")
        _run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", lavfi,
              "-ac", "2", "-ar", str(rate), wav])
        for prod in producers:
            _, encoder, bitrate = PRODUCERS[prod]
            out = os.path.join(tmpdir, f"{prod}__{cname}.m4a")
            _run(["ffmpeg", "-y", "-loglevel", "error", "-i", wav, "-c:a", encoder,
                  "-b:a", bitrate, "-map_metadata", "-1", out])
            paths[(prod, cname)] = out
    return paths


def condition_features(paths: dict, fidelity: str, tmpdir: str,
                       rate: int = 44100) -> dict:
    from src.scrub import cli
    feats = {}
    for (prod, content), src in paths.items():
        if fidelity == "raw":
            target = src
        else:
            target = os.path.join(tmpdir, f"{prod}__{content}__{fidelity}.m4a")
            cli.scrub_file(src, target, fidelity)
        feats[(prod, content)] = e_engine.features(target, rate=rate)
    return feats


def run(fidelities=("raw", "F1", "F2", "F3"), tmpdir: str | None = None,
        rate: int = 44100) -> dict:
    producers = available_producers()
    contents = [c for c, _ in e_engine.CONTENTS]
    tmpdir = tmpdir or tempfile.mkdtemp(prefix="e_m4a_audio_")
    os.makedirs(tmpdir, exist_ok=True)
    paths = build_corpus(tmpdir, producers, rate=rate)
    results = {}
    for fid in fidelities:
        feats = condition_features(paths, fid, tmpdir, rate=rate)
        results[fid] = e_engine.loco_accuracy(feats, producers, contents,
                                              class_of=bitrate_class_of)
        results[fid]["engine"] = e_engine.loco_accuracy(feats, producers, contents,
                                                        class_of=class_of)
    results["_meta"] = {"producers": producers, "contents": contents,
                        "cross_engine": have_second_engine()}
    return results


def controls_valid(results: dict) -> bool:
    """F1 and F2 copy the coded stream, so the engine MUST still be identifiable
    there. If it is not, the feature is too weak and F3 proves nothing."""
    controls = [c for c in ("raw", "F1", "F2") if c in results]
    return all(results[c]["recoverable"] for c in controls)


def main() -> None:
    if not have_second_engine():
        print("No second AAC engine available (needs ffmpeg with aac_at, macOS "
              "AudioToolbox). The cross-engine question cannot be asked here.")
        return
    r = run()
    meta = r.pop("_meta")
    print(f"E-M4A-AUDIO peer set: {meta['producers']} x "
          f"{len(meta['contents'])} contents\n")
    print("Primary question — can the SOURCE'S OWN QUALITY SETTING be recovered "
          "from the sound?  (classes: 128k vs 192k original, chance 0.50)")
    for fid, m in r.items():
        tag = "control" if fid in ("raw", "F1", "F2") else "TEST   "
        verdict = "RECOVERABLE" if m["recoverable"] else "at chance"
        print(f"  [{fid:3}] {tag} {m['accuracy']:.2f} ({m['correct']}/{m['total']})"
              f" -> {verdict}")
    print(f"  controls valid: {controls_valid(r)}\n")
    print("Secondary — which ENGINE made it (ffmpeg's AAC vs Apple's). Reported "
          "only to show it is NOT answerable here: the two are near-twins, so even "
          "untouched files barely separate.")
    for fid, m in r.items():
        e = m["engine"]
        print(f"  [{fid:3}] {e['accuracy']:.2f} ({e['correct']}/{e['total']})"
              f" -> {'RECOVERABLE' if e['recoverable'] else 'at chance'}")


if __name__ == "__main__":
    main()
