"""E3 DQT-fingerprint machinery: JpegPlugin.structural_features + the peer-set
producer analysis. Hermetic — synthesizes a two-producer corpus with distinct
quantization tables (different JPEG qualities stand in for different encoders),
so it needs no external HEIC corpus.
"""
from __future__ import annotations

import io
import os

from tests.harness.plugins.jpeg import JpegPlugin
from tests.scrub import corpus, e3_dqt


def _write_jpeg(path: str, seed: int, quality: int) -> None:
    img = corpus.make_pixels(64, 48, seed=seed)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    with open(path, "wb") as f:
        f.write(buf.getvalue())


def _build_two_producer_corpus(tmpdir: str, scenes: int = 4) -> None:
    """Two 'producers' = two fixed quality settings (=> two distinct DQTs),
    each applied to the same set of scenes. Same content across producers,
    different quantization — the essence of the DQT fingerprint."""
    for prod, q in (("qhi", 95), ("qlo", 60)):
        for i in range(scenes):
            _write_jpeg(os.path.join(tmpdir, f"{prod}__scene{i}.jpg"), seed=i, quality=q)


def test_structural_features_exposes_dqt(tmp_path):
    p = JpegPlugin()
    a = tmp_path / "a.jpg"; _write_jpeg(str(a), seed=1, quality=95)
    b = tmp_path / "b.jpg"; _write_jpeg(str(b), seed=1, quality=60)
    fa, fb = p.structural_features(str(a)), p.structural_features(str(b))
    assert "dqt" in fa and fa["dqt"] != "none"
    # same pixels, different quality -> different quantization tables
    assert fa["dqt"] != fb["dqt"], "DQT should differ across quantization settings"


def test_structural_features_dqt_is_content_independent(tmp_path):
    """Same encoder+quality, different scenes -> identical DQT (the property that
    makes it a producer fingerprint rather than a content feature)."""
    p = JpegPlugin()
    sigs = set()
    for i in range(4):
        f = tmp_path / f"s{i}.jpg"; _write_jpeg(str(f), seed=i, quality=90)
        sigs.add(p.structural_features(str(f))["dqt"])
    assert len(sigs) == 1, f"DQT varied across scenes at fixed quality: {sigs}"


def test_structural_features_empty_on_garbage(tmp_path):
    bad = tmp_path / "bad.jpg"; bad.write_bytes(b"not a jpeg")
    assert JpegPlugin().structural_features(str(bad)) == {}


def test_e3_flags_dqt_as_producer_fingerprint_raw(tmp_path):
    _build_two_producer_corpus(str(tmp_path))
    r = e3_dqt.run_condition("raw", str(tmp_path))
    assert r["dqt"] is not None and r["dqt"].leak, "DQT must fingerprint the producer"
    assert r["dqt"].max_within == 1, "DQT should be constant within a producer"
    assert r["a2_fail"] is True


def test_e3_fingerprint_survives_f1(tmp_path):
    _build_two_producer_corpus(str(tmp_path))
    r = e3_dqt.run_condition("F1", str(tmp_path))
    assert r["dqt"].leak, "F1 keeps DQT -> producer still separable"
    assert r["a2_fail"] is True


def test_e3_evaluate_cell_returns_fail(tmp_path):
    _build_two_producer_corpus(str(tmp_path))
    cell = e3_dqt.evaluate_cell("F1", str(tmp_path))
    assert cell.adversary == "A2" and cell.fidelity == "F1"
    assert cell.verdict.value == "fail"
    assert any(l.locus.feature_id == "struct:dqt" for l in cell.leaks)
