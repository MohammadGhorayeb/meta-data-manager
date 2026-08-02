"""E-ENGINE — cross-engine A2 residual in the *audio* space (MP3).

E-LAME answers the header-level question: after F3 every producer collapses to the
canonical LAME-192-CBR signature, so `struct:lame_version` / `xing_magic` /
`bitrate_hist` / `channel_mode` no longer separate producers. That is a real result,
but not the whole question — the harness's structural channel is categorical and
reads frame headers, never the decoded waveform.

The question this experiment closes: a first-generation lossy encode bakes its own
decisions into the *audio*, and re-encoding does not undo them (the audio analogue
of Sorell's primary-quantization trace in JPEG). So a peer-corpus adversary might
still recover the source *engine* from the scrubbed file's spectrum alone, even
behind a perfectly normalized header.

Design (honest by construction):
  * Classes are ENGINES, not front-ends. `lame_vbr` (LAME CLI) and `ffmpeg_vbr`
    (ffmpeg's libmp3lame) are the same engine and produce the same audio, so asking
    a spectral classifier to separate them would measure nothing. E-LAME already
    covers front-ends in header space. Here: lame-engine vs shine-engine, chance 1/2.
  * Features are content-independent by construction — fractional energy above
    16/19/20 kHz and a spectral cutoff estimate. An encoder's lowpass and its
    high-band quantization behaviour live here; the specific tune does not.
  * Classifier = leave-one-CONTENT-out nearest centroid, features standardized over
    the corpus. The held-out content is in no centroid, so the classifier cannot
    memorize content — it must generalize an *engine* trace to unseen audio. That
    is exactly the A2 adversary: a corpus of other people's files, not yours.
  * `raw` and `F1` are CONTROLS. If they do not classify near-perfectly, the feature
    is too weak and the F3 number means nothing — the run reports that rather than
    quietly claiming a pass.

Run:  ./.venv/bin/python -m tests.scrub.e_engine
"""
from __future__ import annotations

import os
import subprocess
import tempfile

import numpy as np

from tests.scrub import mp3_corpus as mc

# Contents must be BROADBAND. An encoder trace lives in how the encoder treats the
# top of the spectrum, so content with no high-frequency energy (a pure 440 Hz tone)
# carries no trace to find for ANY engine — testing on tones measures whether
# silence is silent, not whether the scrub works. Real forensic audio (music,
# speech) is broadband, so the corpus is: several noise colours at distinct seeds,
# a chirp, and a noise+tone mix. All deterministic (fixed seeds) so this reproduces.
CONTENTS = [
    ("pink1", "anoisesrc=duration=2:color=pink:seed=1:amplitude=0.6"),
    ("pink2", "anoisesrc=duration=2:color=pink:seed=2:amplitude=0.4"),
    ("white1", "anoisesrc=duration=2:color=white:seed=3:amplitude=0.3"),
    ("brown1", "anoisesrc=duration=2:color=brown:seed=4:amplitude=0.7"),
    ("chirp", "aevalsrc='0.5*sin(2*PI*(200*t+4000*t*t))':d=2:s=44100"),
    ("mix", "anoisesrc=duration=2:color=white:seed=5:amplitude=0.25,"
            "aeval='val(0)+0.4*sin(2*PI*1000*t)'"),
]

# producer -> (engine class, argv builder from wav; None = encoded via ffmpeg)
PRODUCERS = {
    "lame_vbr": ("lame", lambda wav, out: ["lame", "--quiet", "-V", "2", wav, out]),
    "ffmpeg_vbr": ("lame", None),
    "shine_cbr": ("shine", lambda wav, out: ["shineenc", "-q", "-b", "128", wav, out]),
}

# Margin over chance above which we call the engine recoverable. With 12 trials the
# standard error is ~0.14, so 0.15 is roughly one sigma — deliberately generous to
# the ADVERSARY (privacy-conservative: we would rather over-report a leak).
RECOVERABLE_MARGIN = 0.15


def _run(cmd):
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {p.stderr.decode('utf-8', 'replace')[:300]}")


def available_producers() -> list[str]:
    out = ["ffmpeg_vbr"]
    if mc.HAVE_LAME:
        out.insert(0, "lame_vbr")
    if mc.HAVE_SHINE:
        out.append("shine_cbr")
    return out


def engines(producers: list[str]) -> list[str]:
    seen = []
    for p in producers:
        e = PRODUCERS[p][0]
        if e not in seen:
            seen.append(e)
    return seen


def build_corpus(tmpdir: str, producers: list[str]) -> dict:
    """{(producer, content): path} — same content, different source encoder."""
    paths = {}
    for cname, lavfi in CONTENTS:
        wav = os.path.join(tmpdir, f"{cname}.wav")
        _run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", lavfi,
              "-ac", "2", "-ar", "44100", wav])
        for prod in producers:
            out = os.path.join(tmpdir, f"{prod}__{cname}.mp3")
            builder = PRODUCERS[prod][1]
            if builder is None:
                _run(["ffmpeg", "-y", "-loglevel", "error", "-i", wav,
                      "-c:a", "libmp3lame", "-q:a", "2", "-map_metadata", "-1", out])
            else:
                _run(builder(wav, out))
            paths[(prod, cname)] = out
    return paths


def features(path: str) -> np.ndarray:
    """Content-independent spectral signature of the decoded audio.

    An encoder's identity shows up in where it stops coding (its lowpass) and how it
    treats the top of the band — not in which notes were played, so every feature
    here is a RATIO or a band edge, never absolute level.
    """
    p = subprocess.run(["ffmpeg", "-i", path, "-f", "s16le", "-ac", "1",
                        "-ar", "44100", "-loglevel", "error", "pipe:1"],
                       capture_output=True)
    x = np.frombuffer(p.stdout, dtype="<i2").astype(np.float64) / 32768.0
    if x.size < 8192:
        return np.zeros(4)
    from scipy.signal import welch
    f, pxx = welch(x, fs=44100, nperseg=4096)
    total = pxx.sum() + 1e-30
    hf16 = pxx[f > 16000].sum() / total
    hf19 = pxx[f > 19000].sum() / total
    hf20 = pxx[f > 20000].sum() / total
    cutoff = f[int(np.max(np.where(pxx > pxx.max() * 1e-7)))]
    return np.array([np.log10(hf16 + 1e-20), np.log10(hf19 + 1e-20),
                     np.log10(hf20 + 1e-20), cutoff / 22050.0])


def condition_features(paths: dict, fidelity: str, tmpdir: str) -> dict:
    """Scrub every corpus file at `fidelity` (or take it raw) -> feature vector."""
    from src.scrub import cli
    feats = {}
    for (prod, content), src in paths.items():
        if fidelity == "raw":
            target = src
        else:
            target = os.path.join(tmpdir, f"{prod}__{content}__{fidelity}.mp3")
            cli.scrub_file(src, target, fidelity)
        feats[(prod, content)] = features(target)
    return feats


def loco_accuracy(feats: dict, producers: list[str], contents: list[str]) -> dict:
    """Leave-one-content-out nearest-centroid ENGINE classification."""
    classes = engines(producers)
    M = np.array([feats[k] for k in feats])
    mu, sd = M.mean(axis=0), M.std(axis=0) + 1e-12
    z = {k: (v - mu) / sd for k, v in feats.items()}

    correct, total, confusion = 0, 0, {}
    for held in contents:
        train = [c for c in contents if c != held]
        centroids = {}
        for e in classes:
            vecs = [z[(p, c)] for p in producers if PRODUCERS[p][0] == e
                    for c in train]
            centroids[e] = np.mean(vecs, axis=0)
        for p in producers:
            true_e = PRODUCERS[p][0]
            v = z[(p, held)]
            pred = min(classes, key=lambda e: np.linalg.norm(v - centroids[e]))
            confusion[(true_e, pred)] = confusion.get((true_e, pred), 0) + 1
            correct += int(pred == true_e)
            total += 1
    acc = correct / total
    chance = 1.0 / len(classes)
    return {"accuracy": acc, "correct": correct, "total": total, "chance": chance,
            "classes": classes, "recoverable": acc - chance > RECOVERABLE_MARGIN,
            "confusion": {f"{a}->{b}": n for (a, b), n in sorted(confusion.items())}}


def run(fidelities=("raw", "F1", "F3"), tmpdir: str | None = None) -> dict:
    producers = available_producers()
    contents = [c for c, _ in CONTENTS]
    tmpdir = tmpdir or tempfile.mkdtemp(prefix="e_engine_")
    os.makedirs(tmpdir, exist_ok=True)
    paths = build_corpus(tmpdir, producers)
    results = {fid: loco_accuracy(condition_features(paths, fid, tmpdir),
                                  producers, contents)
               for fid in fidelities}
    results["_meta"] = {"producers": producers, "engines": engines(producers),
                        "contents": contents}
    return results


def controls_valid(results: dict) -> bool:
    """The F3 number is only meaningful if the controls separate the engines."""
    return all(results[c]["recoverable"] for c in ("raw", "F1") if c in results)


def main() -> None:
    r = run()
    meta = r.pop("_meta")
    print(f"E-ENGINE peer set: {meta['producers']} "
          f"-> engine classes {meta['engines']} x {len(meta['contents'])} contents "
          f"(leave-one-content-out nearest centroid)\n")
    for fid, m in r.items():
        tag = "control" if fid in ("raw", "F1") else "TEST   "
        verdict = "RECOVERABLE" if m["recoverable"] else "at chance"
        print(f"[{fid:3}] {tag} engine classification "
              f"{m['accuracy']:.2f} ({m['correct']}/{m['total']}), "
              f"chance {m['chance']:.2f} -> {verdict}   {m['confusion']}")
    print("\ncontrols valid:", controls_valid(r),
          "(raw/F1 must be RECOVERABLE, else the feature is too weak to trust F3)")


if __name__ == "__main__":
    main()
