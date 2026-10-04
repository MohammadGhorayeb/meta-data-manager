"""Phase 4 — MP4 identification, the ISO fixture, and the refusal list.

Ported from the branch that built MP4 first (its M2, `test_mp4_walker.py`). That
branch had a separate read-only walker; main's F1 does its own walking and refuses
inline, so the tests that described the walker's internal model were dropped (the
pointer-invariant tests in `test_mp4_f1.py` cover every layout they measured) and
the ones that describe BEHAVIOUR are kept here, pointed at the handler and at F1.

**Two handlers, one container, one file.** M4A has claimed brands `isom` and `mp42`
since Phase 2, and an MP4 declares exactly those. The separation is not registration
order: M4A declines any file with a `vide` track and this handler requires one.
Asserted on a spread of shapes rather than argued in a comment.

**A track kind we do not model is refused, not skipped.** Scrubbing around an
unmeasured surface turns "not modelled" into "leaks".
"""
from __future__ import annotations

import ast
import os
import shutil
import struct
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from src.scrub import dispatch  # noqa: E402
from src.scrub.errors import ParseError  # noqa: E402
from src.scrub.formats.mp4 import f1  # noqa: E402
from src.scrub.formats.mp4 import inspect as mp4_inspect  # noqa: E402
from src.scrub.formats.mp4.handler import Mp4Handler  # noqa: E402
from src.scrub.standards import isobmff as iso  # noqa: E402
from tests.scrub import mp4_iso_corpus as c  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(not c.HAVE_FFMPEG, reason="ffmpeg not installed")


# --------------------------------------------------------------------------- #
# The fixture itself
# --------------------------------------------------------------------------- #
def test_the_iso_corpus_builds_its_files_without_importing_the_code_under_test():
    """The HEIC M5 rule: a fixture that shares code with the thing it tests can
    agree with it about a misreading of the format. Checked by walking the
    module's import statements, not by grepping for a string. (`test_mp4.py` holds
    the QuickTime corpus to the same rule.)"""
    tree = ast.parse(open(os.path.join(os.path.dirname(__file__),
                                       "mp4_iso_corpus.py"), encoding="utf-8").read())
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not [m for m in imported if m.split(".")[0] in ("src", "scrub")], \
        f"mp4_iso_corpus imports from the code under test: {imported}"


@pytest.mark.skipif(shutil.which("exiftool") is None, reason="exiftool not installed")
def test_an_independent_reader_finds_the_metadata_the_fixture_claims_to_carry():
    """Otherwise every later "the tag is gone" assertion passes vacuously.

    This caught a real defect while being written: with a zeroed manufacturer
    field in the metadata `hdlr`, and with `loci`'s longitude and latitude in the
    spoken rather than the stored order, ExifTool reported **no** tags and **no**
    GPS for a file that was carrying both.
    """
    path = c.temp_mp4(c.handbuilt())
    try:
        out = subprocess.run(["exiftool", "-s", "-G1", path],
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
    """The property that makes registration order not matter, asserted over a
    spread of shapes: the interesting case is the generic-brand overlap where both
    handlers see a name they recognise."""
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
    """What is registered must match what is implemented, and F2 must still refuse
    by name."""
    handler = dispatch.default_dispatcher().resolve(c.handbuilt())
    assert handler.format_id == "mp4"
    assert handler.fidelities == ("F1",)
    with pytest.raises(Exception) as exc:
        handler.scrub(c.handbuilt(), "F2")
    assert "F2" in str(exc.value)


# --------------------------------------------------------------------------- #
# The version trap
# --------------------------------------------------------------------------- #
def test_a_64_bit_header_is_zeroed_as_a_64_bit_one():
    """The silent half of the version trap. A version-1 `mvhd` handled as version
    0 does not fail: it zeroes half of one timestamp and the top of the next field,
    which is a plausible-looking file rather than an error. So: both 64-bit times
    gone, and the timescale and duration after them untouched."""
    data = c.handbuilt(mvhd_creation=3872688647, mvhd_version=1)
    src = next(b for root in iso.parse(data) for b in root.walk() if b.type == b"mvhd")
    out = next(b for root in iso.parse(f1.scrub(data)) for b in root.walk()
               if b.type == b"mvhd")
    assert src.payload[0] == out.payload[0] == 1
    assert src.payload[4:20].strip(b"\x00"), "the fixture carries no timestamp"
    assert out.payload[4:20] == b"\x00" * 16
    assert out.payload[20:] == src.payload[20:]


def test_an_unmodelled_header_version_is_refused_not_guessed():
    data = bytearray(c.handbuilt())
    at = data.find(b"mvhd")
    data[at + 4] = 7                                  # version byte
    with pytest.raises(ParseError, match="mvhd.*version 7"):
        f1.scrub(bytes(data))


# --------------------------------------------------------------------------- #
# The refusal list
# --------------------------------------------------------------------------- #
def test_a_chunk_offset_outside_mdat_is_refused():
    """Either we misread the file or it is already broken. Patching a pointer we
    do not understand is how M4A produced a file that parsed and decoded to
    noise."""
    with pytest.raises(ParseError, match="outside mdat"):
        f1.scrub(c.handbuilt(chunk_offset_delta=1 << 20))


def test_a_fragmented_file_is_refused_by_name():
    data = c.handbuilt(extra_top=c.box(b"moof", b"\x00" * 16))
    with pytest.raises(ParseError, match="fragmented"):
        f1.scrub(data)


def test_an_unmodelled_track_handler_is_refused_and_named():
    """A handler type neither kept nor known to be metadata is declined rather than
    partly cleaned."""
    data = c.handbuilt(tracks=(
        {"track_id": 1, "handler": b"vide", "name": "VideoHandler"},
        {"track_id": 2, "handler": b"mebx", "name": "MetadataHandler"},
    ))
    with pytest.raises(ParseError, match="mebx"):
        f1.scrub(data)


@pytest.mark.parametrize("entry", [b"encv", b"enca", b"drms"])
def test_an_encrypted_sample_entry_is_refused(entry):
    """The sample entry is where a file says it is encrypted -- its `sinf` sits
    inside the entry, where a walk over boxes never reaches."""
    tracks = ({"track_id": 1, "handler": b"vide", "name": "VideoHandler"},
              {"track_id": 2, "handler": b"soun", "name": "SoundHandler",
               "fmt": b"mp4a"})
    which = 0 if entry == b"encv" else 1
    tracks[which]["fmt"] = entry
    with pytest.raises(ParseError, match="encrypted"):
        f1.scrub(c.handbuilt(tracks=tracks))


def test_two_media_boxes_are_refused_rather_than_patched_per_region():
    data = c.handbuilt(extra_top=c.box(b"mdat", b"\x00" * 16))
    with pytest.raises(ParseError, match="exactly one moov and one mdat"):
        f1.scrub(data)


def test_a_file_with_no_media_is_refused():
    stripped = c.handbuilt().replace(b"mdat", b"zzzz", 1)
    with pytest.raises(ParseError, match="exactly one moov and one mdat"):
        f1.scrub(stripped)


def test_a_truncated_chunk_table_is_refused_rather_than_read_short():
    """A table declaring more entries than it holds must fail, not return the
    entries that happen to fit."""
    data = bytearray(c.handbuilt())
    at = data.find(b"stco")
    struct.pack_into(">I", data, at + 8, 9999)        # entry_count
    with pytest.raises(ParseError, match="fewer entries than it declares"):
        f1.scrub(bytes(data))


def test_a_non_mp4_is_refused_before_anything_else():
    with pytest.raises(ParseError):
        f1.scrub(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)


# --------------------------------------------------------------------------- #
# Files a real muxer wrote
# --------------------------------------------------------------------------- #
@needs_ffmpeg
def test_every_ffmpeg_producer_is_claimed_and_scrubbed(tmp_path):
    for name, path in c.ffmpeg_corpus(str(tmp_path)).items():
        data = open(path, "rb").read()
        assert Mp4Handler().claims(data), f"{name} not claimed"
        assert f1.residuals(f1.scrub(data)) == [], name


@needs_ffmpeg
def test_a_real_tagged_file_reports_both_metadata_containers(tmp_path):
    """M0's duplicate-locus finding, as a test: one `-metadata location` writes
    the coordinate to `udta/loci` as well as the tag list, so a report that knew
    only about `ilst` would say nothing of the GPS F1 removed."""
    path = c.ffmpeg_sample(str(tmp_path / "tagged.mp4"), gps=True)
    described = mp4_inspect.describe(open(path, "rb").read())
    assert "udta:loci" in described, f"the GPS box is not reported: {described}"
