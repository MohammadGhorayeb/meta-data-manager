"""M4A F3 — canonical AAC re-encode. The A2 defense for the coded audio.

F2 normalises the *muxer* (box order, brand, slack) but copies the coded stream, so
the AAC **encoder's** fingerprint survives it — measured, not assumed: after F2 the
peer set still separates producers by `audio_digest` (experiment E-M4A). That is the
same situation MP3 F1 is in, and it needs the same answer.

F3 decodes to PCM and re-encodes through ONE locked encoder and setting, so every
output shares one encoder signature, then re-muxes canonically via F2's path.

Tradeoff, documented not hidden: anonymity is *within* the canonical class (every F3
file now looks like ffmpeg-AAC-128k), not invisibility — the same trade every lossy
re-encode makes. It also costs one extra generation of loss, so content preservation
is a perceptual gate rather than bit-exactness. The gate is the shared one in
standards/perceptual.py, so M4A inherits the alignment and audible-band handling that
two MP3 bugs taught rather than re-deriving them.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

from ...errors import ScrubError
from ...standards import isobmff as iso
from ...standards import perceptual
from . import f1

CANONICAL_BITRATE = "128k"       # AAC, constant across every output
CANONICAL_CODEC = "aac"          # ffmpeg's native encoder (no external dependency)


def _tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise ScrubError(
            f"{name} required for M4A F3 (brew install ffmpeg). F3 fails closed "
            "rather than emit an un-normalized file.")
    return path


def _source_rate(data: bytes) -> int:
    """Sample rate from the audio sample description, for the gate's band choice.

    Inside `stsd`, an `mp4a` entry's payload begins after its 4-byte type field and
    runs: reserved(6) + data_reference_index(2) + version(2) + revision(2) +
    vendor(4) + channels(2) + sample_size(2) + pre_defined(2) + reserved(2), then a
    16.16 fixed-point sample rate whose integer part is what we want — so 28 bytes
    past the type tag, not 24 (getting that wrong returned 0 and made the gate
    compare a 0 Hz band, which nothing can match).

    The result is sanity-checked and falls back to the default: this field only tunes
    a threshold, so a surprising layout must not fail an otherwise sound scrub — but
    a nonsense value must not be used either.
    """
    try:
        for root in iso.parse(data):
            for box in root.walk():
                if box.type == b"stsd":
                    payload = box.payload
                    at = payload.find(b"mp4a")
                    if at < 0 or at + 30 > len(payload):
                        continue
                    rate = int.from_bytes(payload[at + 28:at + 30], "big")
                    if 8000 <= rate <= 192000:
                        return rate
    except Exception:
        pass
    return perceptual.PCM_RATE


def scrub(data: bytes) -> bytes:
    ffmpeg = _tool("ffmpeg")
    with tempfile.TemporaryDirectory(prefix="m4af3_") as td:
        inp = os.path.join(td, "in.m4a")
        out = os.path.join(td, "out.m4a")
        with open(inp, "wb") as f:
            f.write(data)
        enc = subprocess.run(
            [ffmpeg, "-y", "-i", inp, "-map", "0:a", "-vn",
             "-c:a", CANONICAL_CODEC, "-b:a", CANONICAL_BITRATE,
             "-map_metadata", "-1", "-loglevel", "error", out],
            capture_output=True)
        if enc.returncode != 0 or not os.path.exists(out):
            raise ScrubError("M4A F3 re-encode failed: "
                             + enc.stderr.decode("utf-8", "replace").strip()[:200])
        with open(out, "rb") as f:
            encoded = f.read()

    # ffmpeg stamps its own encoder atom and fresh timestamps, so the metadata strip
    # runs over the output: otherwise F3 would trade the source's fingerprint for our
    # toolchain's, which the scrubber-fingerprint guard exists to forbid.
    encoded = f1.scrub(encoded)
    perceptual.check(data, encoded, source_rate=_source_rate(data),
                     ffmpeg=ffmpeg, label="M4A F3 re-encode")
    return encoded


def residuals(data: bytes) -> list[str]:
    """F3 output is a clean canonical M4A — same audio-only guard as F1."""
    return f1.residuals(data)
