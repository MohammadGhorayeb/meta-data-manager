"""Phase 4 M3 — MP4 F1: the strip, and the bug it found in a shipped format.

The acceptance test here is **decode, not parse**, for the third time in this
project and the first time it caught something. M4A taught the lesson, HEIC
repeated it, and this milestone is where it paid: building MP4 F1 surfaced a
defect that had been in shipped M4A F1 since Phase 2.

The defect, because the tests below are shaped around it. AVFoundation writes
`mdat` with the 64-bit *largesize* header — `size == 1`, then a 64-bit length —
for a box small enough to fit in 32 bits, which the format permits. Our
`mdat_payload_offset()` **reconstructed** that header as 8 bytes rather than
reading the 16 the file actually used. So the predicted "before" and "after"
agreed, the delta came out zero, no chunk offset was patched, and the output kept
byte-identical media while every pointer into it was 8 bytes wrong. It parsed. It
reported the right duration. It decoded to noise. Every check except decoding
called that a success.

Two things now stand in the way of it coming back: the input offset is read from
the parsed box rather than reconstructed, and `strip_and_repack` follows every
chunk offset from input to output and compares the bytes it lands on.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from src.scrub import dispatch  # noqa: E402
from src.scrub.errors import ParseError, ScrubError  # noqa: E402
from src.scrub.formats.mp4 import f1  # noqa: E402
from src.scrub.formats.mp4 import inspect as mp4_inspect  # noqa: E402
from src.scrub.formats.mp4 import walker as w  # noqa: E402
from src.scrub.standards import isobmff as iso  # noqa: E402
from tests.scrub import mp4_corpus as c  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(not c.HAVE_FFMPEG, reason="ffmpeg not installed")
HAVE_EXIFTOOL = shutil.which("exiftool") is not None


def _decode(path: str, out: str) -> bytes:
    """Decode to raw video. Returns the frames; empty means it did not decode."""
    subprocess.run(["ffmpeg", "-v", "error", "-i", path,
                    "-f", "rawvideo", "-pix_fmt", "yuv420p", out, "-y"],
                   capture_output=True)
    return open(out, "rb").read() if os.path.exists(out) else b""


# --------------------------------------------------------------------------- #
# The regression that started all of this
# --------------------------------------------------------------------------- #
def test_a_largesize_mdat_has_its_chunk_offsets_patched():
    """The bug, pinned without needing ffmpeg or a Mac.

    A 64-bit `mdat` header is 16 bytes; reconstructing it as 8 makes the measured
    move zero when the real move is -8. The assertion is on the OFFSETS, not on
    the media bytes, because the media bytes were always fine — that is exactly
    what made this invisible.
    """
    data = c.handbuilt(largesize_mdat=True, free_before_mdat=True)
    before = iso.chunk_offset_tables(iso.parse(data))
    out = f1.scrub(data)
    after = iso.chunk_offset_tables(iso.parse(out))

    src_mdat = next(b for b in iso.parse(data) if b.type == b"mdat")
    out_mdat = next(b for b in iso.parse(out) if b.type == b"mdat")
    assert src_mdat.header_len == 16, "the fixture is not using the largesize form"
    assert out_mdat.header_len == 8, "our serializer should emit the compact form"

    moved = (out_mdat.offset + out_mdat.header_len) - (src_mdat.offset
                                                       + src_mdat.header_len)
    assert moved != 0, "this fixture is supposed to move the media"
    for old_table, new_table in zip(before, after, strict=True):
        for old, new in zip(old_table, new_table, strict=True):
            assert new == old + moved, (
                f"chunk offset {old} became {new}, expected {old + moved}")


def test_every_chunk_offset_still_points_at_the_same_bytes():
    """The invariant that makes the above generic rather than one case.

    Preserving the media is not the promise; preserving the *pointers into it* is.
    """
    for kwargs in ({}, {"largesize_mdat": True}, {"moov_first": True},
                   {"free_before_mdat": False}, {"table": "co64"},
                   {"moov_first": True, "largesize_mdat": True}):
        data = c.handbuilt(**kwargs)
        out = f1.scrub(data)
        before = iso.chunk_offset_tables(iso.parse(data))
        after = iso.chunk_offset_tables(iso.parse(out))
        assert before and after
        for old_table, new_table in zip(before, after, strict=True):
            for old, new in zip(old_table, new_table, strict=True):
                assert data[old:old + 64] == out[new:new + 64], (
                    f"{kwargs}: offset {old} -> {new} lands on different bytes")


def test_the_guard_refuses_rather_than_emitting_a_file_that_decodes_to_noise():
    """Fail closed. A corrupt output that reports success is the worst outcome
    available, so the engine must raise rather than return one."""
    data = c.handbuilt()
    original = iso.shift_chunk_offsets

    def sabotage(boxes, delta):
        return original(boxes, 0) or 1        # patch nothing, claim success

    iso.shift_chunk_offsets = sabotage
    try:
        with pytest.raises(ScrubError, match="decode to noise|point at the same"):
            f1.scrub(c.handbuilt(free_before_mdat=True, moov_first=True))
    finally:
        iso.shift_chunk_offsets = original
    assert f1.scrub(data), "the engine should work again once un-sabotaged"


# --------------------------------------------------------------------------- #
# What F1 removes
# --------------------------------------------------------------------------- #
def test_the_same_coordinate_goes_from_every_box_it_was_written_to():
    """M0's duplicate-locus finding: one location request writes `udta/loci` AND
    the tag list. Removing only `ilst` would leave the GPS in the file."""
    data = c.handbuilt(gps=True, tags=True)
    assert b"loci" in data
    out = f1.scrub(data)
    assert b"loci" not in out
    assert b"udta" not in out
    assert b"ilst" not in out
    assert b"\xa9too" not in out


def test_the_handler_name_goes_but_the_handler_type_stays():
    """The M0 finding, and the half of it that must NOT change: the type is what
    tells a reader the track is video or sound."""
    data = c.handbuilt(tracks=(
        {"track_id": 1, "handler": b"vide", "name": "Core Media Video"},
        {"track_id": 2, "handler": b"soun", "name": "Core Media Audio",
         "fmt": b"mp4a"},
    ))
    out = f1.scrub(data)
    assert b"Core Media" not in out
    layout = w.walk(out)
    assert [t.handler for t in layout.tracks] == [b"vide", b"soun"]
    assert all(t.handler_name == "" for t in layout.tracks)


def test_all_three_timestamp_boxes_are_zeroed_not_just_the_two_obvious_ones():
    """`mdhd` is the one a two-box implementation misses, and ExifTool reports it
    as MediaCreateDate — so the file still says when it was made."""
    data = c.handbuilt(mvhd_creation=3872688647, tracks=(
        {"track_id": 1, "handler": b"vide", "name": "VideoHandler",
         "creation": 3872688647, "modification": 3872688647},))
    out = f1.scrub(data)
    seen = set()
    for root in iso.parse(out):
        for box in root.walk():
            if box.type in iso.TIMESTAMP_BOXES:
                seen.add(box.type)
                width = 8 if box.payload[0] == 1 else 4
                assert not box.payload[4:4 + width * 2].strip(b"\x00"), \
                    f"{box.type!r} still carries a timestamp"
    assert seen == {b"mvhd", b"tkhd", b"mdhd"}, f"only checked {seen}"


def test_the_padding_before_the_media_goes():
    data = c.handbuilt(free_before_mdat=True)
    out = f1.scrub(data)
    assert not [b for b in iso.parse(out) if b.type in (b"free", b"skip")]


def test_the_media_bytes_are_untouched():
    for kwargs in ({}, {"largesize_mdat": True}, {"moov_first": True}):
        data = c.handbuilt(**kwargs)
        src = next(b for b in iso.parse(data) if b.type == b"mdat")
        out = next(b for b in iso.parse(f1.scrub(data)) if b.type == b"mdat")
        assert out.payload == src.payload, kwargs
        assert c.SENTINEL in out.payload


def test_f1_is_deterministic():
    data = c.handbuilt()
    assert f1.scrub(data) == f1.scrub(data)


def test_scrubbing_twice_changes_nothing_more():
    data = c.handbuilt()
    once = f1.scrub(data)
    assert f1.scrub(once) == once


# --------------------------------------------------------------------------- #
# Residuals and reporting
# --------------------------------------------------------------------------- #
def test_residuals_are_clean_on_our_own_output():
    assert f1.residuals(f1.scrub(c.handbuilt())) == []


def test_residuals_can_actually_see_a_leak():
    """A residual check that never fires is decoration. Fed an unscrubbed file it
    must name what is there."""
    found = " ".join(f1.residuals(c.handbuilt()))
    assert "udta" in found
    assert "loci" in found or "location" in found
    assert "hdlr name" in found


def test_the_report_names_every_locus_it_removed():
    data = c.handbuilt(gps=True, tags=True)
    described = mp4_inspect.describe(data)
    assert "udta:loci" in described
    assert any(k.endswith("Encoder") or "encoder" in k for k in described)
    assert any(k.startswith("hdlr:") for k in described)
    assert described["udta:loci"].startswith("40.")


def test_the_report_reads_keys_namespace_tags_by_name():
    """A Keys `ilst` entry's type is an index into `keys`, so a reader matching
    fourcc names reports nothing at all for it — GPS included."""
    boxes = iso.parse(c.keys_meta())
    described = mp4_inspect._keys_and_ilst(boxes[0])
    assert described.get("keys:location") == "+40.7128-074.0060/"
    assert described.get("keys:make") == "TestCorp"
    # And the whole point: matching on fourcc names would have found neither.
    assert not any(k in ("Title", "Encoder", "GPS position") for k in described)


# --------------------------------------------------------------------------- #
# Refusals reach the scrub, not just the walker
# --------------------------------------------------------------------------- #
def test_the_walker_refusals_apply_before_a_single_byte_is_rewritten():
    """A partial clean that reports success is worse than a refusal."""
    cases = {
        "mebx track": c.handbuilt(tracks=(
            {"track_id": 1, "handler": b"vide", "name": "VideoHandler"},
            {"track_id": 2, "handler": b"mebx", "name": "Metadata"})),
        "fragmented": c.handbuilt(extra_top=c.box(b"moof", b"\x00" * 16)),
        "encrypted": c.handbuilt(extra_top=c.box(b"sinf", b"\x00" * 8)),
        "bad offsets": c.handbuilt(chunk_offset_delta=1 << 20),
    }
    for label, data in cases.items():
        with pytest.raises(ParseError, match=".+") as exc:
            f1.scrub(data)
        assert "MP4" in str(exc.value), f"{label}: refusal does not name the format"


def test_the_cli_writes_nothing_when_it_refuses(tmp_path):
    src = tmp_path / "in.mp4"
    src.write_bytes(c.handbuilt(tracks=(
        {"track_id": 1, "handler": b"vide", "name": "VideoHandler"},
        {"track_id": 2, "handler": b"mebx", "name": "Metadata"})))
    out = tmp_path / "out.mp4"
    r = subprocess.run([sys.executable, "-m", "src.scrub", str(src), str(out),
                        "--fidelity", "F1"], cwd=REPO, capture_output=True,
                       text=True)
    assert r.returncode != 0
    assert not out.exists(), "a refused scrub must leave no output"
    assert "mebx" in (r.stdout + r.stderr)


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #
def test_an_mp4_now_resolves_to_the_mp4_handler():
    handler = dispatch.default_dispatcher().resolve(c.handbuilt())
    assert handler.format_id == "mp4"
    assert handler.fidelities == ("F1",)


def test_registering_mp4_did_not_steal_audio_from_m4a():
    """The claim-overlap property, asserted through real dispatch this time."""
    audio = c.handbuilt(tracks=(
        {"track_id": 1, "handler": b"soun", "name": "SoundHandler",
         "fmt": b"mp4a"},))
    assert dispatch.default_dispatcher().resolve(audio).format_id == "m4a"


# --------------------------------------------------------------------------- #
# The acceptance test: decode, do not parse
# --------------------------------------------------------------------------- #
@needs_ffmpeg
def test_real_files_decode_to_identical_video_after_scrubbing(tmp_path):
    """Every producer ffmpeg can make, including the layout where the offsets
    genuinely have to move."""
    corpus = c.ffmpeg_corpus(str(tmp_path))
    for name, path in corpus.items():
        scrubbed = str(tmp_path / f"s_{name}.mp4")
        with open(scrubbed, "wb") as f:
            f.write(f1.scrub(open(path, "rb").read()))
        original = _decode(path, str(tmp_path / f"a_{name}.raw"))
        cleaned = _decode(scrubbed, str(tmp_path / f"b_{name}.raw"))
        assert original, f"{name}: the ORIGINAL did not decode; bad fixture"
        assert cleaned == original, \
            f"{name}: decoded video differs after scrubbing"


@needs_ffmpeg
@pytest.mark.skipif(not HAVE_EXIFTOOL, reason="exiftool not installed")
def test_an_independent_tool_confirms_the_metadata_is_gone(tmp_path):
    path = c.ffmpeg_sample(str(tmp_path / "tagged.mp4"), gps=True)
    scrubbed = tmp_path / "clean.mp4"
    scrubbed.write_bytes(f1.scrub(open(path, "rb").read()))

    def tags(p):
        return subprocess.run(["exiftool", "-a", "-G1", "-s", str(p)],
                              capture_output=True, text=True).stdout

    before, after = tags(path), tags(scrubbed)
    assert "GPSLatitude" in before, "fixture carries no GPS; nothing is proven"
    for gone in ("GPSLatitude", "GPSLongitude", "LocationInformation",
                 "Lavf", "Ground truth", "HandlerDescription"):
        assert gone not in after, f"{gone} survived our scrub"


# --------------------------------------------------------------------------- #
# The constant the fingerprint guard made us declare
# --------------------------------------------------------------------------- #
def test_the_declared_handler_constant_carries_no_locus():
    """A broad declaration must not be able to hide a real leak.

    Blanking handler names closes a producer channel (`Core Media Video` vs
    `VideoHandler`) and, because everything else in a `hdlr` box is fixed by the
    format, makes every output carry a byte-identical box. The fingerprint guard
    flagged that correctly — it is a constant this tool introduces — and the
    answer, as for DOCX's empty `_rels`, is to declare it rather than suppress it.

    DOCX also set the condition on such a declaration, which is this test: assert
    the declared bytes carry nothing that could be a leak, so "declared" can never
    quietly mean "excused".
    """
    for constant in iso.canonical_handler_boxes():
        assert len(constant) < 64, "a declaration this large could hide a locus"
        # Decompose it rather than pattern-match it: two zero bytes, the size
        # word, `hdlr`, version/flags, predefined, the handler type, twelve
        # reserved zeros, an empty name, two zero bytes.
        assert constant[:2] == b"\x00\x00" and constant[-2:] == b"\x00\x00"
        box = constant[2:-2]
        assert int.from_bytes(box[:4], "big") == len(box)
        assert box[4:8] == b"hdlr"
        assert box[8:16] == b"\x00" * 8            # version/flags + predefined
        handler = box[16:20]
        assert handler in (b"soun", b"vide", b"mdir", b"mdta"), handler
        assert box[20:32] == b"\x00" * 12          # reserved
        assert box[32:] == b"\x00", "the handler NAME is not empty"
        # And nothing that looks like a timestamp, a tag or a coordinate.
        for magic in (b"loci", b"ilst", b"keys", b"udta", b"\xa9"):
            assert magic not in constant


def test_the_declaration_is_generated_from_the_code_that_writes_it():
    """Transcribed constants drift. This one must be produced by the same
    `blank_handler_name()` the strip calls, so it cannot describe an output the
    tool no longer emits."""
    boxes = iso.parse(f1.scrub(c.handbuilt()))
    emitted = [b for root in boxes for b in root.walk() if b.type == b"hdlr"]
    assert emitted, "no hdlr box in the output at all"
    declared = b"".join(iso.canonical_handler_boxes())
    for box in emitted:
        rendered = iso.serialize([box])
        assert rendered in declared, (
            f"the strip emits a hdlr box the declaration does not cover: "
            f"{rendered.hex(' ')}")
