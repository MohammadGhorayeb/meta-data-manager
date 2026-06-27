"""Unit tests for oracle/fingerprint_guard.py (spec §9.10).

clean_stub introduces only framing/format-mandated or echoed-content bytes across
diverse inputs, so the guard passes. version_stub stamps a constant tool tag on
every output, so the guard fails with a signature decoding to the stub's
VERSION_TAG (asserted dynamically, not against a hard-coded string).
"""
from __future__ import annotations

from tests.harness.oracle import fingerprint_guard
from tests.harness.stubs.version_stub import VERSION_TAG


def test_clean_stub_passes_guard(clean_stub, toy_plugin, diverse_inputs):
    verdict, detail = fingerprint_guard.evaluate(
        clean_stub, toy_plugin, diverse_inputs, "F1", min_len=4
    )
    assert verdict == "pass"
    assert detail == []


def test_version_stub_fails_with_decoded_tag(version_stub, toy_plugin, diverse_inputs):
    verdict, detail = fingerprint_guard.evaluate(
        version_stub, toy_plugin, diverse_inputs, "F1", min_len=4
    )
    assert verdict == "fail"
    assert detail, "expected at least one signature"
    assert all(d["space"] == "byte" for d in detail)
    # the detected signature decodes to whatever tag the stub actually stamps
    expected = VERSION_TAG.lstrip(b"\x00").decode("latin-1")
    assert any(expected in d["decoded"] for d in detail)
