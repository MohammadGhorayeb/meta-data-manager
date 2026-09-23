"""MP4 F1 — bit-preserving metadata strip (the picture and sound untouched).

What goes, and why each one is here rather than assumed:

  * `moov/udta` whole — which holds BOTH metadata containers M0 found the same
    coordinate in: `loci` (the QuickTime location box, binary, written whenever a
    muxer is handed a location) and `meta`/`keys`/`ilst` (the tag list, where
    `©too` carries the encoder). One `-metadata location` writes the coordinate to
    three places at once, so anything that removed only `ilst` would leave the GPS
    sitting in `loci` (`docs/p4_media_plan.md` §5).
  * `moov/meta` — the same tag store when a muxer hangs it off `moov` directly.
  * `free` / `skip` anywhere — dead space that can hold arbitrary bytes, and whose
    size is a muxer tell in its own right. Every ffmpeg file carries one *before*
    `mdat`, which is the whole offset problem below.
  * `uuid` — where XMP and vendor payloads live in this container.
  * Creation and modification times in `mvhd`, `tkhd` **and `mdhd`**. Three boxes,
    not two: `mdhd` holds a per-track media time that ExifTool reports as
    MediaCreateDate, so a two-track file stamps the same second in ten fields.
  * `hdlr` track names. This is the one M0 was written to find: ffmpeg writes
    `VideoHandler`, AVFoundation writes `Core Media Video`, and because it is not a
    tag, `exiftool -all=` leaves it and reports the file **unchanged**.

What stays: the coded video and audio, byte for byte. F1 does not re-encode and
does not re-mux; it deletes, blanks and re-lays-out the container.

THE TRAP, and it is conditional here in a way it was not for M4A. `stco` holds
absolute file offsets, so patching is needed only when bytes vanish *ahead of*
`mdat`. Measured: `moov` sits after `mdat` in four of six producers, so on those
files this strip removes a kilobyte of tags and moves nothing at all — while the
8-byte `free` box every ffmpeg file puts before `mdat` moves everything. Both paths
run through the same measure-then-patch code, because the dangerous version of this
format is the one where a reader concludes "offsets are safe".
"""
from __future__ import annotations

from ...standards import isobmff as iso
from . import walker as w

# Dropped wherever they appear.
DROP_TYPES = {b"udta", b"meta", b"free", b"skip", b"uuid"}


def scrub(data: bytes) -> bytes:
    """Strip an MP4, refusing anything the walker does not model.

    The walker runs first and for its refusals, not for its model: a file with a
    `mebx` track, a fragment box, an encrypted sample entry or a chunk offset
    outside `mdat` must be declined before a single byte is rewritten, because a
    partial clean that reports success is worse than a refusal.
    """
    w.walk(data)
    return iso.strip_and_repack(data, DROP_TYPES, label="MP4",
                                blank_handler_names=True)


def residuals(data: bytes) -> list[str]:
    """Re-walk scrubbed output. Anything below is a leak we shipped.

    Written against the output rather than the input on purpose: this is the check
    that would catch a strip which removed the boxes it knew about and left one it
    did not.
    """
    out: list[str] = []
    boxes = iso.parse(data)
    for root in boxes:
        for box in root.walk():
            name = box.type.decode("latin-1", "replace")
            if box.type in DROP_TYPES:
                out.append(f"{name} box survived at offset {box.offset}")
            if box.type in iso.TIMESTAMP_BOXES:
                width = 8 if (box.payload and box.payload[0] == 1) else 4
                if box.payload[4:4 + width * 2].strip(b"\x00"):
                    out.append(f"{name} timestamps survived")
            if box.type == b"hdlr":
                handler_name = box.payload[iso.HDLR_NAME_AT:].rstrip(b"\x00")
                if handler_name:
                    out.append(
                        f"hdlr name {handler_name.decode('latin-1', 'replace')!r} "
                        "survived — this is the field ExifTool does not write")

    # Defense in depth over the METADATA region only. Never the media payload:
    # coded H.264 is dense binary and contains short markers by pure chance, so a
    # whole-file scan reports GPS in every clean file it is handed.
    meta_only = b"".join(iso.serialize([b]) for b in boxes if b.type != b"mdat")
    for magic, label in ((b"loci", "the QuickTime location box"),
                         (b"\xa9xyz", "an ISO-6709 location atom"),
                         (b"\xa9too", "the encoder atom"),
                         (b"\xa9nam", "a title atom"),
                         (b"keys", "a Keys-namespace metadata table"),
                         (b"ilst", "a tag list")):
        if magic in meta_only:
            out.append(f"{label} is present in the metadata region")
    return out
