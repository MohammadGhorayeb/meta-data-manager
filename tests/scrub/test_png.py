"""PNG handler: chunk walker (CRC), F1 keep-list, F2 canonical re-encode.

Covers the headline P1 result — PNG A2@F2 passes *losslessly*: different producers
normalize to one deflate fingerprint with bit-identical pixels.
"""
from __future__ import annotations

import hashlib
import io

import pytest
from PIL import Image, PngImagePlugin

from src.scrub.errors import ParseError
from src.scrub.formats.png import chunks as ck
from src.scrub.formats.png import f1, f2


# --------------------------------------------------------------------------- #
# builders
# --------------------------------------------------------------------------- #
def _img(w=80, h=60, mode="RGB", seed=0):
    im = Image.new(mode, (w, h))
    if mode in ("RGB", "RGBA"):
        px = im.load()
        for y in range(h):
            for x in range(w):
                v = ((x * 3 + seed) % 256, (y * 5) % 256, ((x + y) * 7) % 256)
                px[x, y] = v + ((x * 2 % 256,) if mode == "RGBA" else ())
    return im


def _png(mode="RGB", seed=0, compress_level=9, with_text=True) -> bytes:
    meta = None
    if with_text:
        meta = PngImagePlugin.PngInfo()
        meta.add_text("Author", "Moe Secret")
        meta.add_text("GPS", "33.88N")
    buf = io.BytesIO()
    _img(mode=mode, seed=seed).save(buf, "PNG", pnginfo=meta,
                                    compress_level=compress_level)
    return buf.getvalue()


def _idat_sig(data: bytes) -> str:
    s = ck.walk(data)
    return hashlib.sha1(b"".join(c.data for c in s.by_type("IDAT"))).hexdigest()


def _rgba_sig(data: bytes) -> str:
    return hashlib.sha1(Image.open(io.BytesIO(data)).convert("RGBA").tobytes()).hexdigest()


# --------------------------------------------------------------------------- #
# chunk walker
# --------------------------------------------------------------------------- #
def test_walk_parses_and_verifies_crc():
    s = ck.walk(_png())
    assert s.types()[0] == "IHDR" and s.types()[-1] == "IEND"
    assert all(c.crc_ok for c in s.chunks)


def test_walk_rejects_bad_crc():
    data = bytearray(_png())
    # find the first IDAT and flip a byte inside its data to break the CRC
    s = ck.walk(bytes(data))
    idat = s.by_type("IDAT")[0]
    data[idat.offset + 8] ^= 0xFF
    with pytest.raises(ParseError, match="CRC mismatch"):
        ck.walk(bytes(data))


def test_walk_rejects_non_png():
    with pytest.raises(ParseError, match="not a PNG"):
        ck.walk(b"\x89PNGbroken")


def test_assemble_roundtrip_is_stable():
    data = _png(with_text=False)
    s = ck.walk(data)
    assert ck.walk(ck.assemble(s.chunks)).types() == s.types()


# --------------------------------------------------------------------------- #
# F1 — keep-list
# --------------------------------------------------------------------------- #
def test_f1_removes_text_metadata():
    scrubbed = f1.scrub(_png())
    types = ck.walk(scrubbed).types()
    assert "tEXt" not in types and "zTXt" not in types and "iTXt" not in types
    assert ck.walk(scrubbed).trailer == b""
    assert f1.residuals(scrubbed) == []


def test_f1_keeps_idat_verbatim():
    data = _png()
    assert _idat_sig(f1.scrub(data)) == _idat_sig(data), "F1 must not touch IDAT"


def test_f1_drops_trailer():
    data = _png(with_text=False) + b"TRAILERSECRET"
    # trailer after IEND must be gone
    assert ck.walk(f1.scrub(data)).trailer == b""


def test_f1_residuals_flags_metadata():
    assert f1.residuals(_png())


# --------------------------------------------------------------------------- #
# F2 — canonical re-encode (the lossless A2 defense)
# --------------------------------------------------------------------------- #
def test_f2_removes_metadata():
    scrubbed = f2.scrub(_png())
    types = ck.walk(scrubbed).types()
    assert "tEXt" not in types
    assert f2.residuals(scrubbed) == []


def test_f2_normalizes_deflate_fingerprint_losslessly():
    """Headline: two producers of the same image (different compress levels =>
    different IDAT) collapse to ONE deflate fingerprint after F2, pixels intact."""
    a = _png(seed=0, compress_level=9)
    b = _png(seed=0, compress_level=1)
    assert _idat_sig(a) != _idat_sig(b), "precondition: producers must differ"
    fa, fb = f2.scrub(a), f2.scrub(b)
    assert _idat_sig(fa) == _idat_sig(fb), "F2 must normalize the deflate fingerprint"
    # and it's lossless
    assert _rgba_sig(fa) == _rgba_sig(a)
    assert _rgba_sig(fb) == _rgba_sig(b)


def test_f2_is_deterministic():
    data = _png()
    assert f2.scrub(data) == f2.scrub(data)


def test_f2_preserves_alpha():
    data = _png(mode="RGBA")
    assert _rgba_sig(f2.scrub(data)) == _rgba_sig(data), "F2 dropped/changed alpha"


def test_f2_preserves_palette_pixels():
    buf = io.BytesIO()
    _img(mode="RGB").convert("P").save(buf, "PNG")
    data = buf.getvalue()
    assert _rgba_sig(f2.scrub(data)) == _rgba_sig(data)
