"""M9 — the ZIP writer, and the measured constants it is pinned to.

The writer exists because stdlib `zipfile` cannot emit `external_attr = 0` (§2.7), so
the first thing tested is exactly that, followed by every other field the census
measured. The second thing tested is that hand-rolling has not reintroduced the
failure it risks: a local header and a central directory that disagree.
"""
from __future__ import annotations

import io
import random
import shutil
import subprocess
import zipfile

import pytest

from src.scrub.errors import ParseError
from src.scrub.formats.ooxml import opc, zipread
from src.scrub.formats.ooxml import zipwrite as zw

from . import docx_corpus as C
from .e_docx_zip import matching_levels, read_archive, summarise


def _parts() -> dict[str, bytes]:
    return {
        "[Content_Types].xml": C._CONTENT_TYPES.encode(),
        "_rels/.rels": C._ROOT_RELS.encode(),
        "word/document.xml": C._document_xml(C.SOURCE_TEXT).encode(),
        "word/settings.xml": C._TORTURE_SETTINGS.encode(),
    }


@pytest.fixture
def written(tmp_path):
    data = zw.write(_parts())
    p = tmp_path / "out.docx"
    p.write_bytes(data)
    return data, str(p)


# --------------------------------------------------------------------------- #
# Other readers accept it
# --------------------------------------------------------------------------- #
def test_stdlib_zipfile_reads_every_part_back_unchanged(written):
    data, _ = written
    z = zipfile.ZipFile(io.BytesIO(data))
    assert z.testzip() is None
    for name, body in _parts().items():
        assert z.read(name) == body, name


@pytest.mark.skipif(shutil.which("unzip") is None, reason="unzip absent")
def test_an_independent_implementation_accepts_it(written):
    """`zipfile` reading our output proves little on its own — both are Python's view
    of the format. Info-ZIP is a separate implementation with its own opinions."""
    _, path = written
    r = subprocess.run(["unzip", "-t", path], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "No errors detected" in r.stdout


def test_our_own_walker_reads_it_as_a_docx(written):
    data, _ = written
    pkg = opc.parse(data)
    assert pkg.flavour == "docx"
    assert pkg.parts() == zw.order_parts(_parts())


def test_local_and_central_headers_agree_field_for_field(written):
    """The failure hand-rolling risks. The census checks both copies of every field,
    which is why it is reused here rather than trusting the writer's own view."""
    data, path = written
    for e in zipread.read(data).entries:
        assert not e.header_disagreements, f"{e.name}: {e.header_disagreements}"
    assert summarise(read_archive(path))["mismatches"] == 0


def test_parts_survive_a_round_trip_through_our_reader(written):
    data, _ = written
    arc = zipread.read(data)
    for name, body in _parts().items():
        assert arc.by_name(name).content() == body, name


# --------------------------------------------------------------------------- #
# The pinned constants (§2.7), each one measured rather than chosen
# --------------------------------------------------------------------------- #
def test_external_attr_is_zero_which_is_the_whole_reason_this_writer_exists(written):
    """Word, LibreOffice and macOS textutil all write 0. CPython overrides exactly
    that value with `0o600 << 16`, putting the operator's umask in every entry — so
    a package written through the stdlib cannot join the crowd on this field.
    """
    data, _ = written
    for e in zipread.read(data).entries:
        assert e.external_attr == 0, e.name

    # And the contrast, so the claim is demonstrated rather than asserted.
    buf = io.BytesIO()
    zi = zipfile.ZipInfo("word/document.xml", (1980, 1, 1, 0, 0, 0))
    zi.compress_type, zi.external_attr, zi.create_system = zipfile.ZIP_DEFLATED, 0, 0
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(zi, "x")
    assert zipread.read(buf.getvalue()).entries[0].external_attr == 0o600 << 16


def test_every_producer_identifying_field_is_pinned_to_the_crowd(written, tmp_path):
    data, path = written
    r = summarise(read_archive(path))
    assert r["create_system"] == "FAT/Windows(0)"    # not Unix(3) = "written on a Mac"
    assert r["version_made_by"] == "20"
    assert r["version_needed"] == "20"
    assert r["flags"] == "0x0000"                    # no data descriptor, no UTF-8 bit
    assert r["timestamps"] == "epoch-1980"           # what Word itself writes
    assert r["extra_fields"] == "none"               # no NTFS/UT nanosecond clocks
    assert r["external_attr"] == "0x00000000"
    assert r["deflate_flag_bits"] == "normal"
    assert r["eocd_comment"] == 0
    assert r["dir_entries"] == 0
    assert r["macosx_entries"] == 0
    assert r["zip64"] is False


def _separating_body() -> bytes:
    """A part on which the deflate levels actually differ.

    This needed care, and the first attempt got it wrong: repeating one settings part
    40 times is so compressible that levels 6 through 9 emit **identical bytes**, so
    `9 not in levels` failed against a writer that was doing exactly the right thing.
    The level class is a property of the data, not a flaw in the measurement — which
    is the same lesson §2.7 learned from the other direction. Varied paragraph text
    with high-entropy ids separates them; pure random bytes do not (nothing
    compresses, so every level agrees again).
    """
    rnd = random.Random(7)
    words = ["paragraph", "revision", "quarterly", "audit", "provisional",
             "figure", "section", "review"]
    return "".join(
        f'<w:p w14:paraId="{rnd.randrange(16 ** 8):08X}"><w:r><w:t>'
        + " ".join(rnd.choice(words) for _ in range(rnd.randrange(3, 12)))
        + "</w:t></w:r></w:p>"
        for _ in range(400)).encode()


def test_compression_is_level_six_and_not_level_nine():
    """Level 6 is where LibreOffice, textutil and MAT2 all sit — measured twice, by
    two techniques, not inherited as a default. Level 9 is excluded explicitly
    because it is the mistake this project already made once: PDF F2 shipped at 9,
    and the fingerprint guard failed immediately because **no** peer producer emits
    it, so 9 did not normalise our output, it labelled it.
    """
    body = _separating_body()
    data = zw.write({"[Content_Types].xml": C._CONTENT_TYPES.encode(),
                     "word/document.xml": body})
    entry = zipread.read(data).by_name("word/document.xml")
    levels = matching_levels(entry.raw, body)
    assert levels == {6}, levels


def test_entry_order_is_canonical_and_content_types_comes_first(written):
    """Sorted, so the order can never be inherited from a dict or a set — the exact
    non-determinism the in-process floor cannot see. Content types first because
    ECMA-376 Part 2 asks for it, which is also Word's convention."""
    data, _ = written
    names = zipread.read(data).names
    assert names[0] == "[Content_Types].xml"
    assert names[1:] == sorted(names[1:])
    assert zw.order_parts(["b.xml", "[Content_Types].xml", "a.xml"]) == [
        "[Content_Types].xml", "a.xml", "b.xml"]


def test_writing_the_same_parts_twice_gives_identical_bytes():
    assert zw.write(_parts()) == zw.write(_parts())


def test_part_order_in_the_input_does_not_change_the_output():
    """A caller handing us parts in a different order must not produce a different
    file, or the scrubber's own bookkeeping becomes a fingerprint."""
    parts = _parts()
    assert zw.write(parts) == zw.write(dict(reversed(list(parts.items()))))


# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #
def test_a_non_ascii_part_name_is_refused(tmp_path):
    """It would need GP bit 11, making our flag word non-constant. OPC part names are
    ASCII by spec, so this is a refusal rather than a missing feature."""
    with pytest.raises(ParseError, match="non-ASCII"):
        zw.write({"[Content_Types].xml": b"<Types/>", "word/documént.xml": b"<x/>"})


def test_a_directory_entry_is_refused():
    with pytest.raises(ParseError, match="directory entry"):
        zw.write({"[Content_Types].xml": b"<Types/>", "word/": b""})
