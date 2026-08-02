"""FLAC structure walker (spec-parse depth).

A FLAC file is:
  [ optional ID3v2 tag — out of spec, but common in the wild ]
  "fLaC"  [ METADATA_BLOCK ]+  [ audio frames ]  [ optional trailing junk ]

Each metadata block is a 4-byte header — 1 bit "last block", 7 bits type, 24-bit
big-endian length — followed by its payload. STREAMINFO (type 0) must come first.

Unlike MP3, FLAC keeps metadata strictly *outside* the audio frames, so a
bit-preserving strip is exact by construction: drop the metadata blocks we do not
want and the audio bytes never move. That is what makes A2-at-F2 plausible here in
the first place (the PNG situation, not the JPEG one).

Every byte is accounted for into a named region. Fails closed (ParseError) on
anything unexplained — an unaccounted region could carry metadata.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ...errors import ParseError

MAGIC = b"fLaC"

# METADATA_BLOCK types (§ FLAC format spec).
STREAMINFO = 0
PADDING = 1
APPLICATION = 2
SEEKTABLE = 3
VORBIS_COMMENT = 4
CUESHEET = 5
PICTURE = 6

TYPE_NAMES = {
    STREAMINFO: "STREAMINFO", PADDING: "PADDING", APPLICATION: "APPLICATION",
    SEEKTABLE: "SEEKTABLE", VORBIS_COMMENT: "VORBIS_COMMENT",
    CUESHEET: "CUESHEET", PICTURE: "PICTURE",
}

# Blocks that carry user/producer metadata and must not survive a scrub. STREAMINFO
# stays: it is mandatory and describes the AUDIO (rates, channels, sample count, and
# an MD5 of the unencoded audio) — content, not metadata. SEEKTABLE is an index into
# the audio, but its granularity is an encoder choice, so it is a producer tell and
# gets normalised at F2 rather than trusted at F1.
METADATA_BLOCKS = (APPLICATION, VORBIS_COMMENT, CUESHEET, PICTURE)


@dataclass
class Block:
    type: int
    offset: int          # offset of the 4-byte header
    length: int          # payload length
    last: bool
    payload: bytes

    @property
    def total(self) -> int:
        return 4 + self.length

    @property
    def name(self) -> str:
        return TYPE_NAMES.get(self.type, f"RESERVED_{self.type}")


@dataclass
class Layout:
    id3v2: tuple[int, int] | None          # (start, end) of a non-spec ID3v2 prefix
    magic_offset: int
    blocks: list[Block] = field(default_factory=list)
    audio_start: int = 0
    audio_end: int = 0
    trailing: tuple[int, int] = (0, 0)     # (start, length) of bytes after audio

    @property
    def vorbis_comment(self) -> Block | None:
        return next((b for b in self.blocks if b.type == VORBIS_COMMENT), None)

    @property
    def has_metadata(self) -> bool:
        return any(b.type in METADATA_BLOCKS for b in self.blocks)


def _id3v2_len(data: bytes) -> int:
    """Length of a leading ID3v2 tag, or 0. Out of spec for FLAC, but taggers write
    it anyway — and a scrubber that only looks after 'fLaC' would leave it intact."""
    if len(data) < 10 or data[:3] != b"ID3":
        return 0
    if any(b & 0x80 for b in data[6:10]):        # syncsafe: high bit must be clear
        raise ParseError("FLAC: malformed ID3v2 size field")
    size = (data[6] << 21) | (data[7] << 14) | (data[8] << 7) | data[9]
    footer = 10 if (data[5] & 0x10) else 0
    return 10 + size + footer


def parse_vorbis_comment(payload: bytes) -> tuple[str, list[str]]:
    """-> (vendor string, [\"KEY=value\", ...]). Little-endian lengths, unlike every
    other field in FLAC — a genuine spec quirk (Vorbis heritage), and an easy bug."""
    if len(payload) < 4:
        raise ParseError("FLAC: truncated VORBIS_COMMENT")
    vlen = int.from_bytes(payload[:4], "little")
    if 4 + vlen + 4 > len(payload):
        raise ParseError("FLAC: VORBIS_COMMENT vendor string overruns block")
    vendor = payload[4:4 + vlen].decode("utf-8", "replace")
    pos = 4 + vlen
    count = int.from_bytes(payload[pos:pos + 4], "little")
    pos += 4
    items = []
    for _ in range(count):
        if pos + 4 > len(payload):
            raise ParseError("FLAC: VORBIS_COMMENT truncated comment count")
        n = int.from_bytes(payload[pos:pos + 4], "little")
        pos += 4
        if pos + n > len(payload):
            raise ParseError("FLAC: VORBIS_COMMENT comment overruns block")
        items.append(payload[pos:pos + n].decode("utf-8", "replace"))
        pos += n
    return vendor, items


def walk(data: bytes) -> Layout:
    id3_len = _id3v2_len(data)
    id3 = (0, id3_len) if id3_len else None
    pos = id3_len

    if data[pos:pos + 4] != MAGIC:
        raise ParseError("FLAC: missing fLaC magic")
    magic_offset = pos
    pos += 4

    blocks: list[Block] = []
    while True:
        if pos + 4 > len(data):
            raise ParseError("FLAC: truncated metadata block header")
        header = data[pos]
        last = bool(header & 0x80)
        btype = header & 0x7F
        length = int.from_bytes(data[pos + 1:pos + 4], "big")
        if pos + 4 + length > len(data):
            raise ParseError(f"FLAC: {TYPE_NAMES.get(btype, btype)} block overruns file")
        blocks.append(Block(btype, pos, length, last, data[pos + 4:pos + 4 + length]))
        pos += 4 + length
        if last:
            break
        if len(blocks) > 4096:                    # runaway guard
            raise ParseError("FLAC: implausible metadata block count")

    if not blocks or blocks[0].type != STREAMINFO:
        raise ParseError("FLAC: first metadata block must be STREAMINFO")

    audio_start = pos
    # Audio frames run to EOF. FLAC has no end marker, so anything appended is
    # indistinguishable from audio by structure alone -- we record the boundary and
    # let the fidelity tiers decide. (F2 re-encodes, which drops appended junk by
    # construction; F1 cannot tell, so it must not silently truncate.)
    audio_end = len(data)
    if audio_start >= audio_end:
        raise ParseError("FLAC: no audio frames after metadata")

    return Layout(id3v2=id3, magic_offset=magic_offset, blocks=blocks,
                  audio_start=audio_start, audio_end=audio_end,
                  trailing=(audio_end, 0))


def streaminfo(layout: Layout) -> dict:
    """Decode STREAMINFO — content facts, used by the plugin's A2 channel and by the
    F2 content check. min/max block and frame size are ENCODER choices, so they are
    producer tells even though the block itself is mandatory."""
    p = layout.blocks[0].payload
    if len(p) < 34:
        raise ParseError("FLAC: short STREAMINFO")
    bits = int.from_bytes(p[10:18], "big")
    return {
        "min_blocksize": int.from_bytes(p[0:2], "big"),
        "max_blocksize": int.from_bytes(p[2:4], "big"),
        "min_framesize": int.from_bytes(p[4:7], "big"),
        "max_framesize": int.from_bytes(p[7:10], "big"),
        "samplerate": (bits >> 44) & 0xFFFFF,
        "channels": ((bits >> 41) & 0x7) + 1,
        "bits_per_sample": ((bits >> 36) & 0x1F) + 1,
        "total_samples": bits & 0xFFFFFFFFF,
        "md5": p[18:34].hex(),
    }
