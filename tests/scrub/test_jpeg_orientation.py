"""EXIF orientation is content: a cleaned photo is displayed the way the original was.

Found in Phase 6 (`docs/p6_tail_plan.md` §9.1) and fixed in all three JPEG tiers:
they dropped the whole EXIF block, Orientation with it, so a phone photo held
upright -- stored sideways with Orientation = 6 -- came out displayed sideways in
every viewer that honours the tag. The pixel tests could not see it, because Pillow
decodes the stored pixels and ignores the tag; these tests compare what a viewer
shows (`ImageOps.exif_transpose`), which is what the hard constraint is about.
"""
from __future__ import annotations

import io

import pytest
from PIL import Image, ImageChops, ImageOps

from src.scrub.formats.jpeg import f1, f2, f3, orientation
from src.scrub.formats.jpeg import segments as seg

TIERS = {"F1": f1, "F2": f2, "F3": f3}


def photo(value: int, artist: str = "SENTINEL-ARTIST") -> bytes:
    """A 64x32 picture with a blue strip on its left edge: where the strip ends up
    on screen says which of the eight transforms a viewer applied."""
    img = Image.new("RGB", (64, 32), (200, 30, 30))
    img.paste((30, 30, 200), (0, 0, 16, 32))
    exif = Image.Exif()
    exif[0x0112] = value
    exif[0x013B] = artist
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92, exif=exif.tobytes())
    return buf.getvalue()


def shown(data: bytes) -> Image.Image:
    return ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")


def _strip_side(im: Image.Image) -> tuple:
    """Where the blue strip is: the bluest of the four edges."""
    w, h = im.size
    small = im.resize((max(1, w // 8), max(1, h // 8)))
    sw, sh = small.size
    edges = {"left": [small.getpixel((0, y)) for y in range(sh)],
             "right": [small.getpixel((sw - 1, y)) for y in range(sh)],
             "top": [small.getpixel((x, 0)) for x in range(sw)],
             "bottom": [small.getpixel((x, sh - 1)) for x in range(sw)]}
    return im.size, max(edges, key=lambda k: sum(p[2] - p[0] for p in edges[k]))


@pytest.mark.parametrize("tier", ["F1", "F2"])
@pytest.mark.parametrize("value", range(1, 9))
def test_the_lossless_tiers_display_exactly_as_the_original(tier, value):
    data = photo(value)
    out = TIERS[tier].scrub(data)
    assert shown(out).size == shown(data).size
    assert ImageChops.difference(shown(out), shown(data)).getbbox() is None
    assert b"SENTINEL" not in out and TIERS[tier].residuals(out) == []


@pytest.mark.parametrize("value", range(1, 9))
def test_f3_bakes_the_orientation_into_the_pixels(value):
    data = photo(value)
    out = f3.scrub(data)
    assert orientation.read(out) == 1, "F3 must not need a tag"
    assert _strip_side(shown(out)) == _strip_side(shown(data))
    assert b"SENTINEL" not in out and f3.residuals(out) == []


@pytest.mark.parametrize("value", range(2, 9))
def test_the_kept_segment_is_canonical_and_holds_only_the_orientation(value):
    out = f1.scrub(photo(value))
    exif = [s for s in seg.walk(out).segments if s.kind == "app1_exif"]
    assert len(exif) == 1
    assert bytes(out[exif[0].offset:exif[0].end]) == orientation.segment(value)


def test_an_upright_photo_gets_no_segment_at_all():
    out = f1.scrub(photo(1))
    assert not [s for s in seg.walk(out).segments if s.kind == "app1_exif"]


@pytest.mark.parametrize("tier", ["F1", "F2", "F3"])
def test_two_rotated_photos_differing_only_in_metadata_come_out_identical(tier):
    a = TIERS[tier].scrub(photo(6, "SENTINEL-ALICE"))
    b = TIERS[tier].scrub(photo(6, "SENTINEL-CAROL"))
    assert a == b


def test_a_non_canonical_orientation_segment_is_a_residual():
    out = bytearray(f1.scrub(photo(6)))
    i = out.index(b"Exif\x00\x00") + 6 + 8 + 2 + 8
    out[i:i + 2] = b"\x00\x09"                          # an out-of-range value
    assert f1.residuals(bytes(out))
