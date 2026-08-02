"""FLAC F2 — canonical lossless re-encode. The A2 defense that costs nothing.

F1 removes the tags but keeps the original audio frames, so the encoder's structural
habits survive: block size, frame sizes, seektable granularity, how the residuals
were partitioned. Those classify the producer the same way a JPEG's DQT does — but
unlike JPEG, FLAC is LOSSLESS, so re-encoding through one locked encoder erases them
at **zero quality cost**. This is the PNG-F2 situation, not the JPEG-F3 one: A2
without giving up a single sample.

Content identity is therefore exact, not perceptual. The decoded PCM must be
bit-identical to the source's; anything else is a bug, and the tier fails closed
rather than emit it. FLAC's own STREAMINFO MD5 (a hash of the *unencoded* audio)
gives us a second, independent check for free.

Fail-closed: missing tools, a failed re-encode, or any content mismatch raises.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile

from ...errors import ContentError, ScrubError
from . import f1
from . import walker as w

# Locked canonical encode. -compression_level 8 is ffmpeg's flac default ceiling and
# fixes the block size and residual-partition strategy, which is exactly the set of
# choices that would otherwise fingerprint the producer.
COMPRESSION_LEVEL = 8


def _tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise ScrubError(
            f"{name} required for FLAC F2 (brew install ffmpeg). F2 fails closed "
            "rather than emit an un-normalized file.")
    return path


def _pcm_md5(path_or_data, is_path: bool = False) -> str:
    """MD5 of the decoded PCM — the content-identity definition for lossless audio."""
    ffmpeg = _tool("ffmpeg")
    cmd = [ffmpeg, "-i", path_or_data if is_path else "pipe:0",
           "-map", "0:a", "-vn", "-f", "s32le", "-loglevel", "error", "pipe:1"]
    if is_path:
        r = subprocess.run(cmd, capture_output=True)
    else:
        r = subprocess.run(cmd, input=path_or_data, capture_output=True)
    if r.returncode != 0:
        raise ContentError("FLAC F2: could not decode audio for the content check: "
                           + r.stderr.decode("utf-8", "replace").strip()[:200])
    return hashlib.md5(r.stdout).hexdigest()


def scrub(data: bytes) -> bytes:
    layout = w.walk(data)                          # validate + fail closed
    before = w.streaminfo(layout)

    ffmpeg = _tool("ffmpeg")
    with tempfile.TemporaryDirectory(prefix="flacf2_") as td:
        inp = os.path.join(td, "in.flac")
        out = os.path.join(td, "out.flac")
        with open(inp, "wb") as f:
            f.write(data)
        enc = subprocess.run(
            # -map 0:a + -vn: take ONLY the audio. Embedded cover art is exposed by
            # ffmpeg as a video stream, so without this the re-encode either carries
            # the art through (a metadata leak, and a nested one at that) or dies
            # trying to decode it. Dropping it is the correct scrub either way.
            [ffmpeg, "-y", "-i", inp, "-map", "0:a", "-vn", "-map_metadata", "-1",
             "-c:a", "flac", "-compression_level", str(COMPRESSION_LEVEL),
             "-loglevel", "error", out],
            capture_output=True)
        if enc.returncode != 0 or not os.path.exists(out):
            raise ScrubError("FLAC F2 re-encode failed: "
                             + enc.stderr.decode("utf-8", "replace").strip()[:200])
        with open(out, "rb") as f:
            encoded = f.read()

    # ffmpeg writes its own vendor string ("Lavf...") and may add tags, so the F1
    # pass runs over the re-encoded file too. Without it the tool would swap the
    # source's fingerprint for OUR toolchain's -- a scrubber signature, which the
    # fingerprint guard exists to forbid.
    encoded = f1.scrub(encoded)

    after = w.streaminfo(w.walk(encoded))
    for field in ("samplerate", "channels", "bits_per_sample", "total_samples"):
        if before[field] != after[field]:
            raise ContentError(
                f"FLAC F2 changed {field}: {before[field]} -> {after[field]}")

    # Content check, cheapest sufficient form first. STREAMINFO carries an MD5 of the
    # UNENCODED audio, computed independently by each encoder over the raw samples —
    # so two matching MD5s already prove bit-identical audio, with no decode at all.
    # Decoding both files (the older check) cost three ffmpeg runs per scrub and
    # dominated matrix generation. The decode remains the fallback for files whose
    # MD5 is unset (all zeroes is legal), so the guarantee never weakens to nothing.
    unset = "0" * 32
    if before["md5"] != unset and after["md5"] != unset:
        if before["md5"] != after["md5"]:
            raise ContentError(
                "FLAC F2 changed the audio: STREAMINFO MD5 differs "
                f"({before['md5']} -> {after['md5']})")
    elif _pcm_md5(data) != _pcm_md5(encoded):
        raise ContentError("FLAC F2 is not bit-exact: decoded PCM differs")
    return encoded


def residuals(data: bytes) -> list[str]:
    """F2 output is a canonical FLAC: same audio-only guard as F1, plus a check that
    the re-encode really did normalise the encoder's structural choices."""
    out = f1.residuals(data)
    layout = w.walk(data)
    info = w.streaminfo(layout)
    if info["min_blocksize"] != info["max_blocksize"]:
        out.append("variable block size survived (producer tell)")
    return out
