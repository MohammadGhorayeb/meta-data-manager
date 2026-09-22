"""MP4 structure walker: the *track* model.

M4A gave this project the ISOBMFF box layer and HEIC gave it an item table on top.
MP4's second layer is neither: it is a set of **tracks**, each with its own media
handler and its own table of absolute chunk offsets into one shared `mdat`. So the
walker's job is to turn `moov` into that table:

    mvhd            the movie header: timescale, duration, creation/modify times
    trak/tkhd       per-track header, with its own creation/modify times
    trak/mdia/hdlr  what KIND of track it is (`vide`, `soun`, ...) and its name
    trak/.../stco   where that track's chunks are, as ABSOLUTE file offsets
    udta            user data: `loci` (GPS) and `meta`/`keys`/`ilst` (tags)

Reading is accounting, not interpretation — the same contract as the HEIC, PDF and
ZIP walkers. Two things make that contract sharper here than it looks:

**Absolute offsets, conditionally.** `stco` points at chunks by absolute file
offset, so removing bytes *before* `mdat` invalidates every entry. Measured
(`docs/p4_media_plan.md` §5): `moov` sits after `mdat` in four of six producers, so
metadata removal often moves nothing at all — but every ffmpeg file carries an
8-byte `free` box *before* `mdat`, and dropping that moves everything. The walker
therefore reports `bytes_before_mdat` per box rather than leaving a caller to assume
either way.

**A track kind we do not model is refused, not skipped.** Phase 4 M1 put real phone
video out of scope, which makes an iPhone `.MOV`'s `mebx` timed-metadata track
(device motion sampled per frame) an unmeasured surface. Scrubbing around it would
turn "out of scope" into "leaks", so a file carrying one is refused with its handler
type named. That is the DOCX locus census's `UNCLASSIFIED` rule in another container.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from ...errors import ParseError
from ...standards import isobmff

MAGIC_OFFSET = 4                     # `ftyp` follows the 4-byte box size
FTYP = b"ftyp"

# Brands that mean "MP4 video". `isom` and `mp42` are the two measured in M0
# (ffmpeg and AVFoundation respectively); the rest are the ISO base-media family
# that is definitionally this container. Brand alone never decides — `isom` and
# `mp42` are also what an audio-only M4A may declare — so `looks_like_mp4()`
# additionally requires a video track. Failing to claim is the safe direction: an
# unrecognised brand means the tool declines rather than scrubs on a guess.
MP4_BRANDS = {b"isom", b"iso2", b"iso4", b"iso5", b"iso6", b"mp41", b"mp42",
              b"avc1", b"M4V ", b"M4VH", b"M4VP"}

# Track handler types we model. Anything else is refused by name rather than
# ignored -- see the module docstring.
#   vide  the picture
#   soun  the sound
# Deliberately absent: `mebx` (timed metadata -- Phase 4 M1 scope decision),
# `text`/`sbtl`/`subt` (subtitles are content we have not measured), `hint`
# (streaming hints, which carry their own offsets into other tracks' samples and
# would need patching we have not written).
MODELLED_HANDLERS = {b"vide", b"soun"}

# Boxes whose presence means the sample tables are not where we look for them.
# A fragmented MP4 keeps samples in `moof` runs with their own offset model, so
# every assumption below is wrong for one.
FRAGMENT_BOXES = {b"moof", b"mvex", b"mfra", b"sidx"}

# Boxes that mean the media is encrypted or protected. Scrubbing one would at best
# fail and at worst produce a file that no longer decrypts.
PROTECTION_BOXES = {b"sinf", b"pssh", b"schm", b"frma", b"senc"}

# Sample-entry formats that mean encrypted media even when no protection box sits
# at the top level.
PROTECTED_SAMPLE_ENTRIES = {b"encv", b"enca", b"encs", b"drms", b"drmi"}

# The metadata-bearing boxes this format has. Each is a locus in the Phase 3 sense:
# a place metadata lives, carrying a disposition. `loci` and `udta` are dropped
# whole; the header boxes are kept but with their time fields zeroed, because the
# rest of each header is structure the file cannot lose.
METADATA_BOXES = {b"udta", b"loci", b"meta", b"keys", b"ilst", b"free", b"skip"}


@dataclass
class Track:
    track_id: int = 0
    handler: bytes = b""             # `vide`, `soun`, ...
    handler_name: str = ""           # e.g. "VideoHandler", "Core Media Video"
    creation_time: int = 0
    modification_time: int = 0
    # (box type, absolute offsets) for this track's chunk tables. Kept as the
    # parsed values rather than the box, so a caller can check them against `mdat`
    # without re-reading the table.
    chunk_offsets: list[int] = field(default_factory=list)
    offset_table_kind: bytes = b""   # b"stco", b"co64", or b"" when absent

    @property
    def is_modelled(self) -> bool:
        return self.handler in MODELLED_HANDLERS


@dataclass
class Layout:
    boxes: list[isobmff.Box]
    tracks: list[Track] = field(default_factory=list)
    brand: bytes = b""
    compatible: list[bytes] = field(default_factory=list)
    mvhd_creation: int = 0
    mvhd_modification: int = 0
    mdat_offset: int = 0
    mdat_end: int = 0
    moov_before_mdat: bool = False

    @property
    def handlers(self) -> set[bytes]:
        return {t.handler for t in self.tracks}

    @property
    def has_video(self) -> bool:
        return b"vide" in self.handlers

    def top_level(self, box_type: bytes) -> list[isobmff.Box]:
        return [b for b in self.boxes if b.type == box_type]

    def removable_before_mdat(self) -> list[isobmff.Box]:
        """Top-level boxes sitting before `mdat` that a scrub would drop.

        This is the whole offset question in one list. `ftyp` stays (it identifies
        the file) and `mdat` is the payload; anything else ahead of `mdat` that we
        remove shifts the media and makes every `stco` entry wrong. Measured on the
        M0 corpus: this is `[free]` for every ffmpeg file and `[]` for
        AVFoundation's, which is exactly the difference between the patch being
        mandatory and being a no-op.
        """
        return [b for b in self.boxes
                if b.offset < self.mdat_offset
                and b.type not in (FTYP, b"mdat")
                and b.type in METADATA_BOXES]

    def bytes_removed_before_mdat(self) -> int:
        return sum(b.size for b in self.removable_before_mdat())

    def metadata_boxes(self) -> list[isobmff.Box]:
        """Every box in the file whose contents are metadata rather than media."""
        found = []
        for root in self.boxes:
            for box in root.walk():
                if box.type in (b"udta", b"loci"):
                    found.append(box)
                elif box.type in (b"free", b"skip"):
                    found.append(box)
        return found


def brand(data: bytes) -> bytes:
    if len(data) < 12 or data[MAGIC_OFFSET:8] != FTYP:
        return b""
    return data[8:12]


def compatible_brands(data: bytes) -> list[bytes]:
    """The brand list after the major brand and minor version."""
    if len(data) < 16 or data[MAGIC_OFFSET:8] != FTYP:
        return []
    try:
        size = struct.unpack_from(">I", data, 0)[0]
    except struct.error:
        return []
    tail = data[16:min(size, len(data))]
    return [tail[i:i + 4] for i in range(0, len(tail) - 3, 4)]


def looks_like_mp4(data: bytes) -> bool:
    """Identification: the brand narrows it, a video track decides it.

    `ftyp` is shared by every ISOBMFF file this project handles, and the brands are
    shared too — an audio-only M4A may declare `isom` or `mp42` exactly as an MP4
    does. What separates them is what is inside: this claims a file only when it
    carries a video track, so the M4A handler (which claims audio and explicitly
    refuses anything with a `vide` handler) and this one cannot both take a file.

    Cheap and total: any failure to parse is a decline, never an exception, because
    identification runs on every file the tool is given.
    """
    major = brand(data)
    if not major:
        return False
    if major not in MP4_BRANDS and not any(
            b in MP4_BRANDS for b in compatible_brands(data)):
        return False
    try:
        boxes = isobmff.parse(data)
    except Exception:
        return False
    for root in boxes:
        for box in root.walk():
            if box.type == b"hdlr" and len(box.payload) >= 12:
                if box.payload[8:12] == b"vide":
                    return True
    return False


def _u32(payload: bytes, at: int) -> int:
    if at + 4 > len(payload):
        raise ParseError("MP4: truncated box payload")
    return struct.unpack_from(">I", payload, at)[0]


def _u64(payload: bytes, at: int) -> int:
    if at + 8 > len(payload):
        raise ParseError("MP4: truncated box payload")
    return struct.unpack_from(">Q", payload, at)[0]


def _header_times(payload: bytes, kind: str) -> tuple[int, int, int]:
    """(creation, modification, track_id) from an `mvhd` or `tkhd` payload.

    Both are full boxes whose field WIDTHS depend on the version byte: version 0
    writes 32-bit times, version 1 writes 64-bit. Reading a version-1 box as
    version 0 does not fail, it silently returns the top half of a timestamp — so
    the version is read rather than assumed, and an unknown one is refused.
    """
    if len(payload) < 4:
        raise ParseError(f"MP4: {kind} too short to carry a version")
    version = payload[0]
    if version == 0:
        creation, modification = _u32(payload, 4), _u32(payload, 8)
        third = _u32(payload, 12)                  # timescale (mvhd) / id (tkhd)
    elif version == 1:
        creation, modification = _u64(payload, 4), _u64(payload, 12)
        third = _u32(payload, 20)
    else:
        raise ParseError(f"MP4: {kind} version {version} not modelled")
    return creation, modification, third


def _parse_chunk_offsets(stbl: isobmff.Box) -> tuple[bytes, list[int]]:
    """(table kind, absolute offsets) for one track's sample table."""
    for kind, width in ((b"stco", 4), (b"co64", 8)):
        box = next((c for c in stbl.children if c.type == kind), None)
        if box is None:
            continue
        payload = box.payload
        if len(payload) < 8:
            raise ParseError(f"MP4: {kind.decode()} shorter than its header")
        count = struct.unpack_from(">I", payload, 4)[0]
        need = 8 + count * width
        if need > len(payload):
            raise ParseError(
                f"MP4: {kind.decode()} claims {count} entries but holds "
                f"{(len(payload) - 8) // width}")
        reader = _u32 if width == 4 else _u64
        return kind, [reader(payload, 8 + i * width) for i in range(count)]
    return b"", []


def _parse_track(trak: isobmff.Box) -> Track:
    track = Track()
    tkhd = next((c for c in trak.children if c.type == b"tkhd"), None)
    if tkhd is None:
        raise ParseError("MP4: trak with no tkhd (cannot identify the track)")
    creation, modification, track_id = _header_times(tkhd.payload, "tkhd")
    track.creation_time, track.modification_time = creation, modification
    track.track_id = track_id

    hdlr = trak.find(b"mdia/hdlr")
    if hdlr is None:
        raise ParseError(f"MP4: track {track_id} has no hdlr (cannot tell its kind)")
    if len(hdlr.payload) < 24:
        raise ParseError(f"MP4: track {track_id} hdlr too short")
    track.handler = hdlr.payload[8:12]
    track.handler_name = (hdlr.payload[24:].split(b"\x00")[0]
                          .decode("utf-8", "replace"))

    stbl = trak.find(b"mdia/minf/stbl")
    if stbl is None:
        raise ParseError(f"MP4: track {track_id} has no sample table")
    track.offset_table_kind, track.chunk_offsets = _parse_chunk_offsets(stbl)
    return track


def walk(data: bytes) -> Layout:
    """Parse an MP4 into the track model, refusing anything we do not model.

    Every refusal below names what it refused. A scrub that proceeds on a file it
    only partly understands is the failure mode this project keeps finding: the
    output parses, plays, and still carries the thing it was asked to remove.
    """
    if not looks_like_mp4(data):
        raise ParseError("MP4: not an MP4 video (brand or video track missing)")

    boxes = isobmff.parse(data)
    layout = Layout(boxes=boxes, brand=brand(data),
                    compatible=compatible_brands(data))

    present = {b.type for root in boxes for b in root.walk()}
    fragmented = present & FRAGMENT_BOXES
    if fragmented:
        raise ParseError(
            "MP4: fragmented file ("
            + ", ".join(sorted(b.decode("latin-1") for b in fragmented))
            + ") -- samples live in movie fragments with their own offset model, "
              "not in moov's sample tables")
    protected = present & PROTECTION_BOXES
    if protected:
        raise ParseError(
            "MP4: protected/encrypted media ("
            + ", ".join(sorted(b.decode("latin-1") for b in protected)) + ")")

    mdats = [b for b in boxes if b.type == b"mdat"]
    if not mdats:
        raise ParseError("MP4: no mdat box (no media to preserve)")
    if len(mdats) > 1:
        raise ParseError(
            f"MP4: {len(mdats)} mdat boxes -- chunk offsets would have to be "
            "patched per region, which is untested")
    layout.mdat_offset, layout.mdat_end = mdats[0].offset, mdats[0].end

    moov = next((b for b in boxes if b.type == b"moov"), None)
    if moov is None:
        raise ParseError("MP4: no moov box (no track table)")
    layout.moov_before_mdat = moov.offset < layout.mdat_offset

    mvhd = next((c for c in moov.children if c.type == b"mvhd"), None)
    if mvhd is None:
        raise ParseError("MP4: no mvhd box (no movie header)")
    layout.mvhd_creation, layout.mvhd_modification, _ = _header_times(
        mvhd.payload, "mvhd")

    for trak in (c for c in moov.children if c.type == b"trak"):
        layout.tracks.append(_parse_track(trak))
    if not layout.tracks:
        raise ParseError("MP4: moov carries no trak boxes")

    # A sample entry can declare encryption on its own, without any protection box
    # at the top level -- so the format code is checked too, not just the boxes.
    for root in boxes:
        for box in root.walk():
            if box.type != b"stsd" or len(box.payload) < 16:
                continue
            fmt = box.payload[12:16]
            if fmt in PROTECTED_SAMPLE_ENTRIES:
                raise ParseError(
                    f"MP4: encrypted sample entry {fmt.decode('latin-1')}")

    unmodelled = sorted({t.handler for t in layout.tracks if not t.is_modelled})
    if unmodelled:
        raise ParseError(
            "MP4: unmodelled track handler(s) "
            + ", ".join(h.decode("latin-1", "replace") for h in unmodelled)
            + " -- this tier refuses rather than scrubbing around a track whose "
              "contents it has never measured (docs/p4_media_plan.md section 8)")

    # Every chunk must land inside the media box. An offset that does not is either
    # a file we have misread or one already broken, and in both cases patching it
    # would move a pointer we do not understand.
    for track in layout.tracks:
        for offset in track.chunk_offsets:
            if not (layout.mdat_offset <= offset < layout.mdat_end):
                raise ParseError(
                    f"MP4: track {track.track_id} chunk offset {offset} falls "
                    f"outside mdat [{layout.mdat_offset}, {layout.mdat_end})")
    return layout
