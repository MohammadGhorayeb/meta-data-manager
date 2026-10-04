"""The shared ISOBMFF walker reads `meta` in both dialects (P4 M8).

ISO 14496-12 makes `meta` a full box: 4 bytes of version/flags, then children.
QuickTime makes `moov/meta` and `trak/meta` plain containers. Before M8 the walker
knew only the ISO form and failed on every iPhone video ("declares size 1751411826",
which is ASCII `hdlr` read as a size). The boxes here are built by hand, byte by byte,
because the real videos that exposed it are personal files that never enter the repo.
"""
from __future__ import annotations

import struct

import pytest

from src.scrub.errors import ParseError
from src.scrub.standards import isobmff as iso


def _box(btype: bytes, body: bytes) -> bytes:
    return struct.pack(">I", 8 + len(body)) + btype + body


def _hdlr(handler: bytes) -> bytes:
    # version/flags, pre_defined, handler_type, 3x reserved, empty name
    return _box(b"hdlr", b"\x00" * 4 + b"\x00" * 4 + handler + b"\x00" * 12 + b"\x00")


def _keys(*names: bytes) -> bytes:
    body = b"\x00" * 4 + struct.pack(">I", len(names))
    for n in names:
        body += struct.pack(">I", 8 + len(n)) + b"mdta" + n
    return _box(b"keys", body)


def _ilst_entry(index: int, value: bytes) -> bytes:
    data = _box(b"data", struct.pack(">II", 1, 0) + value)   # UTF-8, default locale
    return struct.pack(">I", 8 + len(data)) + struct.pack(">I", index) + data


QT_META = _box(b"meta", _hdlr(b"mdta")
               + _keys(b"com.apple.quicktime.model")
               + _box(b"ilst", _ilst_entry(1, b"PLANTED-MODEL")))

ISO_META = _box(b"meta", b"\x00\x00\x00\x00" + _hdlr(b"mdir")
                + _box(b"ilst", _box(b"\xa9too", _box(b"data", b"\x00" * 8 + b"x"))))


def test_quicktime_meta_parses_as_a_plain_container():
    moov = _box(b"moov", QT_META)
    meta = iso.parse(moov)[0].find(b"meta")
    assert meta is not None and meta.quicktime_meta
    assert [c.type for c in meta.children] == [b"hdlr", b"keys", b"ilst"]
    assert meta.children[0].payload[8:12] == b"mdta"


def test_iso_meta_still_parses_as_a_full_box():
    """M4A and HEIC depend on this reading; the dialect check must not move it."""
    moov = _box(b"moov", _box(b"udta", ISO_META))
    meta = iso.parse(moov)[0].find(b"udta/meta")
    assert meta is not None and not meta.quicktime_meta
    assert meta.payload == b"\x00\x00\x00\x00"
    assert [c.type for c in meta.children] == [b"hdlr", b"ilst"]


@pytest.mark.parametrize("raw", [
    _box(b"moov", QT_META),
    _box(b"moov", _box(b"udta", ISO_META)),
    # Both dialects in one file: the choice is per box, not per brand or per file.
    _box(b"moov", QT_META + _box(b"trak", QT_META) + _box(b"udta", ISO_META)),
], ids=["quicktime", "iso", "mixed"])
def test_each_dialect_is_written_back_as_read(raw):
    assert iso.serialize(iso.parse(raw)) == raw


def test_a_trak_level_quicktime_meta_is_reached_too():
    """The lens model sits in `trak/meta` on an iPhone, not in `moov/meta`."""
    moov = _box(b"moov", _box(b"trak", QT_META))
    meta = iso.parse(moov)[0].find(b"trak/meta")
    assert meta is not None and meta.quicktime_meta
    assert b"PLANTED-MODEL" in iso.serialize([meta.find(b"ilst")])


def test_a_meta_that_is_neither_dialect_fails_closed():
    """Non-zero first word but no four-character type after it: not QuickTime, and
    read as ISO it overruns. It must raise, never be guessed into shape."""
    bogus = _box(b"meta", struct.pack(">I", 0x7FFFFFFF) + b"\x00\x01\x02\x03" + b"\x00" * 8)
    with pytest.raises(ParseError):
        iso.parse(_box(b"moov", bogus))
