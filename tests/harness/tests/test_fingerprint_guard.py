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


def test_a_tag_beside_a_declared_mark_is_still_caught(version_stub, toy_plugin,
                                                       diverse_inputs):
    """Declaring zeros as a mark (as in-place blanking does) explains runs made of
    zeros and the input's own bytes -- never the tool's own string between them."""
    class ZerosDeclared(type(toy_plugin)):
        def mandatory_constants(self):
            return [*super().mandatory_constants(), bytes(4), b"\0"]

    verdict, detail = fingerprint_guard.evaluate(
        version_stub, ZerosDeclared(), diverse_inputs, "F1", min_len=4)
    assert verdict == "fail"
    expected = VERSION_TAG.lstrip(b"\x00").decode("latin-1")
    assert any(expected in d["decoded"] for d in detail)


def test_a_mark_beside_echoed_bytes_is_explained():
    marks = [bytes(4)]
    inputs = [b"..CompanyName=ALICE..", b"..CompanyName=BRUNO.."]
    joined = b"CompanyName=" + bytes(8)
    assert fingerprint_guard.explained(joined, marks, inputs, 4)
    assert not fingerprint_guard.explained(b"TOOLv1" + bytes(8), marks, inputs, 4)
    assert not fingerprint_guard.explained(joined, [], inputs, 4)


def test_fill_is_stripped_from_the_edges_only():
    """A declared fill character explains blank padding around echoed text, but
    a stamp that merely CONTAINS the fill is still a stamp."""
    fills = [b"0", b"\0"]
    inputs = [b"vcs.time=2026-10-06T09:15:51Z\nbuild", b"\0\0_main\0_main\0"]
    assert fingerprint_guard.explained(b"00Z\nbuild", fills, inputs, 4)
    assert fingerprint_guard.explained(bytes(40) + b"_main\0_main" + bytes(9), fills,
                                       inputs, 4)
    assert not fingerprint_guard.explained(b"20261007", fills, inputs, 4)
    assert not fingerprint_guard.explained(b"\0\0SCRUBv1\0\0", fills, inputs, 4)
