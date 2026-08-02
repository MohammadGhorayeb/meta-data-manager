"""MP3 F3 — canonical lossy re-encode. The A2 defense.

F1/F2 keep the original audio frames, so the encoder fingerprint (the LAME/Xing
version string + the per-frame bitstream contour — the audio analogue of the JPEG
DQT) survives. F3 breaks that: decode to PCM and re-encode through ONE locked
canonical encoder + settings, so every output shares one encoder signature and one
quantization strategy. The original producer is laundered away.

Locked canonical settings (fixed => producer-independent output => A2 anonymity):
  LAME 3.100, 192 kbps CBR, joint-stereo (mono stays mono), quality 2.
Verified: two different producers of one source (LAME front-end vs ffmpeg's
libmp3lame) collapse to an identical signature after F3.

Tradeoff, documented not hidden: the A2 defense is anonymity *within* the canonical
LAME class (every F3 file now looks like LAME-192-CBR), not invisibility — the same
trade every F3/re-encode tool makes. MP3 re-encode is also lossy (one extra
generation) and shifts samples by the encoder delay, so content preservation is a
perceptual gate (aligned cross-correlation), not bit-exactness.

Fail-closed: missing tools, a failed re-encode, or a perceptual divergence past the
gate all raise rather than emit a file we cannot vouch for.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

from ...errors import ContentError, ScrubError
from . import f1
from . import walker as w

CANONICAL_BITRATE = 192          # kbps, CBR
LAME_QUALITY = 2                 # -q 2
# Perceptual gate: normalized cross-correlation after delay alignment. A faithful
# 192-CBR re-encode sits at ~0.999+; gross corruption / wrong file is far below.
PERCEPTUAL_MIN_NCC = 0.99
# The gate compares only the AUDIBLE band. Our constraint is perceptual identity to
# a human listener, and LAME lowpasses around 19 kHz by design — so a source from a
# non-lowpassing encoder (e.g. shine, which codes right up to Nyquist) loses real
# ultrasonic energy in the re-encode. Comparing full-band waveforms scores that
# inaudible removal as divergence and makes the tool refuse a file it scrubbed
# correctly: measured NCC 0.95 on shine-encoded white noise, which is inaudibly
# identical. Band-limiting both signals first keeps the guard honest about what it
# claims to guard (what you can hear), while still catching real corruption.
GATE_BAND_HZ = 16000
PCM_RATE = 44100


def _tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise ScrubError(
            f"{name} required for MP3 F3 (brew install lame ffmpeg). F3 fails "
            "closed rather than emit an un-normalized file.")
    return path


def scrub(data: bytes) -> bytes:
    """Decode + re-encode through the locked canonical LAME setting; strip all
    metadata. Raises ContentError if the re-encode diverges past the gate."""
    layout = w.walk(data)                          # validate + fail closed
    mode = "m" if layout.frames[0].is_mono else "j"

    ffmpeg, lame = _tool("ffmpeg"), _tool("lame")
    with tempfile.TemporaryDirectory(prefix="mp3f3_") as td:
        inp = os.path.join(td, "in.mp3")
        wav = os.path.join(td, "dec.wav")
        out = os.path.join(td, "out.mp3")
        with open(inp, "wb") as f:
            f.write(data)
        dec = subprocess.run([ffmpeg, "-y", "-i", inp, "-f", "wav", wav],
                             capture_output=True)
        if dec.returncode != 0 or not os.path.exists(wav):
            raise ScrubError("MP3 F3 decode failed: "
                             + dec.stderr.decode("utf-8", "replace").strip()[:200])
        enc = subprocess.run(
            [lame, "--quiet", "-m", mode, "-b", str(CANONICAL_BITRATE), "--cbr",
             "-q", str(LAME_QUALITY), wav, out], capture_output=True)
        if enc.returncode != 0 or not os.path.exists(out):
            raise ScrubError("MP3 F3 re-encode failed: "
                             + enc.stderr.decode("utf-8", "replace").strip()[:200])
        with open(out, "rb") as f:
            encoded = f.read()

    # Defense in depth: strip any tag LAME may have added; the canonical Xing/LAME
    # header lives in the audio region and is kept.
    encoded = f1.scrub(encoded)
    _check_perceptual(data, encoded)
    return encoded


def _pcm_mono(data: bytes):
    """Decode to a mono float PCM vector at 44.1 kHz (for the perceptual gate)."""
    import numpy as np
    r = subprocess.run([_tool("ffmpeg"), "-i", "pipe:0", "-f", "s16le",
                        "-ac", "1", "-ar", "44100", "pipe:1"],
                       input=data, capture_output=True)
    return np.frombuffer(r.stdout, dtype="<i2").astype(np.float64)


def _best_ncc(a, b, maxlag: int = 3000, window: int = 200_000) -> float:
    """Best normalized cross-correlation over a small delay-alignment window —
    MP3 re-encode shifts samples by the encoder delay, so we recover the lag."""
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


def _bandlimit(x, cutoff: int = GATE_BAND_HZ, fs: int = PCM_RATE):
    """Zero everything above `cutoff` (numpy-only brick-wall via rfft) so the
    perceptual gate compares the audible band, not ultrasonics neither codec keeps."""
    import numpy as np
    if x.size < 2048:
        return x
    spec = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(x.size, d=1.0 / fs)
    spec[freqs > cutoff] = 0
    return np.fft.irfft(spec, n=x.size)


def _check_perceptual(original: bytes, scrubbed: bytes) -> None:
    ncc = _best_ncc(_bandlimit(_pcm_mono(original)),
                    _bandlimit(_pcm_mono(scrubbed)))
    if ncc < PERCEPTUAL_MIN_NCC:
        raise ContentError(
            f"MP3 F3 re-encode diverged perceptually: NCC {ncc:.4f} "
            f"< {PERCEPTUAL_MIN_NCC}")


def residuals(data: bytes) -> list[str]:
    """F3 output is a clean canonical LAME MP3 — same audio-only guard as F1
    (the canonical LAME header is the intended A2-anonymity signature, allowed)."""
    return f1.residuals(data)
