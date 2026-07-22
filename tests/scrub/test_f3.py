"""JPEG F3 handler: canonical lossy re-encode — metadata removal, DQT
normalization (the A2 defense), perceptual content preservation, determinism.
"""
from __future__ import annotations

import hashlib
import io

import pytest
from PIL import Image

from src.scrub.formats.jpeg import f3
from src.scrub.formats.jpeg import segments as seg
from tests.scrub import corpus

_METADATA_KINDS = {
    "app1_exif", "app1_xmp", "app1_xmp_extended", "app2_icc", "app2_mpf",
    "app13_photoshop", "com",
}


def _dqt_sig(data: bytes) -> str:
    p = b"".join(s.payload for s in seg.walk(data).by_kind("dqt"))
    return hashlib.sha1(p).hexdigest()


def _jpeg(seed: int, quality: int) -> bytes:
    buf = io.BytesIO()
    corpus.make_pixels(64, 48, seed=seed).save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


@pytest.mark.filterwarnings("ignore:.*malformed MPO.*")
def test_f3_removes_every_metadata_locus():
    scrubbed = f3.scrub(corpus.build_torture_jpeg())
    kinds = {s.kind for s in seg.walk(scrubbed).segments}
    assert not (_METADATA_KINDS & kinds), f"metadata survived: {_METADATA_KINDS & kinds}"
    assert seg.walk(scrubbed).trailer == b""
    assert f3.residuals(scrubbed) == []


@pytest.mark.filterwarnings("ignore:.*malformed MPO.*")
def test_f3_strips_comment_leak():
    """Regression: Pillow re-emits a source COM via im.info['comment']; F3 must
    rebuild a bare image so no comment survives the re-encode."""
    with_com = corpus.insert_after_soi(
        corpus._content_only(corpus._plain_jpeg()),
        corpus.com_segment(b"secret-comment-should-not-survive"))
    scrubbed = f3.scrub(with_com)
    assert not seg.walk(scrubbed).by_kind("com"), "COM segment leaked through F3"


def test_f3_normalizes_dqt_across_inputs():
    """The A2 defense: different producers/qualities in -> ONE identical DQT out."""
    a = f3.scrub(_jpeg(seed=1, quality=60))
    b = f3.scrub(_jpeg(seed=1, quality=95))
    c = f3.scrub(_jpeg(seed=7, quality=80))
    assert _dqt_sig(a) == _dqt_sig(b) == _dqt_sig(c), \
        "F3 DQT must be identical regardless of input (producer-independent)"


def test_f3_content_preserved_under_phash():
    src = _jpeg(seed=3, quality=90)
    scrubbed = f3.scrub(src)          # raises ContentError if it diverged
    import imagehash
    d = imagehash.phash(Image.open(io.BytesIO(src))) - \
        imagehash.phash(Image.open(io.BytesIO(scrubbed)))
    assert d <= f3.PHASH_MAX_DISTANCE


def test_f3_output_decodes_and_matches_dimensions():
    src = _jpeg(seed=2, quality=85)
    scrubbed = f3.scrub(src)
    assert Image.open(io.BytesIO(src)).size == Image.open(io.BytesIO(scrubbed)).size


def test_f3_is_deterministic():
    src = _jpeg(seed=5, quality=88)
    assert f3.scrub(src) == f3.scrub(src)


@pytest.mark.filterwarnings("ignore:.*malformed MPO.*")
def test_residuals_flags_unscrubbed_input():
    assert f3.residuals(corpus.build_torture_jpeg())
