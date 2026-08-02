"""FlacPlugin — harness-side format knowledge for FLAC (FormatPlugin).

Mirrors the MP3/PNG plugins. FLAC is the audio counterpart of PNG: lossless, with
metadata cleanly separated from the audio frames, so the A2 fingerprint lives in the
encoder's *structural* choices — block size, frame sizes, which metadata blocks it
writes and in what order, how much padding it leaves, and the Vorbis vendor string
that names the encoder outright.

That last one is why FLAC differs from MP3: the vendor string is a producer signature
sitting in a metadata block rather than baked into the audio, so removing it is free.
The rest needs a re-encode — which for a lossless format costs nothing either.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess

from src.scrub.formats.flac import walker as w


class FlacPlugin:
    format_id = "flac"

    def matches(self, header: bytes, path: str = "") -> bool:
        return header[:4] == w.MAGIC or header[:3] == b"ID3"

    def annotate(self, in_path: str, offset: int) -> str | None:
        try:
            data = open(in_path, "rb").read()
            layout = w.walk(data)
        except Exception:
            return None
        if layout.id3v2 and offset < layout.id3v2[1]:
            return f"id3v2@+{offset}"
        for b in layout.blocks:
            if b.offset <= offset < b.offset + b.total:
                return f"{b.name}@+{offset - b.offset}"
        if layout.audio_start <= offset < layout.audio_end:
            return f"audio@+{offset - layout.audio_start}"
        return None

    def canonical_content(self, path: str) -> bytes:
        """Decoded PCM is the content identity — and for a LOSSLESS format it is an
        exact one, so any difference here is a bug rather than a tolerance question.
        Only the audio stream is decoded (`-map 0:a -vn`): cover art shows up as a
        video stream and is metadata, not content."""
        if shutil.which("ffmpeg") is None:
            return b""
        p = subprocess.run(["ffmpeg", "-i", path, "-map", "0:a", "-vn",
                            "-f", "s32le", "-loglevel", "error", "pipe:1"],
                           capture_output=True)
        return hashlib.sha1(p.stdout).digest()

    def mandatory_constants(self) -> list[bytes]:
        """Format-required invariants the fingerprint guard must not read as a tool
        signature — recorded in the matrix's `excluded` block, never hidden.

        `fLaC` is the stream magic. The four bytes after it are the STREAMINFO block
        header: type 0 and length 34 are spec-mandated (STREAMINFO is always first
        and always 34 bytes), and the high bit is the last-block flag, which is set
        because a scrubbed file carries exactly one metadata block. That last bit is
        genuinely our choice, and it is the canonical-form signature every
        normalising scrubber has — the direct analogue of MP3's canonical LAME
        header, which this project already accepts and documents. It says a file was
        scrubbed; it says nothing about who made it or what was removed. The
        alternative (keeping the source's other blocks so the flag lands elsewhere)
        would reintroduce exactly the producer tells F1 exists to remove.
        """
        streaminfo_header = bytes([0x80 | w.STREAMINFO]) + (34).to_bytes(3, "big")
        return [w.MAGIC, w.MAGIC + streaminfo_header]

    def structural_features(self, path: str) -> dict:
        """A2 structural channel. Returns {} on parse failure."""
        try:
            data = open(path, "rb").read()
            layout = w.walk(data)
            info = w.streaminfo(layout)
        except Exception:
            return {}
        vc = layout.vorbis_comment
        vendor = ""
        if vc is not None:
            try:
                vendor, _ = w.parse_vorbis_comment(vc.payload)
            except Exception:
                vendor = "<unparseable>"
        padding = sum(b.length for b in layout.blocks if b.type == w.PADDING)
        return {
            "vendor": vendor or "<none>",
            "block_types": tuple(b.name for b in layout.blocks),
            "padding_len": padding,
            "blocksize": (info["min_blocksize"], info["max_blocksize"]),
            # Frame sizes are the encoder's residual-partitioning fingerprint. They
            # depend on content too, so they are reported and left to the variance
            # engine to judge rather than being asserted as a producer trait.
            "framesize": (info["min_framesize"], info["max_framesize"]),
            "samplerate": info["samplerate"],
            "channels": info["channels"],
            "bits_per_sample": info["bits_per_sample"],
            "audio_digest": hashlib.sha1(
                data[layout.audio_start:layout.audio_end]).hexdigest()[:16],
        }
