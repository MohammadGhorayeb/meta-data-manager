"""M2 — the HEIC walker: the item model, and what it refuses.

M4A taught this project the ISOBMFF *box* layer. HEIC adds an *item* layer on top,
and everything hard about the format lives there: the photograph is a grid of 61-95
HEVC tiles, and alongside it sit auxiliary images, metadata blobs and a thumbnail,
all addressed by absolute offset out of one `iloc` table.

The corpus is six real iPhone photos, which are git-ignored and macOS-local, so every
test here skips cleanly without them rather than silently testing nothing — the
limit-#12 precedent. The synthetic cases below run everywhere.
"""
from __future__ import annotations

import glob
import os
import struct

import pytest

from src.scrub.errors import ParseError
from src.scrub.formats.heic import walker as w

REAL = ([] if os.environ.get("SCRUB_IGNORE_REAL_SAMPLES")
        else sorted(glob.glob("metadata-research/step2/*.HEIC")))
needs_real = pytest.mark.skipif(
    not REAL, reason="no real HEIC corpus on this machine (see p4 plan §W5)")


@pytest.fixture(scope="module")
def sample() -> bytes:
    if not REAL:
        pytest.skip("no HEIC corpus")
    return open(REAL[0], "rb").read()


# --------------------------------------------------------------------------- #
# Identification: `ftyp` is shared by every ISOBMFF file
# --------------------------------------------------------------------------- #
def test_the_brand_decides_not_the_ftyp_prefix(tmp_path):
    """M4A, MP4 and HEIC all begin `....ftyp`. Only the brand separates them, and
    claiming a sibling would route an audio file into an image handler."""
    from . import m4a_corpus as mc
    if mc.HAVE_FFMPEG:
        m4a = open(mc.torture_m4a(str(tmp_path / "a.m4a")), "rb").read()
        assert w.brand(m4a) == b"M4A "
        assert not w.looks_like_heic(m4a)

    fake_mp4 = struct.pack(">I", 20) + b"ftypisom" + b"\x00" * 8
    assert not w.looks_like_heic(fake_mp4)


def test_a_heic_brand_is_claimed():
    for major in (b"heic", b"heix", b"mif1"):
        blob = struct.pack(">I", 16) + b"ftyp" + major + b"\x00\x00\x00\x00"
        assert w.looks_like_heic(blob), major


def test_a_generic_major_brand_with_a_heic_compatible_brand_is_claimed():
    """A file may declare something generic up front and list the real brand in the
    compatible-brands array that follows."""
    blob = struct.pack(">I", 24) + b"ftyp" + b"mp42" + b"\x00\x00\x00\x00" \
        + b"mp42" + b"heic"
    assert w.looks_like_heic(blob)


def test_identification_never_raises_on_garbage():
    """Dispatch asks every handler in turn, so a crash here takes down dispatch for
    every other format."""
    for junk in (b"", b"ftyp", b"\x00\x00\x00\x08ftyp", b"not a file", b"\xff" * 40):
        assert w.looks_like_heic(junk) is False


# --------------------------------------------------------------------------- #
# The item model, on real files
# --------------------------------------------------------------------------- #
@needs_real
@pytest.mark.parametrize("path", REAL, ids=[p.split("/")[-1] for p in REAL])
def test_every_real_photo_parses(path):
    layout = w.walk(open(path, "rb").read())
    assert layout.items
    assert layout.primary_id in layout.items
    # The photograph is a grid of tiles, not a single image — the fact that shapes
    # everything else about this format.
    assert layout.items[layout.primary_id].item_type == b"grid"
    assert len(layout.by_type(b"hvc1")) > 10


@needs_real
def test_the_metadata_items_are_found(sample):
    """Exif, XMP and Apple's plist, which is what F1 will have to remove."""
    layout = w.walk(sample)
    kinds = {i.item_type for i in layout.metadata_items}
    assert b"Exif" in kinds
    assert b"mime" in kinds                 # XMP
    assert all(i.size > 0 for i in layout.metadata_items)


@needs_real
def test_the_thumbnail_is_found(sample):
    """A second picture of the same scene — the Phase 1 lesson in a new container."""
    layout = w.walk(sample)
    thumbs = layout.thumbnail_ids()
    assert thumbs, "no thmb reference found"
    assert all(t in layout.items for t in thumbs)


@needs_real
def test_auxiliary_images_are_identified_separately_from_the_photo(sample):
    """Depth maps and semantic mattes are `auxl`-referenced images. They are not the
    photograph, and F1 has to be able to tell the difference before deciding."""
    layout = w.walk(sample)
    aux = layout.auxiliary_ids()
    assert aux
    assert layout.primary_id not in aux


@needs_real
def test_the_exif_item_parses_with_the_existing_tiff_reader(sample):
    """The reuse that made HEIC the cheap next step: no new EXIF code at all.

    `standards/tiff_ifd` and `tiff_values` were written for JPEG in Phase 1 and read
    a HEIC's EXIF item unmodified.
    """
    from src.scrub.standards import tiff_ifd, tiff_values
    layout = w.walk(sample)
    exif = layout.by_type(b"Exif")[0]
    extent = exif.file_extents[0]
    blob = sample[extent.offset:extent.end]
    # The item payload begins with a 4-byte offset to the TIFF header.
    tiff = blob[4:]
    if tiff[:6] == b"Exif\x00\x00":
        tiff = tiff[6:]
    tree = tiff_ifd.parse(tiff)
    named = {tiff_values.tag_name(ifd.name, e.tag): e
             for ifd in tree.ifds for e in ifd.entries
             if tiff_values.tag_name(ifd.name, e.tag)}
    assert "Make" in named and "Model" in named


# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #
def test_a_non_heif_brand_is_refused():
    blob = struct.pack(">I", 16) + b"ftyp" + b"isom" + b"\x00\x00\x00\x00"
    with pytest.raises(ParseError, match="not a HEIF"):
        w.walk(blob)


@needs_real
def test_an_item_whose_extent_runs_past_eof_is_refused(sample):
    """Truncation must be refused, not read short: an item we mis-locate is one we
    either fail to remove or remove from the middle of the photograph."""
    with pytest.raises(ParseError):
        w.walk(sample[: len(sample) // 2])


@needs_real
def test_idat_relative_items_are_modelled_not_refused(sample):
    """The first version of this walker refused construction method 1 and rejected
    all six corpus files.

    Apple writes the `grid` item — including the PRIMARY item — with its bytes inside
    `idat` rather than at a file offset. Those extents carry no file position, so
    anything that moves bytes must skip them; the model records that rather than
    pretending they are regions of the file.
    """
    layout = w.walk(sample)
    primary = layout.items[layout.primary_id]
    assert primary.construction == w.CONSTRUCTION_IDAT
    assert not primary.in_file
    assert primary.file_extents == []

    # Every item that IS file-addressed has real, in-bounds extents.
    for item in layout.items.values():
        if item.in_file:
            for extent in item.file_extents:
                assert 0 <= extent.offset < extent.end <= len(sample)
