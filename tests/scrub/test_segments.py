"""JPEG segment-walker soundness tests (crafted streams + real files)."""
from __future__ import annotations

import pytest

from src.scrub.errors import ParseError
from src.scrub.formats.jpeg import segments as seg
from tests.scrub import corpus


def _stream(entropy: bytes, trailer: bytes = b"") -> bytes:
    """A minimal walkable (not decodable) JPEG marker stream."""
    return (
        b"\xff\xd8"                       # SOI
        b"\xff\xdb\x00\x04\x00\x00"       # DQT (len 4)
        b"\xff\xc0\x00\x05\x00\x00\x00"   # SOF0 (len 5)
        b"\xff\xda\x00\x04\x00\x00"       # SOS header (len 4)
        + entropy
        + b"\xff\xd9"                     # EOI
        + trailer
    )


def test_walk_accounts_for_every_byte():
    data = _stream(b"\x12\x34\x56", trailer=b"XY")
    s = seg.walk(data)
    # contiguous coverage: each segment starts where the previous ended
    pos = 0
    for sg in s.segments:
        assert sg.offset == pos
        pos = sg.end
    assert pos == s.trailer_offset
    assert s.trailer == b"XY"
    assert data[pos:] == b"XY"


def test_entropy_scan_passes_stuffing_and_rst():
    # FF00 (stuffed literal) and FFD0/FFD7 (restart markers) are entropy, not
    # the end of the scan; only FFD9 (EOI) terminates.
    entropy = b"\x11\xff\x00\x22\xff\xd0\x33\xff\xd7\x44"
    s = seg.walk(_stream(entropy))
    sos = s.by_kind("sos")[0]
    assert sos.marker == seg.SOS
    # the SOS segment must span all the entropy up to EOI
    eoi = s.by_kind("eoi")[0]
    assert sos.end == eoi.offset


def test_multiple_scans_progressive():
    # two SOS segments (progressive), each with its own entropy
    data = (
        b"\xff\xd8"
        b"\xff\xda\x00\x04\x00\x00" + b"\xaa\xbb"
        + b"\xff\xda\x00\x04\x00\x00" + b"\xcc\xdd"
        + b"\xff\xd9"
    )
    s = seg.walk(data)
    assert len(s.by_kind("sos")) == 2


def test_fill_bytes_before_marker():
    # extra 0xFF fill bytes before EOI must be tolerated
    data = _stream(b"\x11\x22") + b""
    data = data[:-2] + b"\xff\xff\xff\xd9"   # pad the EOI with fill bytes
    s = seg.walk(data)
    assert s.by_kind("eoi")


@pytest.mark.parametrize("bad", [
    b"\x89PNG\r\n\x1a\n",                       # not a JPEG
    b"\xff\xd8\xff\xe0\x00\xff" + b"\x00" * 4,  # APP0 length past EOF
    b"\xff\xd8\xff\xdb\x00\x04\x00\x00",        # no EOI
])
def test_malformed_fails_closed(bad):
    with pytest.raises(ParseError):
        seg.walk(bad)


def test_classifies_real_base_jpeg():
    s = seg.walk(corpus.make_base_jpeg())
    kinds = set(s.markers())
    # content markers present
    assert seg.SOI in kinds and seg.EOI in kinds and seg.SOS in kinds
    kind_names = {sg.kind for sg in s.segments}
    assert "app0_jfif" in kind_names
    assert "app1_exif" in kind_names
    assert "app2_icc" in kind_names
    assert "com" in kind_names


def test_classifies_torture_loci():
    s = seg.walk(corpus.build_torture_jpeg())
    names = {sg.kind for sg in s.segments}
    assert {"app1_xmp", "app1_xmp_extended", "app2_mpf",
            "app13_photoshop"} <= names
    assert s.trailer  # trailer bytes captured
