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


def test_a_failed_scrub_is_named_rather_than_surfacing_as_a_missing_file():
    """`InProcessScrubber` catches exceptions and returns `ok=False` so a broken
    scrub cannot crash the oracle — but both call sites used to open the output path
    regardless, so a scrub that never wrote a file surfaced as `FileNotFoundError`
    inside the harness, naming neither the format, the tier, nor the reason.

    Found when a DOCX F3 run collided with another LibreOffice process: F3
    re-typesets through an engine that serialises on its own profile lock, making it
    the first tier here that can fail for a purely environmental reason. The bug was
    older than the tier; the tier is only what made it happen.
    """
    import pytest

    from tests.harness.contract import ScrubResult
    from tests.harness.oracle import floor

    class Failing:
        def run(self, in_path, out_path, fidelity):
            return ScrubResult(ok=False, out_path=out_path, fidelity=fidelity,
                               returncode=1, stderr="engine busy")

    with pytest.raises(RuntimeError, match="scrub failed.*engine busy"):
        floor.measure(Failing(), None, "/nonexistent/in", "F3", n=1)
