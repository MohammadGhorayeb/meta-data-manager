"""JPEG F1 handler: leak removal, content preservation, determinism, guard."""
from __future__ import annotations

import io

import pytest
from PIL import Image

from src.scrub.formats.jpeg import f1
from src.scrub.formats.jpeg import segments as seg
from tests.scrub import corpus

_METADATA_KINDS = {
    "app1_exif", "app1_xmp", "app1_xmp_extended", "app2_icc", "app2_mpf",
    "app13_photoshop", "com",
}


def test_f1_removes_every_metadata_locus():
    scrubbed = f1.scrub(corpus.build_torture_jpeg())
    s = seg.walk(scrubbed)
    kinds = {sg.kind for sg in s.segments}
    assert not (_METADATA_KINDS & kinds), f"metadata survived: {_METADATA_KINDS & kinds}"
    assert s.trailer == b"", "trailer bytes survived"
    # our own post-condition guard agrees the output is clean
    assert f1.residuals(scrubbed) == []


@pytest.mark.filterwarnings("ignore:.*malformed MPO.*")
def test_f1_preserves_pixels_bit_exact():
    torture = corpus.build_torture_jpeg()
    scrubbed = f1.scrub(torture)
    before = Image.open(io.BytesIO(torture)).convert("RGB")
    after = Image.open(io.BytesIO(scrubbed)).convert("RGB")
    assert before.size == after.size
    assert before.tobytes() == after.tobytes(), "F1 changed decoded pixels"


def test_f1_preserves_entropy_bytes_verbatim():
    torture = corpus.build_torture_jpeg()
    scrubbed = f1.scrub(torture)
    sos_in = seg.walk(torture).by_kind("sos")[0]
    sos_out = seg.walk(scrubbed).by_kind("sos")[0]
    # the entire SOS segment (scan header + entropy-coded data) is byte-identical
    assert torture[sos_in.offset:sos_in.end] == scrubbed[sos_out.offset:sos_out.end]


def test_f1_canonicalizes_jfif():
    scrubbed = f1.scrub(corpus.build_torture_jpeg())
    jfif = seg.walk(scrubbed).by_kind("app0_jfif")
    assert jfif, "expected a canonical JFIF in output"
    s = jfif[0]
    assert scrubbed[s.offset:s.end] == f1.CANONICAL_JFIF


def test_f1_is_deterministic():
    torture = corpus.build_torture_jpeg()
    assert f1.scrub(torture) == f1.scrub(torture)


def test_residuals_flags_unscrubbed_input():
    # sanity: the guard must actually detect leaks, not vacuously pass
    dirty = corpus.build_torture_jpeg()
    assert f1.residuals(dirty), "residuals() failed to flag a dirty file"
