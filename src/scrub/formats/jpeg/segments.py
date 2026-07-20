"""JPEG marker/segment walker (spec-parse depth).

One honest walk of the marker stream, shared by the F1 handler, the harness
plugin's annotate(), and the corpus injector. A walker bug would hide from the
differential test (handler and plugin share the blind spot), so this module is
unit-tested directly against crafted byte streams (p1 plan §5).

Marker model (ITU-T T.81):
  - A marker is 0xFF followed by a code byte (not 0x00, not 0xFF). Any number of
    0xFF fill bytes may precede the code byte.
  - Standalone (no length): SOI, EOI, RST0-7, TEM.
  - All others carry a 2-byte big-endian length that INCLUDES the length bytes
    but NOT the 0xFF+code.
  - SOS is followed by entropy-coded data (not length-covered): scan to the next
    real marker, treating 0xFF00 as a stuffed literal and 0xFFD0-D7 (RST) as part
    of the entropy stream. Progressive JPEGs have multiple SOS segments.
  - Bytes after EOI are the trailer — a first-class metadata locus.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ...errors import ParseError
from ...standards import icc, iptc_iim, xmp

# --- marker codes ---
SOI = 0xD8
EOI = 0xD9
SOS = 0xDA
DQT = 0xDB
DHT = 0xC4
DRI = 0xDD
DAC = 0xCC        # arithmetic conditioning (rare, content-critical)
COM = 0xFE
TEM = 0x01
APP0 = 0xE0
APP15 = 0xEF

_RST = set(range(0xD0, 0xD8))
_STANDALONE = {SOI, EOI, TEM} | _RST
# SOF markers: 0xC0-0xCF minus DHT(C4), JPG(C8), DAC(CC).
_SOF = {c for c in range(0xC0, 0xD0)} - {DHT, 0xC8, DAC}

# Content-critical markers: the image is defined by these (F1 keep-list core).
CONTENT_MARKERS = {SOI, EOI, SOS, DQT, DHT, DRI, DAC} | _SOF


@dataclass
class Segment:
    marker: int              # code byte, e.g. 0xE1 for APP1
    offset: int              # index of the leading 0xFF in the file
    length: int              # total bytes consumed (marker+len+payload, or
                             #   marker+scan-header+entropy for SOS, or 2 for
                             #   standalone)
    payload: bytes           # payload bytes only (empty for standalone/SOS-body)
    kind: str                # classification, e.g. "app1_exif", "sos", "dqt"

    @property
    def end(self) -> int:
        return self.offset + self.length

    @property
    def is_content(self) -> bool:
        return self.marker in CONTENT_MARKERS


@dataclass
class JpegStructure:
    segments: list[Segment]
    trailer: bytes           # bytes after EOI
    trailer_offset: int      # where the trailer starts (== EOI end)

    def by_kind(self, kind: str) -> list[Segment]:
        return [s for s in self.segments if s.kind == kind]

    def markers(self) -> list[int]:
        return [s.marker for s in self.segments]


def _classify(marker: int, payload: bytes) -> str:
    if marker == SOI:
        return "soi"
    if marker == EOI:
        return "eoi"
    if marker == SOS:
        return "sos"
    if marker == DQT:
        return "dqt"
    if marker == DHT:
        return "dht"
    if marker == DRI:
        return "dri"
    if marker == DAC:
        return "dac"
    if marker in _SOF:
        return f"sof{marker - 0xC0}"
    if marker == COM:
        return "com"
    if APP0 <= marker <= APP15:
        n = marker - APP0
        if marker == APP0:
            if payload.startswith(b"JFIF\x00"):
                return "app0_jfif"
            if payload.startswith(b"JFXX\x00"):
                return "app0_jfxx"
        if marker == 0xE1:
            if xmp.is_extended(payload):
                return "app1_xmp_extended"
            if xmp.is_standard(payload):
                return "app1_xmp"
            if payload.startswith(b"Exif\x00\x00"):
                return "app1_exif"
        if marker == 0xE2:
            if payload.startswith(icc.JPEG_ICC_SIG):
                return "app2_icc"
            if payload.startswith(b"MPF\x00"):
                return "app2_mpf"
        if marker == 0xED and iptc_iim.photoshop_sig_len(payload):
            return "app13_photoshop"
        if marker == 0xEE and payload.startswith(b"Adobe\x00"):
            return "app14_adobe"
        return f"app{n}"
    return f"marker_{marker:02x}"


def _next_marker(data: bytes, pos: int) -> int:
    """Advance past 0xFF fill bytes and return the index of the 0xFF that begins
    the next marker. Raises if no marker byte is found."""
    n = len(data)
    if pos >= n or data[pos] != 0xFF:
        raise ParseError(f"expected marker (0xFF) at offset {pos}")
    # Collapse fill bytes: the real marker is the last 0xFF before the code.
    while pos + 1 < n and data[pos + 1] == 0xFF:
        pos += 1
    return pos


def _scan_entropy(data: bytes, start: int) -> int:
    """From the start of entropy-coded data, return the offset of the 0xFF that
    begins the next real marker. 0xFF00 is a stuffed literal; 0xFFD0-D7 are RST
    markers inside the stream; anything else ends the scan."""
    n = len(data)
    i = start
    while i < n:
        if data[i] != 0xFF:
            i += 1
            continue
        if i + 1 >= n:
            raise ParseError("entropy stream ends inside a 0xFF escape")
        nxt = data[i + 1]
        if nxt == 0x00 or nxt in _RST:
            i += 2
            continue
        if nxt == 0xFF:            # fill byte, stay on the FF run
            i += 1
            continue
        return i                   # real marker
    raise ParseError("entropy stream ran to EOF without a terminating marker")


def walk(data: bytes) -> JpegStructure:
    """Parse the whole JPEG into ordered segments + trailer, accounting for
    every byte. Fails closed (ParseError) on any structural violation — an
    unaccounted region could carry metadata."""
    n = len(data)
    if n < 2 or data[0] != 0xFF or data[1] != SOI:
        raise ParseError("not a JPEG (missing SOI FFD8)")

    segments: list[Segment] = [Segment(SOI, 0, 2, b"", "soi")]
    pos = 2

    while pos < n:
        mpos = _next_marker(data, pos)
        marker = data[mpos + 1]

        if marker == EOI:
            segments.append(Segment(EOI, mpos, 2, b"", "eoi"))
            trailer_off = mpos + 2
            return JpegStructure(segments=segments,
                                 trailer=data[trailer_off:],
                                 trailer_offset=trailer_off)

        if marker in _STANDALONE:
            # A stray standalone marker at top level (RST/TEM outside a scan).
            segments.append(Segment(marker, mpos, 2, b"", _classify(marker, b"")))
            pos = mpos + 2
            continue

        # Length-bearing marker.
        if mpos + 4 > n:
            raise ParseError(f"marker {marker:#04x} at {mpos} has no length field")
        seg_len = int.from_bytes(data[mpos + 2:mpos + 4], "big")
        if seg_len < 2:
            raise ParseError(f"marker {marker:#04x} length {seg_len} < 2")
        payload_start = mpos + 4
        payload_end = mpos + 2 + seg_len
        if payload_end > n:
            raise ParseError(
                f"marker {marker:#04x} payload runs past EOF "
                f"({payload_end} > {n})")
        payload = data[payload_start:payload_end]

        if marker == SOS:
            entropy_end = _scan_entropy(data, payload_end)
            seg = Segment(SOS, mpos, entropy_end - mpos, payload,
                          _classify(SOS, payload))
            segments.append(seg)
            pos = entropy_end
            continue

        segments.append(Segment(marker, mpos, payload_end - mpos, payload,
                                _classify(marker, payload)))
        pos = payload_end

    raise ParseError("reached EOF without EOI marker")
