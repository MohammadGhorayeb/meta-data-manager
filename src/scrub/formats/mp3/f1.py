"""MP3 F1 — bit-preserving metadata strip.

F1 keeps the MPEG audio frames byte-for-byte (decoded PCM stays identical) and
drops everything that is not audio: the front ID3v2 tag, every EOF trailer
(ID3v1/1.1, APEv2/APEv1, Lyrics3, appended ID3v2.4 footer), and any appended
"hitchhiker" data past the last audio frame. The output is exactly the file's
audio region.

Encoder-identity residual (documented, the audio analogue of the JPEG DQT): the
Xing/Info + LAME header frame lives INSIDE the first audio frame, and it carries
the encoder version string (e.g. "LAME3.100") plus the gapless encoder
delay/padding. Empirically, zeroing that string breaks the decoder's gapless trim
and shifts the samples — so removing it is NOT bit-preserving. F1 therefore keeps
it verbatim, and the encoder fingerprint is an A2 residual at F1 (removed only by
the F3 re-encode), never claimed clean. It is encoder identity, not user
metadata, so it is not an A1 leak.
"""
from __future__ import annotations

from . import walker as w


def scrub(data: bytes) -> bytes:
    """Return only the audio region — drops the front tag, all trailers, and any
    appended data, keeping the MPEG frames (and their gapless LAME info) exact."""
    layout = w.walk(data)              # fail-closed ParseError on malformed input
    return layout.audio_bytes()


def residuals(data: bytes) -> list[str]:
    """Re-walk scrubbed output; anything but a clean audio-only stream is a leak.
    Empty list = clean. (The kept Xing/LAME encoder tag is expected and allowed —
    it is the documented A2 residual, not user metadata.)"""
    out: list[str] = []
    layout = w.walk(data)
    if layout.id3v2 is not None:
        out.append("front ID3v2 tag survived")
    for t in layout.trailers:
        out.append(f"{t.kind} trailer survived at {t.offset}")
    if layout.appended[1] > 0:
        out.append(f"{layout.appended[1]} appended byte(s) after last audio frame")
    # defense in depth: long, audio-improbable tag magics must not appear anywhere.
    for magic, label in ((b"APETAGEX", "APEv2"), (b"LYRICS200", "Lyrics3")):
        if magic in data:
            out.append(f"{label} magic present in output")
    if data[:3] == b"ID3":
        out.append("ID3v2 magic at start of output")
    return out
