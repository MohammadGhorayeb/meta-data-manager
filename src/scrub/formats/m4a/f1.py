"""M4A F1 — bit-preserving metadata strip (audio samples untouched).

MAT2 refuses M4A outright, so this is the one format in Phase 2 where the benchmark
is a gap to beat rather than match.

What goes:
  * `moov/udta` — the iTunes metadata tree (`meta/ilst`: ©nam title, ©ART artist,
    ©too the encoder that made the file, and `covr` cover art, which is a nested
    image carrying its own EXIF/GPS).
  * `moov/meta` — the same store when a muxer hangs it directly off `moov`.
  * `free` / `skip` boxes anywhere — dead space that can hold arbitrary bytes, and
    whose *size* is itself a muxer tell.
  * `uuid` boxes — where XMP and vendor payloads live.
  * Creation and modification timestamps in `mvhd` / `tkhd` / `mdhd`. These are the
    easy ones to miss: they are structural fields, not tags, so a tag-oriented
    scrubber leaves them and the file still says exactly when it was made.

THE TRAP this tier has to solve: `stco` / `co64` hold **absolute file offsets** of
the audio chunks. Removing any box ahead of `mdat` slides the audio, and a file with
stale offsets still parses, still reports the right duration, and decodes to
garbage. So the strip is: rebuild the tree, measure how far `mdat` moved, patch every
chunk offset by that delta, and verify the audio bytes are unchanged before
returning. Fail closed if any of that does not hold.
"""
from __future__ import annotations

from ...standards import isobmff as iso

# Dropped wherever they appear.
DROP_TYPES = {b"udta", b"meta", b"free", b"skip", b"uuid"}

# Kept as a module-level name because `residuals()` below reports against it and
# because tests import it. The set itself now lives in `standards/isobmff.py`,
# shared with MP4 -- see the note there about why this engine was moved.
TIMESTAMP_BOXES = iso.TIMESTAMP_BOXES


def scrub(data: bytes) -> bytes:
    """Strip an M4A through the shared ISOBMFF engine.

    `blank_handler_names=True` is not cosmetic and was not here originally. An
    AVFoundation-muxed M4A carries `Core Media Audio` in its `hdlr` name, and this
    tier used to leave it: the name is not a tag, so nothing tag-oriented removes
    it -- `exiftool -all=` reports such a file unchanged. Measured while building
    MP4 (docs/p4_media_plan.md section 5), and fixed here rather than only there,
    because the leak was in the shipped format too.
    """
    return iso.strip_and_repack(data, DROP_TYPES, label="M4A",
                                blank_handler_names=True)


def residuals(data: bytes) -> list[str]:
    """Re-walk scrubbed output; anything but a clean audio-only file is a leak."""
    out: list[str] = []
    boxes = iso.parse(data)
    for b in boxes:
        if b.type not in iso.TOP_LEVEL_KEEP:
            out.append(f"top-level {b.type.decode('latin-1', 'replace')!r} box "
                       f"survived at {b.offset} -- outside the keep list")
    for root in boxes:
        for box in root.walk():
            if box.type in DROP_TYPES:
                out.append(f"{box.type.decode('latin-1')} box survived at {box.offset}")
            if box.type in TIMESTAMP_BOXES:
                version = box.payload[0] if box.payload else 0
                width = 8 if version == 1 else 4
                stamps = box.payload[4:4 + width * 2]
                if stamps.strip(b"\x00"):
                    out.append(f"{box.type.decode('latin-1')} timestamps survived")
            if box.type == b"hdlr":
                name = box.payload[iso.HDLR_NAME_AT:].rstrip(b"\x00")
                if name:
                    out.append(
                        f"hdlr name {name.decode('latin-1', 'replace')!r} survived")
    # Defense in depth: iTunes atom names and art magics must not survive. Scanned
    # over the METADATA region only, never the audio payload -- coded AAC is dense
    # binary and contains short markers like JPEG's `FF D8 FF` by pure chance, so a
    # whole-file scan reports art in every clean file it is handed.
    meta_only = b"".join(iso.serialize([b]) for b in boxes if b.type != b"mdat")
    for magic, label in ((b"\xa9nam", "iTunes title atom"), (b"\xa9ART", "artist atom"),
                         (b"covr", "cover art atom"), (b"\xff\xd8\xff", "JPEG art")):
        if magic in meta_only:
            out.append(f"{label} present in the metadata region")
    return out
