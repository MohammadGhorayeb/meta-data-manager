"""M4aPlugin — harness-side format knowledge for M4A (FormatPlugin).

The A2 channel for a container format is its *layout*: which brand the muxer stamps,
the order it writes the top-level boxes in (notably whether `moov` precedes `mdat` —
the "faststart" choice), how much slack it leaves in `free` boxes, and which optional
boxes it emits. None of that depends on the audio, so it fingerprints the muxer the
way a DQT fingerprints a JPEG encoder.

The coded audio digest is tracked separately, because it separates *encoder* settings
rather than muxer layout — the two are different producers in one file, and F2
normalises only the second.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess

from src.scrub.standards import isobmff as iso


class M4aPlugin:
    format_id = "m4a"

    def matches(self, header: bytes, path: str = "") -> bool:
        return len(header) >= 12 and header[4:8] == b"ftyp"

    def annotate(self, in_path: str, offset: int) -> str | None:
        try:
            boxes = iso.parse(open(in_path, "rb").read())
        except Exception:
            return None
        best = None
        for root in boxes:
            for box in root.walk():
                if box.offset <= offset < box.end:
                    # Deepest enclosing box wins: "moov/udta/meta/ilst" is a far more
                    # useful evidence label than "moov".
                    if best is None or box.offset >= best.offset:
                        best = box
        if best is None:
            return None
        return f"{best.type.decode('latin-1', 'replace')}@+{offset - best.offset}"

    def canonical_content(self, path: str) -> bytes:
        """Decoded PCM — the content identity. Audio only: cover art is exposed as a
        video stream and is metadata, not content."""
        if shutil.which("ffmpeg") is None:
            return b""
        p = subprocess.run(["ffmpeg", "-i", path, "-map", "0:a", "-vn",
                            "-f", "s16le", "-loglevel", "error", "pipe:1"],
                           capture_output=True)
        return hashlib.sha1(p.stdout).digest()

    def mandatory_constants(self) -> list[bytes]:
        # Every ISOBMFF file carries these; they are format-required, not our mark.
        return [b"ftyp", b"moov", b"mdat", b"mvhd", b"trak", b"mdia", b"minf",
                b"stbl", b"stsd", b"mp4a"]

    def structural_features(self, path: str) -> dict:
        """A2 structural channel. Returns {} on parse failure."""
        try:
            data = open(path, "rb").read()
            boxes = iso.parse(data)
        except Exception:
            return {}
        top = [b.type.decode("latin-1", "replace") for b in boxes]
        ftyp = next((b for b in boxes if b.type == b"ftyp"), None)
        brand = ftyp.payload[:4].decode("latin-1", "replace") if ftyp else "none"
        free_total = sum(b.size for root in boxes for b in root.walk()
                         if b.type in (b"free", b"skip"))
        mdat = next((b for b in boxes if b.type == b"mdat"), None)
        moov_first = None
        moov = next((b for b in boxes if b.type == b"moov"), None)
        if moov is not None and mdat is not None:
            moov_first = moov.offset < mdat.offset
        all_types = tuple(sorted({b.type.decode("latin-1", "replace")
                                  for root in boxes for b in root.walk()}))
        return {
            "brand": brand,
            "top_level_order": tuple(top),
            "moov_before_mdat": moov_first,          # the faststart muxer choice
            "free_bytes": free_total,
            "box_inventory": all_types,
        }

    def coded_audio_digest(self, path: str) -> str:
        """Hash of the coded audio, deliberately NOT part of structural_features.

        It used to be, and that made M4A the only format judged against its own
        compressed content in the categorical A2 channel: two sources encoded at
        different bitrates hash differently no matter how perfectly the container is
        scrubbed, so M4A could never pass while MP3 — whose structural channel is
        headers only — did. Same evidence, different yardsticks, and the results
        table silently compared them as if they were the same.

        Coded-audio identity is an AUDIO-space question, and audio space has a proper
        adversary simulation: `tests/scrub/e_m4a_audio.py`, the M4A counterpart of
        E-ENGINE. This method stays available for experiments that want it explicitly.
        """
        try:
            boxes = iso.parse(open(path, "rb").read())
        except Exception:
            return ""
        mdat = next((b for b in boxes if b.type == b"mdat"), None)
        return hashlib.sha1(mdat.payload if mdat else b"").hexdigest()[:16]
