"""E-M4A-AUDIO: does the source's own encode survive F3 in the *sound*?

Guards the experiment as much as the result, exactly as the MP3 version does: an
adversary simulation that cannot recover the source from an UNSCRUBBED file proves
nothing when it also fails on a scrubbed one, so the controls are asserted first.

Needs a second AAC engine (`aac_at`, macOS AudioToolbox). Skipped without one rather
than quietly reducing to a single-engine set that cannot answer the question.
"""
from __future__ import annotations

import pytest

from tests.scrub import e_m4a_audio
from tests.scrub import m4a_corpus as mc

pytestmark = pytest.mark.skipif(
    not (mc.HAVE_FFMPEG and e_m4a_audio.have_second_engine()),
    reason="needs ffmpeg with a second AAC engine (aac_at)")


@pytest.fixture(scope="module")
def results():
    return e_m4a_audio.run()


def test_controls_recover_the_source_setting(results):
    """CONTROL: F1 and F2 copy the coded stream, so the source's quality setting must
    still be readable there. If it is not, the F3 number means nothing."""
    for cond in ("raw", "F1", "F2"):
        m = results[cond]
        assert m["recoverable"], (
            f"{cond}: source setting classified at only {m['accuracy']:.2f} vs "
            f"chance {m['chance']:.2f} — the feature is too weak to test F3 with")


def test_f3_destroys_the_recoverable_source_trace(results):
    """The claim: after the canonical re-encode, the source's own quality setting is
    no longer readable from the audio."""
    assert e_m4a_audio.controls_valid(results), "controls invalid; F3 proves nothing"
    m = results["F3"]
    assert not m["recoverable"], (
        f"source setting still recoverable after F3 at {m['accuracy']:.2f} vs chance "
        f"{m['chance']:.2f} — limit #10 must stay open and the cell must say so")


def test_engine_question_is_reported_as_unanswerable(results):
    """Honesty guard. The two available AAC encoders are near-twins, so engine
    identity is barely separable even on untouched files. That must be REPORTED, not
    dressed up as an F3 success — a classifier that cannot tell them apart to begin
    with would 'pass' after any treatment at all, including doing nothing."""
    raw_engine = results["raw"]["engine"]
    f3_engine = results["F3"]["engine"]
    assert not f3_engine["recoverable"]
    if not raw_engine["recoverable"]:
        pytest.skip("engine not separable even unscrubbed — nothing to conclude")


def test_peer_set_spans_two_engines():
    prods = e_m4a_audio.available_producers()
    assert {e_m4a_audio.class_of(p) for p in prods} == {"ffmpeg_aac", "apple_aac"}
    assert {e_m4a_audio.bitrate_class_of(p) for p in prods} == {"128k", "192k"}
