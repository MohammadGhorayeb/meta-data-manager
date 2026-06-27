"""Unit tests for oracle/floor.py (spec §9.8).

A deterministic scrubber (clean_stub) yields an empty noise floor; a scrubber
that stamps a per-run nonce (noisy_clean_stub) yields a non-empty floor whose
variable locus sits in byte space.
"""
from __future__ import annotations

from tests.harness.oracle import floor


def test_clean_stub_floor_is_deterministic(clean_stub, toy_plugin, toyf_file):
    report, repeats = floor.measure(clean_stub, toy_plugin, toyf_file, "F1", n=5)
    assert report.deterministic is True
    assert report.repeats == 5
    assert report.variable_loci == []
    # repeats are the per-run byte blobs and, being deterministic, identical.
    assert len(repeats) == 5
    assert len(set(repeats)) == 1


def test_clean_stub_floor_via_a1_variants(clean_stub, toy_plugin, a1_variants):
    # alternate way to obtain a TOYF input: first member of the first variant.
    inp = a1_variants()[0][0]
    report, _ = floor.measure(clean_stub, toy_plugin, inp, "F1", n=5)
    assert report.deterministic is True
    assert report.variable_loci == []


def test_noisy_clean_stub_floor_has_nonce_locus(noisy_clean_stub, toy_plugin, toyf_file):
    report, repeats = floor.measure(noisy_clean_stub, toy_plugin, toyf_file, "F1", n=5)
    assert report.deterministic is False
    assert report.repeats == 5
    assert report.variable_loci, "expected a non-empty floor for the per-run nonce"
    # the per-run nonce manifests as a byte-space locus.
    assert all(locus.space == "byte" for locus in report.variable_loci)
    assert any(
        locus.feature_id and locus.feature_id.startswith("byte@")
        for locus in report.variable_loci
    )
    # nonce really does vary across repeats.
    assert len(set(repeats)) > 1
