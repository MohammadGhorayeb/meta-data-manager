"""Tests for the W8/M6 ZIP census — and for the measurements the writer decision rests on.

Three kinds of test live here:

1. The census parses what real producers write, without `zipfile`'s help.
2. The census *detects* the failure mode W11 exists to avoid — a local header and a
   central directory record that disagree.
3. The stdlib traps that decided W8 are asserted as facts, so that if CPython ever
   changes one, this suite says so instead of the writer quietly drifting.
"""
from __future__ import annotations

import struct
import zipfile
import zlib

import pytest

from . import docx_corpus as C
from .e_docx_zip import (
    level_class,
    matching_levels,
    read_archive,
    summarise,
)


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    return C.producers(str(tmp_path_factory.mktemp("docxzip")))


def test_every_producer_parses_and_agrees_with_itself(corpus):
    """A ZIP stores each entry's header twice. Any producer whose two copies disagree
    is writing a subtly corrupt file -- and if one ever does, that is a finding about
    the producer, not a reason for the census to raise."""
    for name, path in corpus.items():
        arc = read_archive(path)
        assert arc.entries, f"{name}: no entries parsed"
        for e in arc.entries:
            assert not e.mismatches, f"{name}/{e.name}: {e.mismatches}"


def test_docx_shape_is_what_the_census_reads(corpus):
    """Every producer must have written the two parts that make a package a DOCX --
    which is also what W10's `claims()` will key on, since `PK\\x03\\x04` is the magic
    of every ZIP ever made."""
    for name, path in corpus.items():
        names = summarise(read_archive(path))["names"]
        assert "[Content_Types].xml" in names, name
        assert any(n == "word/document.xml" for n in names), name


def test_level_recovery_finds_the_level_it_was_written_at(tmp_path):
    """The recovered class must CONTAIN the true level. It is a class rather than a
    number because small inputs compress identically at several levels."""
    plain = (C._CONTENT_TYPES * 4).encode()
    for lvl in (1, 6, 9):
        co = zlib.compressobj(lvl, zlib.DEFLATED, -15, 8)
        stored = co.compress(plain) + co.flush()
        assert lvl in matching_levels(stored, plain), lvl


def test_non_zlib_deflate_is_reported_as_such():
    """A valid deflate stream that no zlib setting reproduces must come back
    `not-zlib`, not silently matched to the nearest level. This is the whole basis of
    the Word finding."""
    plain = b"metadata " * 400
    # A stored-block deflate stream: valid, decompresses correctly, and produced by
    # no zlib level.
    body = b"\x01" + struct.pack("<HH", len(plain), 0xFFFF ^ len(plain)) + plain
    assert zlib.decompress(body, -15) == plain
    assert matching_levels(body, plain) == set()


def test_census_catches_a_local_central_disagreement(tmp_path):
    """The classic subtly-corrupt rewrite. Patch one copy of the timestamp and the
    census must name the field rather than parse happily past it."""
    p = str(tmp_path / "drift.docx")
    C.synthetic(p)
    raw = bytearray(open(p, "rb").read())
    loc = raw.find(b"PK\x03\x04")
    struct.pack_into("<H", raw, loc + 10, 0x1234)      # local header mod-time only
    open(p, "wb").write(raw)

    arc = read_archive(p)
    assert any("time cen=" in m for e in arc.entries for m in e.mismatches)


# --------------------------------------------------------------------------- #
# The stdlib facts the W8 decision rests on
# --------------------------------------------------------------------------- #
def test_zipfile_cannot_write_external_attr_zero(tmp_path):
    """**The W8 decision, as a test.**

    Word, LibreOffice and macOS `textutil` all write `external_attr = 0`. Stdlib
    `zipfile` overrides exactly that value:

        if not zinfo.external_attr:
            zinfo.external_attr = 0o600 << 16

    so the one value that joins the crowd is the one value it refuses to keep, and a
    package written with it carries the operator's umask instead. Everything else --
    `create_system`, `create_version`, the timestamp, the level, the flags -- pins
    fine; this single field is why W11 writes the container itself.

    If CPython ever drops that override, this test fails and the decision should be
    revisited rather than inherited.
    """
    zi = zipfile.ZipInfo("word/document.xml", (1980, 1, 1, 0, 0, 0))
    zi.compress_type = zipfile.ZIP_DEFLATED
    zi.create_system = 0
    zi.external_attr = 0
    p = str(tmp_path / "pinned.zip")
    with zipfile.ZipFile(p, "w") as z:
        z.writestr(zi, "hello", compresslevel=6)

    raw = open(p, "rb").read()
    cen = raw.find(b"PK\x01\x02")
    assert struct.unpack_from("<I", raw, cen + 38)[0] == 0o600 << 16


def test_zipinfo_defaults_to_stored_and_ignores_the_archive_compression(tmp_path):
    """The second stdlib trap, caught by the census on its first run: a bare
    `ZipInfo` carries `compress_type=ZIP_STORED` and **silently overrides** the
    `ZipFile(..., ZIP_DEFLATED)` argument. Three uncompressed parts went out while
    the writer had asked for deflate."""
    p = str(tmp_path / "stored.zip")
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo("word/document.xml", (1980, 1, 1, 0, 0, 0)),
                   "x" * 5000)
    assert read_archive(p).entries[0].method_cen == 0        # stored, not deflated


def test_zipfile_stamps_the_host_os_into_every_entry(tmp_path):
    """`ZipInfo()` defaults `create_system` from the platform, so an unpinned
    package says which OS scrubbed it -- in every entry, in a field nobody reads."""
    assert zipfile.ZipInfo("a.xml").create_system in (0, 3)
    import sys
    expected = 0 if sys.platform == "win32" else 3
    assert zipfile.ZipInfo("a.xml").create_system == expected


# --------------------------------------------------------------------------- #
# The published table
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not C.HAVE_WORD,
                    reason="Word samples absent (see tests/corpus/docx/README.md)")
def test_word_deflate_is_not_zlib(corpus):
    """Published in §2.7: no zlib level reproduces Word's bytes at any entry, so its
    compressed output identifies the *implementation*, and 'match Word' is not an
    option available to us."""
    assert level_class(read_archive(corpus["msword"])) == "not-zlib"


@pytest.mark.skipif(not C.HAVE_SOFFICE, reason="soffice absent")
def test_libreoffice_pins_to_level_six(corpus):
    """The other half of the published claim: level 6 is the crowd, measured, not
    picked -- the same answer PDF F2 arrived at from the zlib header byte."""
    assert level_class(read_archive(corpus["libreoffice"])) == "6"
