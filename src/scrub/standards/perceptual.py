"""Perceptual content gate for lossy re-encodes — a SHARED standard module.

Every F3 tier faces the same question: the re-encode is lossy by design, so
bit-comparison is meaningless, yet we must still refuse to emit a file whose audio
was damaged or swapped. The answer is an aligned, band-limited cross-correlation.

Written once and called by MP3 and M4A (and whatever lossy audio format comes next)
because the two subtleties below were each found the hard way, and a copy-pasted gate
would have to re-learn them:

  * **Alignment.** A re-encode shifts samples by the encoder delay, so the comparison
    searches a small lag window. Without it a faithful re-encode scores near zero.
  * **Band.** Compare only what a listener can hear, and only where the source
    actually has audio. Encoders lowpass around 19 kHz, so a source from an encoder
    that codes to Nyquist legitimately loses inaudible ultrasonics — full-band
    comparison scored that as damage (0.95) and refused correctly-scrubbed files.
    And since everything is decoded to 44.1 kHz, a lower-rate source is upsampled and
    the region above ITS Nyquist holds only resampler artefacts — comparing there
    scored 0.98 and refused again, while the real audio agreed at 0.999.
"""
from __future__ import annotations

import subprocess

from ..errors import ContentError

PCM_RATE = 44100
# Ceiling on the compared band. Above this is inaudible to a human listener and is
# removed by any standard encoder's lowpass, so including it measures the codec's
# design rather than whether the scrub preserved the content.
GATE_BAND_HZ = 16000
# A faithful re-encode sits at ~0.999+; gross corruption or a swapped file is far
# below. Unrelated audio measures ~0.000.
DEFAULT_MIN_NCC = 0.99
# Stage-2 fallback thresholds (see `check`). The floor is far above what unrelated
# audio scores (~0.00) and comfortably below the worst legitimate case measured on
# this project's corpus (white noise through AAC, 0.85), so it separates "the same
# recording, restructured inaudibly" from "a different recording" without being
# tuned to any one file.
WAVEFORM_FLOOR = 0.75
MIN_ENVELOPE = 0.99


def pcm_mono(data: bytes, ffmpeg: str = "ffmpeg"):
    """Decode to a mono float PCM vector at PCM_RATE. Audio only — cover art is
    exposed as a video stream and must not reach the comparison.

    Decoded from a temp FILE, never a pipe. MP4/M4A puts its index (`moov`) after the
    audio in the common layout, so decoding it needs seeking; from stdin ffmpeg
    cannot seek, silently yields no samples, and the gate then scores a perfectly
    good scrub at 0.0000 and refuses it. A file costs one write and works for every
    container.
    """
    import os
    import tempfile

    import numpy as np
    with tempfile.TemporaryDirectory(prefix="pgate_") as td:
        path = os.path.join(td, "in.bin")
        with open(path, "wb") as f:
            f.write(data)
        r = subprocess.run([ffmpeg, "-i", path, "-map", "0:a", "-vn", "-f", "s16le",
                            "-ac", "1", "-ar", str(PCM_RATE), "-loglevel", "error",
                            "pipe:1"], capture_output=True)
    return np.frombuffer(r.stdout, dtype="<i2").astype(np.float64)


def gate_band(source_rate: int) -> int:
    """The band to compare, for a source recorded at `source_rate`: the fixed ceiling
    at 44.1 kHz, and just under Nyquist below it (see module docstring)."""
    return min(GATE_BAND_HZ, int(0.92 * source_rate / 2))


def bandlimit(x, cutoff: int = GATE_BAND_HZ, fs: int = PCM_RATE):
    """Zero everything above `cutoff` (numpy-only brick wall via rfft)."""
    import numpy as np
    if x.size < 2048:
        return x
    spec = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(x.size, d=1.0 / fs)
    spec[freqs > cutoff] = 0
    return np.fft.irfft(spec, n=x.size)


def best_ncc(a, b, maxlag: int = 3000, window: int = 200_000) -> float:
    """Best normalized cross-correlation over a small delay-alignment window."""
    return best_ncc_lag(a, b, maxlag, window)[0]


def best_ncc_lag(a, b, maxlag: int = 3000, window: int = 200_000):
    """As best_ncc, but also returns the lag that achieved it, so a follow-up
    measurement can compare the two signals already aligned."""
    import numpy as np
    n = min(len(a), len(b), window)
    if n < 2000:
        return 0.0, 0
    best, best_lag = -1.0, 0
    for lag in range(-maxlag, maxlag + 1, 25):
        if lag >= 0:
            x, y = a[lag:lag + n], b[:n]
        else:
            x, y = a[:n], b[-lag:-lag + n]
        m = min(len(x), len(y))
        if m < 1000:
            continue
        x2, y2 = x[:m], y[:m]
        sx, sy = x2.std(), y2.std()
        if sx < 1e-6 or sy < 1e-6:
            continue
        c = float(np.mean((x2 - x2.mean()) * (y2 - y2.mean())) / (sx * sy))
        if c > best:
            best, best_lag = c, lag
    return best, best_lag


def _align(a, b, lag: int):
    """Trim two signals to their overlapping region at the given lag."""
    n = min(len(a), len(b))
    if lag >= 0:
        return a[lag:lag + n], b[:n]
    return a[:n], b[-lag:-lag + n]


def envelope_similarity(a, b, fs: int = PCM_RATE, bands: int = 24) -> float:
    """Correlation of two signals' time-frequency ENERGY envelopes.

    Why this exists: AAC does not reproduce noise waveforms, it reproduces their
    perceptual character — so a re-encode of noisy material is inaudibly identical
    while correlating poorly sample-by-sample. Measured on this project's own corpus:
    white noise through AAC scores 0.86-0.90 on waveform correlation and is not
    remotely damaged, where MP3 on the same material scores 0.998. A gate that only
    looked at waveforms would refuse cymbals, applause, rain and breath — most real
    music — as "diverged".

    This measures what a listener actually hears: how energy is distributed across
    frequency and time. It is deliberately used only as a SECOND opinion, never
    alone, because two different noise recordings have similar envelopes while being
    entirely different audio — see `check`.
    """
    import numpy as np
    frame, hop = 4096, 2048
    n = min(len(a), len(b))
    if n < frame * 4:
        return 0.0
    a, b = a[:n], b[:n]
    win = np.hanning(frame)
    edges = np.linspace(0, frame // 2 + 1, bands + 1).astype(int)

    def env(x):
        rows = []
        for start in range(0, n - frame, hop):
            spec = np.abs(np.fft.rfft(x[start:start + frame] * win))
            rows.append([spec[lo:hi].sum() if hi > lo else 0.0
                         for lo, hi in zip(edges[:-1], edges[1:], strict=True)])
        return np.log10(np.array(rows) + 1e-12)

    ea, eb = env(a).ravel(), env(b).ravel()
    if ea.size == 0 or ea.std() < 1e-9 or eb.std() < 1e-9:
        return 0.0
    return float(np.corrcoef(ea, eb)[0, 1])


def check(original: bytes, scrubbed: bytes, source_rate: int = PCM_RATE,
          min_ncc: float = DEFAULT_MIN_NCC, ffmpeg: str = "ffmpeg",
          label: str = "re-encode") -> float:
    """Raise ContentError unless the scrubbed audio still sounds like the original.

    Two stages, and the second one exists because waveform correlation alone is the
    wrong question for codecs that reproduce noise perceptually rather than
    sample-for-sample (AAC does; MP3 largely does not):

      1. Waveform correlation in the audible band. Passes outright at `min_ncc` —
         this is the normal case and the strictest evidence.
      2. If that falls short, the signals must STILL be strongly correlated
         (`WAVEFORM_FLOOR` — proof it is the same recording, not merely similar
         material) AND their time-frequency energy envelopes must match
         (`MIN_ENVELOPE` — proof the difference is inaudible restructuring).

    Both conditions are required in stage 2, because either alone is forgeable:
    envelopes alone would accept two different noise recordings, which correlate
    near zero on the waveform; waveform alone rejects perfectly good AAC. Unrelated
    audio fails both and is rejected, which the tests assert directly.

    Returns the waveform score so callers can record it.
    """
    band = gate_band(source_rate)
    a = bandlimit(pcm_mono(original, ffmpeg), cutoff=band)
    b = bandlimit(pcm_mono(scrubbed, ffmpeg), cutoff=band)
    ncc, lag = best_ncc_lag(a, b)
    if ncc >= min_ncc:
        return ncc

    env = 0.0
    if ncc >= WAVEFORM_FLOOR:
        env = envelope_similarity(*_align(a, b, lag))
        if env >= MIN_ENVELOPE:
            return ncc

    raise ContentError(
        f"{label} diverged perceptually: waveform NCC {ncc:.4f} < {min_ncc} "
        f"and the fallback did not hold (needs NCC >= {WAVEFORM_FLOOR} with "
        f"envelope >= {MIN_ENVELOPE}, got envelope {env:.4f}); "
        f"compared below {band} Hz")
