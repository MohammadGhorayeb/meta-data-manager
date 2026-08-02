"""M4A F2 — canonical re-mux. Lossless, because the coded audio is copied.

F1 removes the metadata but keeps the source muxer's structure: box order, where
`moov` sits relative to `mdat`, how much slack it left, which optional boxes it
writes. That layout classifies the muxer the same way FLAC's block sizes classify
its encoder — so F1 clears A1 but not A2.

F2 re-muxes through one locked muxer with `-c copy`: the AAC or ALAC bitstream is
transferred sample-for-sample, so this is lossless for both, and every output gets
one muxer's layout. What it does NOT normalise is the *encoder* fingerprint inside
the coded audio — that needs F3, exactly as with MP3, and the matrix says so rather
than letting F2 imply more than it delivers.

Fail-closed: a missing tool, a failed re-mux, or any change to the decoded audio
raises instead of emitting a file we cannot vouch for.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile

from ...errors import ContentError, ScrubError
from . import f1


def _tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise ScrubError(
            f"{name} required for M4A F2 (brew install ffmpeg). F2 fails closed "
            "rather than emit an un-normalized file.")
    return path


def _decoded_digest(path: str) -> str:
    """Decoded PCM digest. The coded stream is copied, not re-encoded, so this must
    match exactly — F2 is lossless and any difference is a bug, not a tolerance."""
    ffmpeg = _tool("ffmpeg")
    r = subprocess.run([ffmpeg, "-i", path, "-map", "0:a", "-vn", "-f", "s16le",
                        "-loglevel", "error", "pipe:1"], capture_output=True)
    if r.returncode != 0:
        raise ContentError("M4A F2: could not decode audio for the content check: "
                           + r.stderr.decode("utf-8", "replace").strip()[:200])
    return hashlib.sha1(r.stdout).hexdigest()


def scrub(data: bytes) -> bytes:
    ffmpeg = _tool("ffmpeg")
    with tempfile.TemporaryDirectory(prefix="m4af2_") as td:
        inp = os.path.join(td, "in.m4a")
        out = os.path.join(td, "out.m4a")
        with open(inp, "wb") as f:
            f.write(data)
        before = _decoded_digest(inp)
        mux = subprocess.run(
            # -c copy: transfer the coded audio untouched (lossless for AAC and
            # ALAC alike). -map 0:a drops cover art, which ffmpeg exposes as a video
            # stream and which is metadata, not content.
            [ffmpeg, "-y", "-i", inp, "-map", "0:a", "-c", "copy",
             "-map_metadata", "-1", "-loglevel", "error", out],
            capture_output=True)
        if mux.returncode != 0 or not os.path.exists(out):
            raise ScrubError("M4A F2 re-mux failed: "
                             + mux.stderr.decode("utf-8", "replace").strip()[:200])
        after = _decoded_digest(out)
        with open(out, "rb") as f:
            remuxed = f.read()

    if before != after:
        raise ContentError("M4A F2 changed the audio: decoded PCM differs")

    # ffmpeg stamps its own encoder tag and timestamps on the re-mux, so F1 runs over
    # the output. Without this the tool would swap the source's fingerprint for our
    # toolchain's — a scrubber signature, which the guard exists to forbid.
    return f1.scrub(remuxed)


def residuals(data: bytes) -> list[str]:
    return f1.residuals(data)
