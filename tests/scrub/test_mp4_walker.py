"""Phase 4 M2 — the MP4 walker, `claims()`, and the refusal list.

No scrubbing happens here. M2 is identification and accounting, and it lands
before F1 for the reason DOCX's M8 did: a handler registered before it can scrub
is the tool advertising a format it will then fail on.

What these tests are actually guarding:

**Two handlers, one container, one file.** M4A has claimed brands `isom` and
`mp42` since Phase 2, and an MP4 declares exactly those. The separation is not
registration order — it is that M4A claims `soun` and refuses `vide`, while this
claims `vide`. Those predicates cannot both hold, and that is asserted on every
corpus file rather than argued in a comment.

**A track kind we do not model is refused, not skipped.** Phase 4 M1 put real
phone video out of scope, so an iPhone `.MOV`'s `mebx` track is a surface nobody
measured. Scrubbing around it would turn "out of scope" into "leaks".

**The offset question has two answers and the walker must give the right one per
file.** Measured in M0: `moov` after `mdat` means removing metadata moves nothing;
an 8-byte `free` before `mdat` means it moves everything.
"""
from __future__ import annotations

import ast
import os
import struct
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from src.scrub import dispatch  # noqa: E402
from src.scrub.errors import ParseError  # noqa: E402
from src.scrub.formats.mp4 import walker as w  # noqa: E402
from src.scrub.formats.mp4.handler import Mp4Handler  # noqa: E402
from tests.scrub import mp4_corpus as c  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(not c.HAVE_FFMPEG, reason="ffmpeg not installed")


# --------------------------------------------------------------------------- #
# The fixture itself
# --------------------------------------------------------------------------- #
def test_the_corpus_builds_its_files_without_importing_the_code_under_test():
    """The HEIC M5 rule: a fixture that shares code with the thing it tests can
    agree with it about a misreading of the format. Checked by walking the
    module's import statements, not by grepping for a string."""
    tree = ast.parse(open(os.path.join(os.path.dirname(__file__),
                                       "mp4_corpus.py"), encoding="utf-8").read())
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not [m for m in imported if m.split(".")[0] in ("src", "scrub")], \
        f"mp4_corpus imports from the code under test: {imported}"


def test_an_independent_reader_finds_the_metadata_the_fixture_claims_to_carry():
    """Otherwise every later "the tag is gone" assertion passes vacuously.

    This caught a real defect while being written: with a zeroed manufacturer
    field in the metadata `hdlr`, and with `loci`'s longitude and latitude in the
    spoken rather than the stored order, ExifTool reported **no** tags and **no**
    GPS for a file that was carrying both. A removal test against that fixture
    would have been green from the first day and meaningless — the DOCX
    `w:rsid`-in-compressed-bytes mistake in a different container.
    """
    exiftool = pytest.importorskip("shutil").which("exiftool")
    if not exiftool:
        pytest.skip("exiftool not installed")
    import subprocess
    path = c.temp_mp4(c.handbuilt())
    try:
        out = subprocess.run([exiftool, "-s", "-G1", path],
                             capture_output=True, text=True).stdout
    finally:
        os.unlink(path)
    assert "Lavf62.12.101" in out, "the fixture's encoder tag is not readable"
    assert "Ground truth" in out, "the fixture's title tag is not readable"
    assert "GPSLatitude" in out, "the fixture's loci GPS is not readable"


# --------------------------------------------------------------------------- #
# Identification
# --------------------------------------------------------------------------- #
def test_a_hand_built_mp4_is_claimed():
    assert Mp4Handler().claims(c.handbuilt())


def test_matches_is_only_the_cheap_gate():
    """`ftyp` at offset 4 is shared by M4A, HEIC and MP4 alike, so `matches()`
    saying yes must never be read as identification."""
    h = Mp4Handler()
    assert h.matches(c.handbuilt()[:16])
    assert not h.matches(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)


def test_an_audio_only_file_is_not_claimed_as_video():
    """The generic brands are shared, so the track table has to decide."""
    audio_only = c.handbuilt(tracks=(
        {"track_id": 1, "handler": b"soun", "name": "SoundHandler",
         "fmt": b"mp4a"},))
    assert not Mp4Handler().claims(audio_only)


def test_mp4_and_m4a_handlers_can_never_both_claim_one_file():
    """The property that makes registration order not matter.

    M4A refuses anything with a `vide` handler; this claims only files that have
    one. Asserted over a spread of shapes rather than on one file, because the
    interesting case is the generic-brand overlap where both handlers see a name
    they recognise.
    """
    from src.scrub.formats.m4a.handler import M4aHandler
    mp4, m4a = Mp4Handler(), M4aHandler()
    shapes = {
        "video+audio": c.handbuilt(),
        "video only": c.handbuilt(tracks=(
            {"track_id": 1, "handler": b"vide", "name": "VideoHandler"},)),
        "audio only": c.handbuilt(tracks=(
            {"track_id": 1, "handler": b"soun", "name": "SoundHandler",
             "fmt": b"mp4a"},)),
        "mp42 brand": c.handbuilt(major=b"mp42"),
        "faststart": c.handbuilt(moov_first=True),
    }
    for label, data in shapes.items():
        claimed = [n for n, h in (("mp4", mp4), ("m4a", m4a)) if h.claims(data)]
        # Exactly one, not merely "not both" -- two handlers that each declined
        # everything would satisfy mutual exclusion while supporting no files.
        assert len(claimed) == 1, f"{label} claimed by {claimed or 'nobody'}"
    assert m4a.claims(shapes["audio only"]), "the audio case is doing no work"
    assert mp4.claims(shapes["video+audio"])


def test_a_heic_brand_is_not_claimed():
    assert not Mp4Handler().claims(c.handbuilt(major=b"heic",
                                               brands=(b"heic", b"mif1")))


def test_an_unknown_brand_declines_rather_than_guessing():
    assert not Mp4Handler().claims(c.handbuilt(major=b"zzzz", brands=(b"zzzz",)))


def test_identification_never_raises_on_rubbish():
    """`claims()` runs on every file the tool is handed, so it must be total."""
    for junk in (b"", b"\x00" * 4, b"\x00\x00\x00\x18ftyp",
                 b"\x00\x00\x00\x18ftypisom" + b"\xff" * 64,
                 c.handbuilt()[:40]):
        assert Mp4Handler().claims(junk) is False


def test_the_handler_is_registered_only_for_what_it_can_actually_do():
    """M2 asserted this handler was NOT in dispatch, because F1 did not exist yet
    and a registered handler is the tool advertising a format. M3 landed F1, so
    the assertion inverts — but the underlying rule does not: what is registered
    must match what is implemented, and F2 must still refuse by name."""
    handler = dispatch.default_dispatcher().resolve(c.handbuilt())
    assert handler.format_id == "mp4"
    assert handler.fidelities == ("F1",)
    with pytest.raises(Exception) as exc:
        handler.scrub(c.handbuilt(), "F2")
    assert "F2" in str(exc.value)


# --------------------------------------------------------------------------- #
# The track model
# --------------------------------------------------------------------------- #
def test_the_walker_reads_the_track_table():
    layout = w.walk(c.handbuilt())
    assert [t.track_id for t in layout.tracks] == [1, 2]
    assert [t.handler for t in layout.tracks] == [b"vide", b"soun"]
    assert [t.handler_name for t in layout.tracks] == ["VideoHandler",
                                                       "SoundHandler"]
    assert layout.has_video


def test_the_handler_name_is_read_because_it_is_a_producer_fingerprint():
    """M0's finding: AVFoundation writes `Core Media Video` into a field no player
    needs, and `exiftool -all=` leaves it there. A walker that did not read it
    could not report it, and F1 could not remove it."""
    data = c.handbuilt(tracks=(
        {"track_id": 1, "handler": b"vide", "name": "Core Media Video"},))
    assert w.walk(data).tracks[0].handler_name == "Core Media Video"


def test_the_movie_and_track_timestamps_are_read():
    data = c.handbuilt(mvhd_creation=3872688647, tracks=(
        {"track_id": 1, "handler": b"vide", "name": "VideoHandler",
         "creation": 3872688647, "modification": 3872688647},))
    layout = w.walk(data)
    assert layout.mvhd_creation == 3872688647
    assert layout.tracks[0].creation_time == 3872688647


def test_a_64_bit_header_is_not_read_as_a_32_bit_one():
    """The silent half of the version trap. A version-1 `mvhd` read as version 0
    does not fail — it returns the top 32 bits of a 64-bit timestamp, which is a
    plausible-looking wrong number rather than an error."""
    data = c.handbuilt(mvhd_creation=3872688647, mvhd_version=1)
    assert w.walk(data).mvhd_creation == 3872688647


def test_an_unmodelled_header_version_is_refused_not_guessed():
    data = bytearray(c.handbuilt())
    at = data.find(b"mvhd")
    data[at + 4] = 7                                  # version byte
    with pytest.raises(ParseError, match="mvhd version 7"):
        w.walk(bytes(data))


def test_both_chunk_offset_table_kinds_are_read():
    """`co64` is legal and `shift_chunk_offsets()` has always handled it, but no
    file in this suite ever carried one — the M4A test only asserted that *some*
    table was patched. Now one does."""
    for table, kind in (("stco", b"stco"), ("co64", b"co64")):
        layout = w.walk(c.handbuilt(table=table))
        assert {t.offset_table_kind for t in layout.tracks} == {kind}
        for track in layout.tracks:
            assert track.chunk_offsets, "no offsets parsed"


# --------------------------------------------------------------------------- #
# The offset question
# --------------------------------------------------------------------------- #
def test_removing_metadata_moves_nothing_when_moov_follows_mdat():
    """Four of six M0 producers are this shape."""
    layout = w.walk(c.handbuilt(free_before_mdat=False))
    assert not layout.moov_before_mdat
    assert layout.removable_before_mdat() == []
    assert layout.bytes_removed_before_mdat() == 0


def test_the_free_box_every_ffmpeg_file_carries_is_what_moves_mdat():
    """The trap M0 narrowed the plan's claim to. `moov` is still after `mdat`, so
    a reader who stopped at "offsets are safe" would be wrong by 8 bytes — which
    is enough to decode the media as noise."""
    layout = w.walk(c.handbuilt(free_before_mdat=True))
    assert not layout.moov_before_mdat
    assert [b.type for b in layout.removable_before_mdat()] == [b"free"]
    assert layout.bytes_removed_before_mdat() == 8


def test_the_faststart_layout_puts_everything_ahead_of_the_media():
    layout = w.walk(c.handbuilt(moov_first=True))
    assert layout.moov_before_mdat


def test_every_chunk_offset_lands_inside_the_media_box():
    layout = w.walk(c.handbuilt())
    for track in layout.tracks:
        for offset in track.chunk_offsets:
            assert layout.mdat_offset <= offset < layout.mdat_end


# --------------------------------------------------------------------------- #
# The refusal list
# --------------------------------------------------------------------------- #
def test_a_chunk_offset_outside_mdat_is_refused():
    """Either we misread the file or it is already broken. Patching a pointer we
    do not understand is how M4A produced a file that parsed and decoded to
    noise."""
    with pytest.raises(ParseError, match="outside mdat"):
        w.walk(c.handbuilt(chunk_offset_delta=1 << 20))


def test_a_fragmented_file_is_refused_by_name():
    data = c.handbuilt(extra_top=c.box(b"moof", b"\x00" * 16))
    with pytest.raises(ParseError, match="fragmented"):
        w.walk(data)


def test_an_unmodelled_track_handler_is_refused_and_named():
    """The M1 scope decision, enforced. `mebx` is what an iPhone writes; we have
    never measured one, so a file carrying it is declined rather than partly
    cleaned."""
    data = c.handbuilt(tracks=(
        {"track_id": 1, "handler": b"vide", "name": "VideoHandler"},
        {"track_id": 2, "handler": b"mebx", "name": "MetadataHandler"},
    ))
    with pytest.raises(ParseError, match="mebx"):
        w.walk(data)


@pytest.mark.parametrize("kind", [b"sinf", b"pssh", b"schm", b"frma", b"senc"])
def test_protected_media_is_refused(kind):
    data = c.handbuilt(extra_top=c.box(kind, b"\x00" * 8))
    with pytest.raises(ParseError, match="protected|encrypted"):
        w.walk(data)


def test_an_encrypted_sample_entry_is_refused_even_with_no_protection_box():
    """`encv` in the sample description is the other way a file says it is
    encrypted, and a top-level box scan alone would walk straight past it."""
    data = c.handbuilt(tracks=(
        {"track_id": 1, "handler": b"vide", "name": "VideoHandler",
         "fmt": b"encv"},))
    with pytest.raises(ParseError, match="encrypted sample entry"):
        w.walk(data)


def test_two_media_boxes_are_refused_rather_than_patched_per_region():
    data = c.handbuilt(extra_top=c.box(b"mdat", b"\x00" * 16))
    with pytest.raises(ParseError, match="mdat boxes"):
        w.walk(data)


def test_a_file_with_no_media_is_refused():
    data = c.handbuilt()
    stripped = data.replace(b"mdat", b"zzzz", 1)
    with pytest.raises(ParseError, match="no mdat box"):
        w.walk(stripped)


def test_a_truncated_chunk_table_is_refused_rather_than_read_short():
    """A table declaring more entries than it holds must fail, not return the
    entries that happen to fit."""
    data = bytearray(c.handbuilt())
    at = data.find(b"stco")
    struct.pack_into(">I", data, at + 8, 9999)        # entry_count
    with pytest.raises(ParseError, match="stco claims 9999"):
        w.walk(bytes(data))


def test_walking_a_non_mp4_is_refused_before_anything_else():
    with pytest.raises(ParseError, match="not an MP4"):
        w.walk(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)


# --------------------------------------------------------------------------- #
# Files a real muxer wrote
# --------------------------------------------------------------------------- #
@needs_ffmpeg
def test_real_ffmpeg_files_walk_and_reproduce_the_m0_measurements(tmp_path):
    corpus = c.ffmpeg_corpus(str(tmp_path))
    for name, path in corpus.items():
        data = open(path, "rb").read()
        assert Mp4Handler().claims(data), f"{name} not claimed"
        layout = w.walk(data)
        assert layout.has_video
        # M0: ffmpeg writes zeroed movie timestamps and a generic handler name.
        assert layout.mvhd_creation == 0, f"{name} carries a wall-clock timestamp"
        assert any(t.handler_name in ("VideoHandler", "SoundHandler")
                   for t in layout.tracks)
        # M0: every ffmpeg file carries a `free` box before `mdat`.
        assert layout.bytes_removed_before_mdat() > 0, \
            f"{name} has no removable padding before mdat -- M0 said it would"
    assert w.walk(open(corpus["faststart"], "rb").read()).moov_before_mdat
    assert not w.walk(open(corpus["plain"], "rb").read()).moov_before_mdat


@needs_ffmpeg
def test_a_real_tagged_file_exposes_both_metadata_containers(tmp_path):
    """M0's duplicate-locus finding, as a test: one `-metadata location` writes
    the coordinate to `udta/loci` as well as the tag list, so a handler that knew
    only about `ilst` would leave the GPS in the file."""
    path = c.ffmpeg_sample(str(tmp_path / "tagged.mp4"), gps=True)
    layout = w.walk(open(path, "rb").read())
    kinds = {b.type for b in layout.metadata_boxes()}
    assert b"udta" in kinds
    assert b"loci" in kinds, "the GPS box M0 measured is not being reported"
