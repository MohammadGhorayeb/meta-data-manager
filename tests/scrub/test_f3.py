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


def test_f3_content_preserved():
    src = _jpeg(seed=3, quality=90)
    scrubbed = f3.scrub(src)          # raises ContentError if it diverged
    a, b = (Image.open(io.BytesIO(x)).convert("RGB") for x in (src, scrubbed))
    assert f3.psnr(a, b) >= f3.MIN_PSNR_DB


def _flat(kind: str) -> bytes:
    """Near-flat photos -- a clear sky, a night shot -- the kind a perceptual hash
    cannot fingerprint: its bits sit on the median and noise flips them."""
    import numpy as np
    rng = np.random.default_rng(3)
    h, w = 600, 800
    if kind == "sky":
        y = np.linspace(0, 1, h)[:, None, None]
        arr = (np.array([90, 150, 230]) * (1 - y) + np.array([200, 225, 250]) * y
               + rng.normal(0, 3, (h, w, 3)))
    else:
        arr = rng.normal(12, 6, (h, w, 3))
    buf = io.BytesIO()
    Image.fromarray(np.clip(arr, 0, 255).astype("uint8")).save(buf, "JPEG", quality=92)
    return buf.getvalue()


@pytest.mark.parametrize("kind", ["sky", "night"])
def test_f3_cleans_a_near_flat_photo(kind):
    """The hash gate refused these at distance 24 and 22 (51.5 and 46.9 dB)."""
    out = f3.scrub(_flat(kind))
    assert Image.open(io.BytesIO(out)).size == (800, 600)


@pytest.mark.parametrize("mangle", ["rotated", "rb_swapped", "grayscale", "darker",
                                    "shifted"])
def test_the_gate_refuses_a_re_encode_that_changed_the_picture(mangle):
    """What the gate is for. The hash passed the last four (distance 0-4)."""
    import numpy as np
    from PIL import ImageOps

    from src.scrub.errors import ContentError
    src = _jpeg(seed=4, quality=92)
    img = Image.open(io.BytesIO(src)).convert("RGB")
    arr = np.asarray(img)
    bad = {"rotated": lambda: img.rotate(180),
           "rb_swapped": lambda: Image.fromarray(arr[..., ::-1].copy()),
           "grayscale": lambda: ImageOps.grayscale(img).convert("RGB"),
           "darker": lambda: Image.fromarray((arr * 0.7).astype("uint8")),
           "shifted": lambda: Image.fromarray(np.roll(arr, 8, axis=1))}[mangle]()
    buf = io.BytesIO()
    bad.save(buf, "JPEG", quality=90)
    with pytest.raises(ContentError, match="PSNR"):
        f3._check_perceptual(src, buf.getvalue())


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
