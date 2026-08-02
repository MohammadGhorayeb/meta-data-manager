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

from ...errors import ScrubError
from ...standards import perceptual
from . import f1
from . import walker as w

CANONICAL_BITRATE = 192          # kbps, CBR
LAME_QUALITY = 2                 # -q 2
# Gate thresholds live in standards/perceptual.py, which documents WHY the band is
# what it is (two separate bugs taught it). Re-exported here so this tier still reads
# as a self-contained description of its own guarantees.
PERCEPTUAL_MIN_NCC = perceptual.DEFAULT_MIN_NCC
GATE_BAND_HZ = perceptual.GATE_BAND_HZ
PCM_RATE = perceptual.PCM_RATE


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
    _check_perceptual(data, encoded, source_rate=layout.frames[0].samplerate)
    return encoded


# The perceptual gate lives in standards/perceptual.py, shared with M4A: the two
# subtleties it encodes (delay alignment, and comparing only the audible band that
# the source actually occupies) were each found by a bug here, and a second copy
# would have to re-learn them. These thin aliases keep the tier's local vocabulary.
_pcm_mono = perceptual.pcm_mono
_bandlimit = perceptual.bandlimit
_best_ncc = perceptual.best_ncc
_gate_band = perceptual.gate_band


def _check_perceptual(original: bytes, scrubbed: bytes,
                      source_rate: int = PCM_RATE) -> None:
    perceptual.check(original, scrubbed, source_rate=source_rate,
                     min_ncc=PERCEPTUAL_MIN_NCC, ffmpeg=_tool("ffmpeg"),
                     label="MP3 F3 re-encode")


def residuals(data: bytes) -> list[str]:
    """F3 output is a clean canonical LAME MP3 — same audio-only guard as F1
    (the canonical LAME header is the intended A2-anonymity signature, allowed)."""
    return f1.residuals(data)
