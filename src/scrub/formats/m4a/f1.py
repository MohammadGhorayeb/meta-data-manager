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

from ...errors import ParseError, ScrubError
from ...standards import isobmff as iso

# Dropped wherever they appear.
DROP_TYPES = {b"udta", b"meta", b"free", b"skip", b"uuid"}

# Boxes whose first fields are creation/modification times (after version+flags).
TIMESTAMP_BOXES = {b"mvhd", b"tkhd", b"mdhd"}


def _strip(boxes: list[iso.Box]) -> list[iso.Box]:
    kept = []
    for b in boxes:
        if b.type in DROP_TYPES:
            continue
        if b.children:
            b.children = _strip(b.children)
        if b.type in TIMESTAMP_BOXES:
            b.payload = _zero_timestamps(b.payload, b.type)
        kept.append(b)
    return kept


def _zero_timestamps(payload: bytes, btype: bytes) -> bytes:
    """Zero creation_time and modification_time, keeping every other field.

    Version 0 stores them as 32-bit, version 1 as 64-bit, and both sit immediately
    after the 4-byte version+flags — so the width depends on a byte we must read
    rather than assume.
    """
    if len(payload) < 4:
        raise ParseError(f"ISOBMFF: short {btype!r}")
    version = payload[0]
    width = 8 if version == 1 else 4
    if len(payload) < 4 + width * 2:
        raise ParseError(f"ISOBMFF: {btype!r} too short for its timestamps")
    return payload[:4] + b"\x00" * (width * 2) + payload[4 + width * 2:]


def _mdat_offset(boxes: list[iso.Box]) -> int | None:
    """Offset of the mdat PAYLOAD in a serialized tree (audio lives there)."""
    pos = 0
    for b in boxes:
        if b.type == b"mdat":
            header = iso.HEADER_MIN
            if header + len(b.payload) > 0xFFFFFFFF:
                header += 8                      # largesize form
            return pos + header
        pos += len(iso.serialize([b]))
    return None


def scrub(data: bytes) -> bytes:
    boxes = iso.parse(data)
    if not any(b.type == b"ftyp" for b in boxes):
        raise ParseError("M4A: no ftyp box")
    mdat = next((b for b in boxes if b.type == b"mdat"), None)
    if mdat is None:
        raise ParseError("M4A: no mdat box (no audio to preserve)")
    old_audio_offset = _mdat_offset(boxes)
    audio = mdat.payload

    stripped = _strip(boxes)
    new_audio_offset = _mdat_offset(stripped)
    if new_audio_offset is None:
        raise ScrubError("M4A: mdat vanished during strip")

    delta = new_audio_offset - old_audio_offset
    if delta:
        patched = iso.shift_chunk_offsets(stripped, delta)
        if patched == 0:
            # No table to patch, yet the audio moved: every chunk offset that does
            # exist is now wrong, and we cannot know there were none. Refuse.
            raise ScrubError(
                f"M4A: audio moved by {delta} bytes but no stco/co64 table was "
                "found to patch — refusing to emit a file whose sample tables "
                "may point at the wrong bytes")
        # Patching cannot change table sizes (same entry count and width), so the
        # offset measured above still holds. Assert it rather than assume it.
        if _mdat_offset(stripped) != new_audio_offset:
            raise ScrubError("M4A: patching chunk offsets moved mdat again")

    out = iso.serialize(stripped)

    # The promise of this tier: the audio bytes are the same bytes.
    check = iso.parse(out)
    out_mdat = next((b for b in check if b.type == b"mdat"), None)
    if out_mdat is None or out_mdat.payload != audio:
        raise ScrubError("M4A F1 altered the audio samples")
    return out


def residuals(data: bytes) -> list[str]:
    """Re-walk scrubbed output; anything but a clean audio-only file is a leak."""
    out: list[str] = []
    boxes = iso.parse(data)
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
    # Defense in depth: iTunes atom names and art magics must not appear anywhere.
    for magic, label in ((b"\xa9nam", "iTunes title atom"), (b"\xa9ART", "artist atom"),
                         (b"covr", "cover art atom"), (b"\xff\xd8\xff", "JPEG art")):
        if magic in data:
            out.append(f"{label} present in output")
    return out
