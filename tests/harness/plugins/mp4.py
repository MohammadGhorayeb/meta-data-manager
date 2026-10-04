"""Mp4Plugin — harness-side format knowledge for MP4 (FormatPlugin).

An MP4 carries two producers in one file, exactly as an M4A does, and the cells
name which one leaked rather than averaging them:

  * the **muxer**, which decided the brand, the order of the top-level boxes,
    whether `moov` precedes `mdat`, how much `free` slack to leave, what to call
    each track's handler, and whether to write `mdat`'s length in 32 or 64 bits;
  * the **video encoder**, which produced the coded H.264 inside `mdat`.

Two features here exist because Phase 4 found them and nothing else was looking:

`handler_names` — ffmpeg writes `VideoHandler`/`SoundHandler`, AVFoundation writes
`Core Media Video`/`Core Media Audio`. It is not a tag, so `exiftool -all=` leaves
it and reports the file unchanged (§5). M3 closed it in the scrubber; it is
measured here so the matrix can say so, and so a future change that stops closing
it fails a cell instead of passing quietly. M4A's plugin never measured it, which
is why that format leaked it from Phase 2 until MP4 went looking.

`mdat_header_form` — whether `mdat` uses the 64-bit largesize header. AVFoundation
writes it for a 17 KB box; ffmpeg does not. It is a producer choice, it is exactly
the bug M3 found (limit #41), and a fingerprint feature that a scrubber normalises
by construction is still worth measuring: it is the difference between "we handle
this" and "we believe we handle this".

The coded-video DIGEST is deliberately NOT in the categorical channel. Judging a
format on its own compressed content there made M4A the only format held to that
bar — two sources encoded differently hash differently no matter how perfectly the
container is scrubbed — while MP3's channel is headers only. The coded video
reaches this channel only through file SIZE, and the recoverability question (is
the encoder identifiable *from the picture*?) is an image-space question that needs
its own adversary simulation, not a hash comparison.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess

from src.scrub.standards import isobmff as iso


class Mp4Plugin:
    format_id = "mp4"

    def matches(self, header: bytes, path: str = "") -> bool:
        return len(header) >= 12 and header[4:8] == b"ftyp"

    def annotate(self, in_path: str, offset: int) -> str | None:
        """Deepest enclosing box, so evidence reads `moov/udta/loci` not `moov`."""
        try:
            boxes = iso.parse(open(in_path, "rb").read())
        except Exception:
            return None
        best = None
        for root in boxes:
            for box in root.walk():
                if box.offset <= offset < box.end:
                    if best is None or box.offset >= best.offset:
                        best = box
        if best is None:
            return None
        return f"{best.type.decode('latin-1', 'replace')}@+{offset - best.offset}"

    def canonical_content(self, path: str) -> bytes:
        """Decoded frames — the content identity.

        Decoded, not the coded bytes: the promise of F1 is that the picture is the
        same picture, and the M4A/HEIC lesson is that a file can keep byte-identical
        coded data and still decode to noise. A hash of `mdat` would have called
        limit #41's corrupt output a perfect content match.
        """
        if shutil.which("ffmpeg") is None:
            return b""
        p = subprocess.run(["ffmpeg", "-i", path, "-map", "0:v", "-an",
                            "-f", "rawvideo", "-pix_fmt", "yuv420p",
                            "-loglevel", "error", "pipe:1"],
                           capture_output=True)
        return hashlib.sha1(p.stdout).digest()

    def mandatory_constants(self) -> list[bytes]:
        # Format-required box names, plus the exact `hdlr` boxes a blanked-name
        # strip emits. The second group is a constant this tool introduces and the
        # fingerprint guard is right to flag it; it is declared rather than
        # suppressed, generated from the code that writes it, and bounded by a test
        # asserting it carries no locus (limit #42).
        names = [b"ftyp", b"moov", b"mdat", b"mvhd", b"trak", b"mdia", b"minf",
                 b"stbl", b"stsd", b"avc1", b"mp4a", b"vmhd", b"smhd"]
        return names + iso.canonical_handler_boxes()

    def structural_features(self, path: str) -> dict:
        """A2 structural channel — the muxer's choices. {} on parse failure."""
        try:
            data = open(path, "rb").read()
            boxes = iso.parse(data)
        except Exception:
            return {}

        top = [b.type.decode("latin-1", "replace") for b in boxes]
        ftyp = next((b for b in boxes if b.type == b"ftyp"), None)
        brand = ftyp.payload[:4].decode("latin-1", "replace") if ftyp else "none"
        compatible = tuple(
            ftyp.payload[i:i + 4].decode("latin-1", "replace")
            for i in range(8, len(ftyp.payload) - 3, 4)) if ftyp else ()

        mdat = next((b for b in boxes if b.type == b"mdat"), None)
        moov = next((b for b in boxes if b.type == b"moov"), None)
        moov_first = (moov.offset < mdat.offset
                      if moov is not None and mdat is not None else None)
        free_total = sum(b.size for root in boxes for b in root.walk()
                         if b.type in (b"free", b"skip"))
        all_types = tuple(sorted({b.type.decode("latin-1", "replace")
                                  for root in boxes for b in root.walk()}))

        # Handler NAMES, per track kind. See the module docstring: the field no
        # tag-oriented tool touches.
        handler_names = []
        for root in boxes:
            for box in root.walk():
                if box.type == b"hdlr" and len(box.payload) >= iso.HDLR_NAME_AT:
                    kind = box.payload[8:12].decode("latin-1", "replace")
                    name = (box.payload[iso.HDLR_NAME_AT:].rstrip(b"\x00")
                            .decode("utf-8", "replace"))
                    handler_names.append(f"{kind}:{name}")

        # Whether the timestamps are real or zeroed -- the SCHEME, not the value.
        # A wall-clock time and a zero are different producer behaviours before any
        # date is read, which is the shape the DOCX ZIP census found too.
        stamped = any(
            box.payload[4:4 + (8 if box.payload[:1] == b"\x01" else 4) * 2]
            .strip(b"\x00")
            for root in boxes for box in root.walk()
            if box.type in iso.TIMESTAMP_BOXES and len(box.payload) >= 12)

        # Track IDs and `next_track_ID`. F1 drops tracks (an iPhone clip's six
        # timed-metadata tracks) without renumbering the rest, so 1-3 survive with a
        # next ID of 10: a count of what was removed. Measured rather than patched
        # blind -- and a muxer's own numbering scheme would show up here too.
        track_ids: tuple = ()
        if moov is not None:
            ids = []
            for trak in (c for c in moov.children if c.type == b"trak"):
                tkhd = trak.find(b"tkhd")
                if tkhd is not None and len(tkhd.payload) >= 24:
                    at = 4 + (16 if tkhd.payload[0] == 1 else 8)
                    ids.append(int.from_bytes(tkhd.payload[at:at + 4], "big"))
            mvhd = moov.find(b"mvhd")
            nxt = (int.from_bytes(mvhd.payload[-4:], "big")
                   if mvhd is not None and len(mvhd.payload) >= 4 else None)
            track_ids = (tuple(sorted(ids)), nxt)

        return {
            "brand": brand,
            "compatible_brands": compatible,
            "top_level_order": tuple(top),
            "moov_before_mdat": moov_first,        # the faststart muxer choice
            "free_bytes": free_total,
            "box_inventory": all_types,
            "handler_names": tuple(sorted(handler_names)),
            "mdat_header_form": (mdat.header_len if mdat is not None else 0),
            "timestamps_present": stamped,
            "track_ids": track_ids,
        }

    def coded_video_digest(self, path: str) -> str:
        """Hash of the coded video, deliberately NOT in `structural_features`.

        Kept available for experiments that ask for it explicitly, for the same
        reason M4A keeps `coded_audio_digest`: presence of a trace is not a leak,
        recoverability is, and recoverability is not a hash comparison.
        """
        try:
            boxes = iso.parse(open(path, "rb").read())
        except Exception:
            return ""
        mdat = next((b for b in boxes if b.type == b"mdat"), None)
        return hashlib.sha1(mdat.payload if mdat else b"").hexdigest()[:16]
