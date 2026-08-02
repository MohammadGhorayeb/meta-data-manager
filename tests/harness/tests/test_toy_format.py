"""Spec §9.4: round-trip pack/unpack AND read/write for the TOYF container.

Covers empty, single-record, many-record, and max-size-value boundary record
sets, plus the bad-magic ValueError path. read/write use tmp_path.
"""
from __future__ import annotations

import pytest

from tests.harness.corpus import toy_format
from tests.harness.corpus.toy_format import MAGIC

# (content, records) specimens spanning the interesting shapes.
RECORD_SETS = [
    (b"", []),                                          # empty content, no metadata
    (b"hello-content", []),                             # content, but no records
    (b"x", [(7, b"single-value")]),                     # exactly one record
    (                                                   # many records, varied types/lens
        b"the-quick-brown-fox-payload",
        [(0, b""), (1, b"a"), (7, b"A" * 16), (0xA0, b"SRC0"), (255, b"z" * 5)],
    ),
    (b"boundary", [(3, b"M" * 0xFFFF)]),                # max-size value boundary (65535)
]


@pytest.mark.parametrize("content,records", RECORD_SETS)
def test_pack_unpack_round_trip(content, records):
    blob = toy_format.pack(content, records)
    assert blob[:4] == MAGIC
    got_content, got_records = toy_format.unpack(blob)
    assert got_content == content
    assert got_records == records


@pytest.mark.parametrize("content,records", RECORD_SETS)
def test_read_write_round_trip(tmp_path, content, records):
    path = str(tmp_path / "specimen.toyf")
    toy_format.write(path, content, records)
    got_content, got_records = toy_format.read(path)
    assert got_content == content
    assert got_records == records


def test_max_size_value_is_accepted():
    # 0xFFFF is the largest length encodable in the uint16-BE length field.
    big = b"\x00" * 0xFFFF
    content, records = toy_format.unpack(toy_format.pack(b"c", [(1, big)]))
    assert records == [(1, big)]
    assert len(records[0][1]) == 0xFFFF


def test_value_too_long_raises():
    with pytest.raises(ValueError):
        toy_format.pack(b"c", [(1, b"\x00" * (0xFFFF + 1))])


def test_type_out_of_range_raises():
    with pytest.raises(ValueError):
        toy_format.pack(b"c", [(256, b"v")])


def test_bad_magic_unpack_raises():
    blob = toy_format.pack(b"content", [(7, b"vv")])
    bad = b"XXXX" + blob[4:]
    with pytest.raises(ValueError):
        toy_format.unpack(bad)


def test_bad_magic_read_raises(tmp_path):
    path = tmp_path / "bad.toyf"
    good = toy_format.pack(b"content", [(7, b"vv")])
    path.write_bytes(b"NOPE" + good[4:])
    with pytest.raises(ValueError):
        toy_format.read(str(path))
