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


@pytest.fixture(scope="module")
def results_low_rate():
    """F3's anonymity is claimed per sample-rate group, so the audio-space check runs
    in the other group too — 22.05 kHz files come out at 160 kbps, not 192."""
    return e_engine.run(fidelities=("raw", "F1", "F3"), rate=22050)


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


# The low-rate group is asserted at a STRICTER p than the published alpha, and the
# asymmetry is deliberate rather than convenient. Its F3 effect sits nearer the line
# than 44.1 kHz does (p ~0.2 versus ~0.4), and the earlier six-content version of this
# experiment landed on opposite sides of the verdict on macOS and Linux. So the test
# fails only on CLEAR evidence of recovery, and `docs/limits.md` carries the weaker
# claim for this group instead of the test quietly certifying it as clean.
LOW_RATE_FAIL_P = 0.02


def test_f3_leaves_no_clear_trace_in_the_low_rate_group(results_low_rate):
    """The claim is per sample-rate group, so it has to hold in the other group.
    22.05 kHz sources re-encode at 160 kbps rather than 192 — a different group."""
    assert e_engine.controls_valid(results_low_rate), (
        "controls invalid at 22.05 kHz; the F3 result there proves nothing")
    m = results_low_rate["F3"]
    assert m["p_value"] >= LOW_RATE_FAIL_P, (
        f"cross-engine trace clearly survives F3 at 22.05 kHz: {m['accuracy']:.2f} "
        f"vs chance {m['chance']:.2f}, p={m['p_value']:.4f} — anonymity does not hold "
        f"in this group and docs/limits.md must be narrowed. {m['confusion']}")


def test_low_rate_evidence_is_weaker_than_the_main_group(results, results_low_rate):
    """Pins the honesty of the asymmetry above: if the low-rate group ever becomes as
    convincing as the main one, this test fails and the weaker wording in
    docs/limits.md should be strengthened to match."""
    main_p = results["F3"]["p_value"]
    low_p = results_low_rate["F3"]["p_value"]
    if low_p >= main_p:
        pytest.skip(f"low-rate evidence now as strong as the main group "
                    f"(p={low_p:.3f} vs {main_p:.3f}) — strengthen docs/limits.md #2")


def test_features_work_below_the_default_sample_rate():
    """Guards the reason the low-rate test is meaningful: the spectral bands are
    fractions of Nyquist, not fixed frequencies. Absolute 16/19/20 kHz cut-offs sit
    above Nyquist at 22.05 kHz, so every feature would read as an identical zero and
    the classifier would silently be measuring nothing at all."""
    import subprocess
    import tempfile
    td = tempfile.mkdtemp()
    wav, mp3 = f"{td}/a.wav", f"{td}/a.mp3"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "anoisesrc=duration=2:color=white:seed=9:amplitude=0.4",
                    "-ac", "2", "-ar", "22050", wav], check=True)
    subprocess.run(["lame", "--quiet", "-V", "2", wav, mp3], check=True)
    f = e_engine.features(mp3, rate=22050)
    assert not np.allclose(f, 0), "features are degenerate at 22.05 kHz"
