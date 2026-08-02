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
    import numpy as np
    n = min(len(a), len(b), window)
    if n < 2000:
        return 0.0
    best = -1.0
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
        best = max(best, c)
    return best


def check(original: bytes, scrubbed: bytes, source_rate: int = PCM_RATE,
          min_ncc: float = DEFAULT_MIN_NCC, ffmpeg: str = "ffmpeg",
          label: str = "re-encode") -> float:
    """Raise ContentError unless the scrubbed audio matches within the audible band.
    Returns the score so callers can record it."""
    band = gate_band(source_rate)
    ncc = best_ncc(bandlimit(pcm_mono(original, ffmpeg), cutoff=band),
                   bandlimit(pcm_mono(scrubbed, ffmpeg), cutoff=band))
    if ncc < min_ncc:
        raise ContentError(
            f"{label} diverged perceptually: NCC {ncc:.4f} < {min_ncc} "
            f"(compared below {band} Hz)")
    return ncc
