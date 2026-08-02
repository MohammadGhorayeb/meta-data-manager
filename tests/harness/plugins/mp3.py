"""MP3Plugin — harness-side format knowledge for MP3 (FormatPlugin).

Mirrors the JPEG/PNG plugins. The A2 structural channel exposes the encoder
fingerprint that separates producers: the Xing/LAME version string, the CBR-vs-VBR
flag, the per-frame bitrate contour (histogram), and channel mode — the audio
analogue of the JPEG DQT / PNG deflate signature.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess

from src.scrub.formats.mp3 import walker as w


class MP3Plugin:
    format_id = "mp3"

    def matches(self, header: bytes, path: str = "") -> bool:
        if header[:3] == b"ID3":
            return True
        return len(header) >= 2 and header[0] == 0xFF and (header[1] & 0xE0) == 0xE0

    def annotate(self, in_path: str, offset: int) -> str | None:
        try:
            data = open(in_path, "rb").read()
            L = w.walk(data)
        except Exception:
            return None
        if L.id3v2 and offset < L.id3v2[1]:
            return f"id3v2@+{offset}"
        if L.audio_start <= offset < L.audio_end:
            return f"audio@+{offset - L.audio_start}"
        if offset >= L.audio_end:
            return f"trailer@+{offset - L.audio_end}"
        return None

    def canonical_content(self, path: str) -> bytes:
        """Decoded PCM is the content-identity definition for MP3 (two files with
        the same audio but different tags are the same content). Returns a hash of
        the decoded PCM; falls back to b'' if ffmpeg is unavailable."""
        if shutil.which("ffmpeg") is None:
            return b""
        p = subprocess.run(["ffmpeg", "-i", path, "-f", "s16le", "-ac", "2",
                            "-ar", "44100", "pipe:1"], capture_output=True)
        return hashlib.sha1(p.stdout).digest()

    def mandatory_constants(self) -> list[bytes]:
        # No universal magic prefix (ID3 optional); the frame-sync byte 0xFF is
        # audio-defining. Nothing here is a scrubber signature to exclude.
        return [b"\xff"]

    def structural_features(self, path: str) -> dict:
        """A2 encoder fingerprint. Returns {} on parse failure."""
        try:
            data = open(path, "rb").read()
            L = w.walk(data)
        except Exception:
            return {}
        feats: dict = {
            "lame_version": (L.xing.lame_version or b"none").decode("latin-1", "replace")
                            if L.xing else "no_xing",
            "xing_magic": (L.xing.magic.decode("latin-1") if L.xing else "none"),
            "bitrate_hist": tuple(sorted({fr.bitrate for fr in L.frames})),
            "channel_mode": L.frames[0].channel_mode if L.frames else -1,
            # Sample rate is visible to any adversary and it CONSTRAINS the
            # canonical re-encode: 192 kbps CBR is illegal below 32 kHz, so
            # MPEG-2-rate files come out at 160 instead. Without this feature the
            # peer set could not see that F3 output falls into per-rate groups —
            # it went unmeasured until a mixed-rate corpus was built.
            "samplerate": L.frames[0].samplerate if L.frames else -1,
        }
        return feats
