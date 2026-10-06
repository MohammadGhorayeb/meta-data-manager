"""The generated MP4 Pareto matrix records honest verdicts.

MP4's headline is a measured failure with its channel named, the same shape HEIC's
was and PDF's before it: F1 deletes, zeroes and re-lays-out, so it cannot change
what the muxer *chose to be*. The value of running the cell is the list of features
that survive, because that list is the specification for the F2 that would close
them — and, unusually, the list of features that F1 **did** close, because five of
the nine did and a cell that only reported failures would hide that.

What makes this peer set worth trusting more than M4A's: one of its four producers
is a genuinely different muxer (AVFoundation, pass-through so the coded video is
identical), not another setting of the same program. Measuring a container channel
against one implementation's options would mostly measure the options.
"""
from __future__ import annotations

import pytest

from tests.harness.runner import matrix
from tests.scrub import e_mp4, gen_matrix_mp4
from tests.scrub import mp4_iso_corpus as mc

pytestmark = pytest.mark.skipif(not mc.HAVE_FFMPEG, reason="ffmpeg absent")


def _cell(doc, adversary, fidelity):
    return next(c for c in doc["cells"]
                if c["adversary"] == adversary and c["fidelity"] == fidelity)


@pytest.fixture(scope="module")
def doc(tmp_path_factory):
    return gen_matrix_mp4.build_doc(str(tmp_path_factory.mktemp("mp4_matrix")))


def test_matrix_builds_and_validates(doc):
    matrix.validate(doc)
    assert doc["format"] == "mp4"
    assert doc["scrubber_fingerprint"]["verdict"] == "pass"


def test_a1_passes_at_f1(doc):
    """`udta/loci`, the tag list, all three timestamp boxes and the handler names
    collapse: files differing only in metadata scrub to indistinguishable output."""
    assert _cell(doc, "A1", "F1")["verdict"] == "pass"


def test_a2_fails_at_f1_and_names_the_channel(doc):
    """Expected, and the reason the cell is worth running.

    A bit-preserving tier passes every muxer choice through. The cell must say
    *which* choices, per key, rather than reporting a channel verdict that would
    read the same whether F1 had closed most of them or none — the correction
    DOCX's F2 cell had to make.
    """
    cell = _cell(doc, "A2", "F1")
    assert cell["verdict"] == "fail"
    leaking = {lk["locus"]["feature_id"] for lk in cell["leaks"]}
    # Visible to any peer set: ffmpeg's own faststart flag separates these.
    assert "struct:top_level_order" in leaking
    assert "struct:moov_before_mdat" in leaking
    # `brand` needs a producer that stamps a different one, which every ffmpeg
    # configuration does not -- they all write `isom`. The verdict is FAIL either
    # way (verified), so no published claim turns on this; the channel set does.
    if mc.HAVE_AVFOUNDATION:
        assert "struct:brand" in leaking
    assert "muxer:" in cell["reason"]


def test_the_cell_says_which_producers_it_had(doc):
    """A peer set that silently shrinks turns "we compared four producers" into a
    claim about three. macOS-only AVFoundation is named when present and its
    absence is stated when not."""
    reason = _cell(doc, "A2", "F1")["reason"]
    assert "Peer set =" in reason
    if mc.HAVE_AVFOUNDATION:
        assert "avfoundation" in reason
    else:
        assert "macOS-only" in reason


def test_f2_and_f3_are_not_tested_rather_than_failed(doc):
    """A tier nobody ran has no verdict. Writing `fail` there would be inventing a
    measurement, and writing `pass` would be worse."""
    for fidelity in ("F2", "F3"):
        for adversary in ("A1", "A2"):
            cell = _cell(doc, adversary, fidelity)
            assert cell["verdict"] == "not_tested"
            assert "not_built" in cell["reason"]


def test_the_unbuilt_tier_reason_specifies_what_would_close_the_cell(doc):
    """The A2@F1 leaks ARE the F2 spec, and the matrix says so rather than leaving
    a reader to infer it."""
    reason = _cell(doc, "A2", "F2")["reason"]
    for word in ("brand", "top-level order", "`moov` position"):
        assert word in reason


def test_f1_closes_channels_rather_than_only_leaking_them(tmp_path):
    """The other half of the story, asserted rather than trusted.

    A cell that reports only what leaks cannot distinguish a tier that closed most
    of the channel from one that closed none of it. What closes is what makes the
    survivors a specification rather than a shrug.

    Which channels are *visible* depends on the peer set, so the assertion does
    too — see `test_an_ffmpeg_only_peer_set_cannot_see_two_of_the_channels`.
    """
    sources = e_mp4.build_sources(str(tmp_path), repeats=2)
    raw = e_mp4.run_condition("raw", sources, str(tmp_path))
    f1 = e_mp4.run_condition("F1", sources, str(tmp_path))
    closed = set(raw["struct_fingerprints"]) - set(f1["struct_fingerprints"])

    if mc.HAVE_AVFOUNDATION:
        # Only a second MUXER separates these before scrubbing. `free_bytes` and
        # `box_inventory` were asserted outside this branch until the first Linux
        # run: no ffmpeg-only peer set separates them either, so "closed" there
        # would have been a claim about a feature that never fired (limit #44).
        assert {"struct:free_bytes", "struct:box_inventory",
                "struct:handler_names", "struct:mdat_header_form",
                "struct:timestamps_present"} <= closed
    # And the keys that specify F2 are still open, or the spec is wrong.
    assert {"struct:top_level_order",
            "struct:moov_before_mdat"} <= set(f1["struct_fingerprints"])


def test_an_ffmpeg_only_peer_set_cannot_see_two_of_the_channels(tmp_path):
    """The resolution a macOS-only producer buys, measured rather than asserted.

    ffmpeg writes the same handler names, the same brand and the same 32-bit
    `mdat` header in every configuration, so an ffmpeg-only peer set cannot
    separate producers on those keys **even before scrubbing** — and "F1 closes
    the handler-name channel" would be a claim about a feature that never fired.
    A second muxer is what makes those channels observable.

    This is limit #44, and it is a test rather than a note because the honest
    reading of the CI matrix depends on it: the Linux run measures a strictly
    smaller channel set and its cell says which producers it had.
    """
    if not mc.HAVE_AVFOUNDATION:
        pytest.skip("already the ffmpeg-only peer set; nothing to contrast")
    sources = e_mp4.build_sources(str(tmp_path), repeats=2)
    with_avf = set(e_mp4.run_condition("raw", sources, str(tmp_path))
                   ["struct_fingerprints"])
    ffmpeg_only = {k: v for k, v in sources.items() if k != "avfoundation"}
    without = set(e_mp4.run_condition("raw", ffmpeg_only, str(tmp_path))
                  ["struct_fingerprints"])

    assert "struct:handler_names" in with_avf
    assert "struct:handler_names" not in without, (
        "the ffmpeg-only peer set separates handler names, which would make "
        "limit #44 wrong -- check whether the producers were made to differ")
    assert with_avf > without, "the second muxer added no resolution at all"


def test_the_handler_name_channel_fires_when_it_can_be_seen(tmp_path):
    """M3 found that M4A's plugin never measured handler names, so that leak was
    invisible to the matrix as well as to ExifTool. MP4's plugin measures it — and
    a feature that never separates anything is not a measurement, so this asserts
    it actually fires on unscrubbed files."""
    if not mc.HAVE_AVFOUNDATION:
        pytest.skip("needs a second muxer; ffmpeg alone writes one set of names")
    sources = e_mp4.build_sources(str(tmp_path), repeats=2)
    raw = e_mp4.run_condition("raw", sources, str(tmp_path))
    assert "struct:handler_names" in raw["struct_fingerprints"]


@pytest.mark.skipif(not mc.have_encoder("libx264"), reason="no libx264")
def test_b_frames_show_through_the_box_list_and_f1_keeps_them(tmp_path):
    """Limit #48. An encoder that uses B-frames needs a `ctts` table (composition
    offsets) to be decoded at all, so the container's box list says how the video
    was encoded. Found when the Linux peer set first used a different x264 preset:
    `box_inventory` separated the producers and survived F1. F1 keeps the coded
    video by definition, and `ctts` with it -- removing it breaks playback order."""
    from src.scrub.formats.mp4 import f1 as mp4_f1
    from src.scrub.standards import isobmff as iso

    def boxes(data: bytes) -> set[bytes]:
        found, todo = set(), [b for b in iso.parse(data) if b.type == b"moov"]
        while todo:
            box = todo.pop()
            found.add(box.type)
            todo.extend(box.children)
        return found

    plain = mc.ffmpeg_sample(str(tmp_path / "u.mp4"))
    bframes = mc.ffmpeg_sample(str(tmp_path / "m.mp4"), preset="medium")
    assert b"ctts" not in boxes(mp4_f1.scrub(open(plain, "rb").read()))
    assert b"ctts" in boxes(mp4_f1.scrub(open(bframes, "rb").read()))
