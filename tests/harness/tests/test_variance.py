"""Spec §9.6: between-group vs within-group variance decomposition.

Cells dicts are built BY HAND (no scrubbing) so each verdict is exercised in
isolation:
  (a) deterministic clean -> no leak, no floor
  (b) leaky                -> leak, no floor
  (c) nonce                -> floor, no leak
  (d) A2 per-source        -> leak (source-fingerprint condition)
"""
from __future__ import annotations

from tests.harness.oracle import variance


def test_deterministic_clean_no_leak_no_floor():
    # One feature, identical across both groups and every repeat.
    groups = ["g0", "g1"]
    repeats = [0, 1, 2, 3, 4]
    cells = {
        (g, r): {"f": b"same"}
        for g in groups
        for r in repeats
    }
    out = variance.decompose(cells, groups, repeats)
    v = out["f"]
    assert v.leak is False
    assert v.floor is False
    assert v.n_between == 1
    assert v.max_within == 1


def test_leaky_feature_separates_groups():
    # Feature constant WITHIN each group but differs BETWEEN groups.
    groups = ["g0", "g1"]
    repeats = [0, 1, 2, 3, 4]
    values = {"g0": b"A", "g1": b"B"}
    cells = {
        (g, r): {"f": values[g]}
        for g in groups
        for r in repeats
    }
    out = variance.decompose(cells, groups, repeats)
    v = out["f"]
    assert v.leak is True
    assert v.floor is False
    assert v.n_between == 2
    assert v.max_within == 1


def test_nonce_feature_is_floor_not_leak():
    # Feature differs across repeats WITHIN each group (>= groups+1 distinct
    # values per group, n_repeats > n_groups) so it is pure within-group noise.
    groups = ["g0", "g1"]
    repeats = [0, 1, 2]  # n_repeats (3) > n_groups (2)
    # Each group has 3 distinct values (>= n_groups + 1).
    per_group = {
        "g0": [b"n0", b"n1", b"n2"],
        "g1": [b"m0", b"m1", b"m2"],
    }
    cells = {
        (g, r): {"f": per_group[g][r]}
        for g in groups
        for r in repeats
    }
    out = variance.decompose(cells, groups, repeats)
    v = out["f"]
    assert v.floor is True
    assert v.leak is False
    assert v.max_within == 3
    # representative (mode) across groups: distinct-across <= within noise.
    assert v.n_between <= v.max_within


def test_a2_per_source_within_constant_across_variable_is_leak():
    # A2: grouping factor is the SOURCE. Within a source the marker is constant;
    # across sources it varies -> source fingerprint == leak.
    sources = ["src0", "src1"]
    members = [0, 1, 2]  # members_per_source as the within-group axis
    marker = {"src0": b"SRC0", "src1": b"SRC1"}
    cells = {
        (s, m): {"source_marker": marker[s]}
        for s in sources
        for m in members
    }
    out = variance.decompose(cells, sources, members)
    v = out["source_marker"]
    assert v.leak is True
    assert v.floor is False
    assert v.n_between == 2
    assert v.max_within == 1
