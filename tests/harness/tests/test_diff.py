"""Spec §9.7: byte-diff -> offset-aligned features + hex context.

Exercises align_features for identical blobs, a single-byte change, and a
length divergence, checks hex_context windowing, and round-trips a small grid
through to_feature_maps.
"""
from __future__ import annotations

from tests.harness.config import HEX_CONTEXT
from tests.harness.oracle import diff


def test_identical_blobs_yield_no_features():
    assert diff.align_features([b"abc", b"abc"]) == {}


def test_single_byte_change_one_feature_with_per_blob_values():
    feats = diff.align_features([b"abc", b"aXc"])
    assert set(feats.keys()) == {"byte@1:1"}
    # per-blob values aligned to input order: the differing run bytes.
    assert feats["byte@1:1"] == [b"b", b"X"]


def test_hex_context_window():
    blob = b"abcdefghij"
    # ctx small enough to land strictly inside the blob, exercising both bounds.
    got = diff.hex_context(blob, offset=4, length=1, ctx=2)
    # window = [4-2, 4+1+2) = [2, 7) = b"cdefg"
    assert got == b"cdefg".hex()


def test_hex_context_clamps_at_edges():
    blob = b"abcdefghij"
    got = diff.hex_context(blob, offset=0, length=1, ctx=HEX_CONTEXT)
    # lo clamps to 0, hi clamps to len(blob).
    assert got == blob.hex()


def test_differing_length_emits_tail_and_length_divergence():
    feats = diff.align_features([b"abc", b"abcd"])
    assert "length_divergence" in feats
    assert feats["length_divergence"] == [3, 4]
    # first divergence is at the shorter length (3): blobs agree up to there.
    assert "byte_tail@3" in feats
    assert feats["byte_tail@3"] == [b"", b"d"]
    # exactly the coarse tail feature plus the marker, nothing else.
    assert set(feats.keys()) == {"byte_tail@3", "length_divergence"}


def test_to_feature_maps_round_trips_small_grid():
    grid = {
        ("g0", 0): b"aXc",
        ("g0", 1): b"aXc",
        ("g1", 0): b"aYc",
        ("g1", 1): b"aYc",
    }
    cells = diff.to_feature_maps(grid)
    # every grid key has a FeatureMap.
    assert set(cells.keys()) == set(grid.keys())
    # the only divergent column is offset 1, length 1.
    for k in grid:
        assert set(cells[k].keys()) == {"byte@1:1"}
    assert cells[("g0", 0)]["byte@1:1"] == b"X"
    assert cells[("g1", 0)]["byte@1:1"] == b"Y"
    # feeding these cells into variance treats offset-1 as a leak across groups.
    from tests.harness.oracle import variance

    out = variance.decompose(cells, ["g0", "g1"], [0, 1])
    assert out["byte@1:1"].leak is True
