"""JPEG F2 handler: lossless re-encode — metadata removal, pixel identity,
DQT-survives residual, restart/Huffman normalization, determinism, guard.

Skipped wholesale when jpegtran is absent (F2 fails closed on it at runtime; the
unit suite documents the dependency rather than pretending to cover it)."""
from __future__ import annotations

import io
import shutil

import pytest
from PIL import Image

from src.scrub.formats.jpeg import f2
from src.scrub.formats.jpeg import segments as seg
from tests.scrub import corpus

pytestmark = pytest.mark.skipif(
    shutil.which("jpegtran") is None, reason="jpegtran (libjpeg-turbo) not installed")

_METADATA_KINDS = {
    "app1_exif", "app1_xmp", "app1_xmp_extended", "app2_icc", "app2_mpf",
    "app13_photoshop", "com",
}


def _dqt_payloads(data: bytes) -> list[bytes]:
    return [s.payload for s in seg.walk(data).by_kind("dqt")]


def test_f2_removes_every_metadata_locus():
    scrubbed = f2.scrub(corpus.build_torture_jpeg())
    kinds = {sg.kind for sg in seg.walk(scrubbed).segments}
    assert not (_METADATA_KINDS & kinds), f"metadata survived: {_METADATA_KINDS & kinds}"
    assert seg.walk(scrubbed).trailer == b"", "trailer bytes survived"
    assert f2.residuals(scrubbed) == []


@pytest.mark.filterwarnings("ignore:.*malformed MPO.*")
def test_f2_preserves_pixels_bit_exact():
    torture = corpus.build_torture_jpeg()
    scrubbed = f2.scrub(torture)
    before = Image.open(io.BytesIO(torture)).convert("RGB")
    after = Image.open(io.BytesIO(scrubbed)).convert("RGB")
    assert before.size == after.size
    assert before.tobytes() == after.tobytes(), "F2 changed decoded pixels (not lossless)"


def test_f2_preserves_quantization_tables():
    """The documented F2 residual (Kornblum): quantization cannot change
    losslessly, so DQT bytes survive verbatim — this is *why* A2@F2 stays red."""
    torture = corpus.build_torture_jpeg()
    scrubbed = f2.scrub(torture)
    assert _dqt_payloads(scrubbed) == _dqt_payloads(torture), \
        "DQT changed — F2 must not touch quantization"


def test_f2_strips_restart_markers():
    scrubbed = f2.scrub(corpus.build_torture_jpeg())
    assert not seg.walk(scrubbed).by_kind("dri"), "DRI/restart interval survived F2"


def test_f2_regenerates_huffman_tables():
    """F2 must actually re-emit DHT (the Huffman-fingerprint kill), so a valid
    optimized stream always carries its own tables."""
    scrubbed = f2.scrub(corpus.build_torture_jpeg())
    assert seg.walk(scrubbed).by_kind("dht"), "no DHT in F2 output"


def test_f2_is_deterministic():
    torture = corpus.build_torture_jpeg()
    assert f2.scrub(torture) == f2.scrub(torture)


def test_f2_output_is_clean_jpeg_structure():
    """Only image-defining markers + libjpeg's canonical JFIF — nothing else."""
    scrubbed = f2.scrub(corpus.build_torture_jpeg())
    allowed = {"soi", "eoi", "sos", "dqt", "dht", "dri", "dac", "app0_jfif"}
    allowed |= {f"sof{n}" for n in range(16)}
    kinds = {sg.kind for sg in seg.walk(scrubbed).segments}
    assert kinds <= allowed, f"unexpected segments in F2 output: {kinds - allowed}"


def test_residuals_flags_unscrubbed_input():
    dirty = corpus.build_torture_jpeg()
    assert f2.residuals(dirty), "residuals() failed to flag a dirty file"


def test_f2_handles_cmyk_keeping_colour_transform():
    """Regression (benchmark-found): a CMYK JPEG must scrub, not fail closed.
    libjpeg re-emits a canonical Adobe APP14 to signal the colour transform;
    F2 must allow that marker (dropping it would misrender), while still leaving
    no actual metadata and preserving the CMYK colour space."""
    buf = io.BytesIO()
    Image.new("CMYK", (96, 72), (10, 20, 30, 5)).save(buf, "JPEG", quality=90)
    scrubbed = f2.scrub(buf.getvalue())
    assert f2.residuals(scrubbed) == [], "F2 wrongly flagged the canonical APP14"
    kinds = {s.kind for s in seg.walk(scrubbed).segments}
    assert "app14_adobe" in kinds, "colour-transform marker must survive for CMYK"
    assert not (_METADATA_KINDS & kinds), "no real metadata may survive"
    assert Image.open(io.BytesIO(scrubbed)).mode == "CMYK", "CMYK must be preserved"
