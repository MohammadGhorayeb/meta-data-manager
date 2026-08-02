"""E-ENGINE: the cross-engine A2 residual in audio space.

These tests guard the *experiment* as much as the result. An adversary simulation
that cannot recover the engine from an unscrubbed file proves nothing when it also
fails on a scrubbed one — so the controls are asserted first, and the F3 claim is
only meaningful behind them.

Needs shineenc (a non-libmp3lame engine); skipped without it rather than quietly
downgrading to a same-engine peer set that cannot answer the question.
"""
from __future__ import annotations

import numpy as np
import pytest

from tests.scrub import e_engine
from tests.scrub import mp3_corpus as mc

pytestmark = pytest.mark.skipif(
    not (mc.HAVE_FFMPEG and mc.HAVE_LAME and mc.HAVE_SHINE),
    reason="needs ffmpeg + lame + shineenc (second engine)")


@pytest.fixture(scope="module")
def results():
    return e_engine.run(fidelities=("raw", "F1", "F3"))


def test_peer_set_spans_two_engines():
    prods = e_engine.available_producers()
    assert set(e_engine.engines(prods)) == {"lame", "shine"}, (
        "the cross-engine question needs a genuinely different engine, not another "
        f"LAME front-end: {prods}")


def test_level_cannot_be_the_discriminator(tmp_path):
    """Rules out the obvious shortcut: the classifier separating engines by loudness.

    Two properties do it. (1) Within a content, every producer encodes the SAME wav,
    so level is matched across the classes being told apart — measured here by
    building one content and checking the per-producer broadband level agrees.
    (2) Across contents the corpus deliberately varies amplitude, so no single level
    marks a class. (A per-file scale test would be the wrong check: LAME's high-band
    bit allocation is itself level-dependent, so hf19/hf20 legitimately move with
    amplitude — that is encoder behaviour, which is what this experiment measures.)
    """
    amps = [c[1] for c in e_engine.CONTENTS if "amplitude=" in c[1]]
    levels = {a.split("amplitude=")[1].split(":")[0].rstrip("'\"") for a in amps}
    assert len(levels) > 1, f"corpus must vary level across contents, got {levels}"

    prods = e_engine.available_producers()
    paths = e_engine.build_corpus(str(tmp_path), prods)
    one = e_engine.CONTENTS[0][0]
    rms = []
    for p in prods:
        import subprocess
        out = subprocess.run(["ffmpeg", "-i", paths[(p, one)], "-f", "s16le",
                              "-ac", "1", "-ar", "44100", "-loglevel", "error",
                              "pipe:1"], capture_output=True).stdout
        x = np.frombuffer(out, dtype="<i2").astype(float) / 32768.0
        rms.append(float(np.sqrt((x ** 2).mean())))
    spread = max(rms) / max(min(rms), 1e-9)
    assert spread < 1.3, (
        f"producers differ in level on identical source audio ({rms}) — the "
        "classifier could read loudness instead of the encoder")


def test_controls_recover_the_engine(results):
    """CONTROL: on unscrubbed and F1 files the engine must be plainly recoverable.
    If this fails, no conclusion may be drawn from the F3 number."""
    for cond in ("raw", "F1"):
        m = results[cond]
        assert m["recoverable"], (
            f"{cond}: engine classification {m['accuracy']:.2f} is not above chance "
            f"{m['chance']:.2f} — the feature is too weak to test F3 with")


def test_f3_destroys_the_cross_engine_trace(results):
    """The claim itself: after the canonical re-encode, a peer-corpus adversary
    cannot tell which engine produced the original from the audio."""
    assert e_engine.controls_valid(results), "controls invalid; F3 result meaningless"
    m = results["F3"]
    assert not m["recoverable"], (
        f"cross-engine trace survives F3: engine classified at {m['accuracy']:.2f} "
        f"vs chance {m['chance']:.2f} — the A2@F3 cell must be downgraded and the "
        f"residual documented. Confusion: {m['confusion']}")
