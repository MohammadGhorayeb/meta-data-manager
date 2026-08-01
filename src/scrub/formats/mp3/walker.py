"""MP3 structure walker (spec-parse depth).

An MP3 file is, in order:
  [ ID3v2 tag ]  [ MPEG audio frames (maybe a Xing/Info+LAME header frame first) ]
  [ EOF trailer stack: APEv2 / Lyrics3 / ID3v1 / appended ID3v2.4 footer, any order ]
  with any non-trailer bytes past the last valid frame being appended "hitchhiker"
  data (a covert channel).

This walker accounts for every byte into named regions so the F1 handler can drop
the metadata ones and keep the audio verbatim. Audio is located by MPEG frame-header
*sizes*, never by scanning for 0xFFFB (which appears inside tags and payloads).

Fails closed (ParseError) on anything it cannot account for — an unexplained region
could carry metadata.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ...errors import ParseError

# --- MPEG audio frame header tables (Layer III focus) ------------------------
# version: 0=MPEG2.5, 2=MPEG2, 3=MPEG1 (1=reserved). layer: 1=L3,2=L2,3=L1.
_BITRATE = {
    # (version_is_mpeg1, layer3) -> [index0..15] kbps  (0=free,15=bad)
    True:  [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0],
    False: [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0],
}
_SAMPLERATE = {
    3: [44100, 48000, 32000, 0],   # MPEG1
    2: [22050, 24000, 16000, 0],   # MPEG2
    0: [11025, 12000, 8000, 0],    # MPEG2.5
}
_SAMPLES_PER_FRAME = {3: 1152, 2: 576, 0: 576}   # Layer III


@dataclass
class FrameHeader:
    offset: int
    length: int
    version: int          # 3=MPEG1, 2=MPEG2, 0=MPEG2.5
    layer: int            # 1=Layer III
    bitrate: int          # kbps
    samplerate: int       # Hz
    padding: int
    channel_mode: int     # 0=stereo,1=joint,2=dual,3=mono
    header: bytes         # the 4 header bytes

    @property
    def is_mono(self) -> bool:
        return self.channel_mode == 3

    @property
    def side_info_size(self) -> int:
        # Layer III side-info: MPEG1 32(stereo)/17(mono); MPEG2/2.5 17/9.
        if self.version == 3:
            return 17 if self.is_mono else 32
        return 9 if self.is_mono else 17


def parse_frame_header(data: bytes, pos: int) -> Optional[FrameHeader]:
    """Decode a Layer-III MPEG audio frame header at pos, or None if not a valid
    frame sync there."""
    if pos + 4 > len(data):
        return None
    b0, b1, b2, b3 = data[pos], data[pos + 1], data[pos + 2], data[pos + 3]
    if b0 != 0xFF or (b1 & 0xE0) != 0xE0:      # 11 sync bits
        return None
    version = (b1 >> 3) & 0x03
    layer = (b1 >> 1) & 0x03
    if version == 1 or layer != 0x01:          # reserved version / not Layer III
        return None
    br_index = (b2 >> 4) & 0x0F
    sr_index = (b2 >> 2) & 0x03
    padding = (b2 >> 1) & 0x01
    if br_index in (0, 15) or sr_index == 3:   # free/bad bitrate or reserved sr
        return None
    is_mpeg1 = version == 3
    bitrate = _BITRATE[is_mpeg1][br_index]
    samplerate = _SAMPLERATE[version][sr_index]
    if bitrate == 0 or samplerate == 0:
        return None
    channel_mode = (b3 >> 6) & 0x03
    spf = _SAMPLES_PER_FRAME[version]
    length = (spf // 8 * bitrate * 1000) // samplerate + padding
    if length < 4:
        return None
    return FrameHeader(pos, length, version, layer, bitrate, samplerate,
                       padding, channel_mode, data[pos:pos + 4])


# --- structure ---------------------------------------------------------------
@dataclass
class XingInfo:
    frame_offset: int          # start of the Xing/Info header frame
    magic_offset: int          # offset of b"Xing"/b"Info"
    magic: bytes               # b"Xing" (VBR) or b"Info" (CBR)
    lame_offset: Optional[int] # offset of the 9-byte LAME version string, if present
    lame_version: Optional[bytes]


@dataclass
class Trailer:
    kind: str                  # "id3v1" | "apev2" | "lyrics3" | "id3v2_footer"
    offset: int
    length: int


@dataclass
class Mp3Layout:
    data: bytes
    id3v2: Optional[tuple]         # (offset, length) of the front ID3v2 tag
    audio_start: int
    audio_end: int                 # end of the last valid MPEG frame
    frames: list                   # list[FrameHeader]
    xing: Optional[XingInfo]
    appended: tuple                # (offset, length) of hitchhiker bytes (0 len = none)
    trailers: list                 # list[Trailer], file order

    def audio_bytes(self) -> bytes:
        return self.data[self.audio_start:self.audio_end]


def _synchsafe(b: bytes) -> int:
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def _parse_id3v2_front(data: bytes) -> Optional[tuple]:
    """Return (offset=0, total_length) of a leading ID3v2 tag, or None."""
    if len(data) < 10 or data[:3] != b"ID3":
        return None
    flags = data[5]
    size = _synchsafe(data[6:10])
    total = 10 + size
    if flags & 0x10:               # footer present (v2.4)
        total += 10
    if total > len(data):
        raise ParseError("ID3v2 tag size runs past EOF")
    return (0, total)


def _scan_tail(data: bytes, audio_end: int) -> list:
    """Best-effort classification of everything past the last audio frame (all of
    which F1 drops). Records which known trailer types are present — for the
    residual guard and reporting — without needing exact interleaving with any
    appended hitchhiker bytes."""
    tail = data[audio_end:]
    trailers: list[Trailer] = []
    # APEv2 — locate its "APETAGEX" footer and size the tag.
    ape = tail.find(b"APETAGEX")
    if ape != -1:
        # footer is 32B; find the footer whose 8 magic bytes we matched last
        foot_pos = tail.rfind(b"APETAGEX")
        foot = tail[foot_pos:foot_pos + 32]
        if len(foot) >= 24:
            size = int.from_bytes(foot[12:16], "little")
            flags = int.from_bytes(foot[20:24], "little")
            total = size + (32 if (flags & 0x80000000) else 0)
            trailers.append(Trailer("apev2", audio_end + max(0, foot_pos + 32 - total), total))
    if b"LYRICS200" in tail or b"LYRICSBEGIN" in tail:
        trailers.append(Trailer("lyrics3", audio_end + tail.find(b"LYRICSBEGIN"), 0))
    if b"3DI" in tail:
        trailers.append(Trailer("id3v2_footer", audio_end + tail.find(b"3DI"), 0))
    # ID3v1: a 128-byte block starting with "TAG" (final block, or before other trailers)
    i = tail.find(b"TAG")
    while i != -1:
        if i + 128 <= len(tail) or i + 128 == len(tail):
            trailers.append(Trailer("id3v1", audio_end + i, 128))
            break
        i = tail.find(b"TAG", i + 1)
    return trailers


def _locate_xing(data: bytes, first: FrameHeader) -> Optional[XingInfo]:
    base = first.offset + 4 + first.side_info_size
    magic = data[base:base + 4]
    if magic not in (b"Xing", b"Info"):
        return None
    # LAME tag: after Xing header fields. Rather than re-derive every optional
    # field, locate the encoder version string within this frame (it starts the
    # 36-byte LAME tag, e.g. b"LAME3.100" or b"Lavc..."/b"L3.99...").
    frame = data[first.offset:first.offset + first.length]
    lame_off = None
    lame_ver = None
    for sig in (b"LAME", b"Lavc", b"L3.", b"GOGO", b"Shine"):
        i = frame.find(sig)
        if i != -1:
            lame_off = first.offset + i
            lame_ver = data[lame_off:lame_off + 9]
            break
    return XingInfo(first.offset, base, magic, lame_off, lame_ver)


def walk(data: bytes) -> Mp3Layout:
    n = len(data)
    if n < 4:
        raise ParseError("file too small to be an MP3")

    id3v2 = _parse_id3v2_front(data)
    audio_start = id3v2[1] if id3v2 else 0

    # Skip minimal junk before the first frame sync.
    pos = audio_start
    while pos < n and parse_frame_header(data, pos) is None:
        pos += 1
        if pos - audio_start > 8192:
            raise ParseError("no MPEG frame sync found near audio start")
    audio_start = pos

    # Greedy contiguous frame walk — audio is authoritative; the first non-frame
    # byte (a tag/trailer/hitchhiker) ends the audio region.
    frames: list[FrameHeader] = []
    while pos < n:
        fh = parse_frame_header(data, pos)
        if fh is None or pos + fh.length > n:
            break
        frames.append(fh)
        pos += fh.length
    if not frames:
        raise ParseError("no valid MPEG audio frames found")
    audio_end = pos

    xing = _locate_xing(data, frames[0])
    trailers = _scan_tail(data, audio_end)
    appended = (audio_end, n - audio_end)   # entire post-audio region (F1 drops it)

    return Mp3Layout(data=data, id3v2=id3v2, audio_start=audio_start,
                     audio_end=audio_end, frames=frames, xing=xing,
                     appended=appended, trailers=trailers)
