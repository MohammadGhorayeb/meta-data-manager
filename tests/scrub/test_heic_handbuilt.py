"""M5 — HEIC's hard paths, on a file CI can build.

Thirteen tests in `test_heic.py` needed a real iPhone photo, and they were exactly
the ones covering the findings that made HEIC interesting: the tiled `grid`, the
`idat`-relative primary item, the auxiliary images, the thumbnail, Apple's
segmentation blob and the `ipco` orphan-property pruning. A `pillow-heif` file has
none of those — one tile, no grid, no `idat` — so the format's *core* was
regression-tested everywhere and its hard paths only where photos exist.

`heic_corpus.handbuilt()` closes that: it writes the container byte by byte, sharing
no code with the scrubber, with real HEVC tiles inside it so the acceptance test can
still **decode** rather than merely parse. Everything here runs on CI.

The one thing it cannot be is a photograph, so the real-photo tests stay: this file
proves the code handles the shapes, those prove the shapes are what a camera writes.
"""
from __future__ import annotations

import os
import shutil
import struct
import subprocess

import pytest

from src.scrub.errors import ScrubError
from src.scrub.formats.heic import f1
from src.scrub.formats.heic import walker as w

from . import heic_corpus as hc

pytestmark = pytest.mark.skipif(not hc.HAVE_HEIF,
                                reason="pillow-heif absent (no HEVC encoder)")


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> bytes:
    path = str(tmp_path_factory.mktemp("heic_hb") / "hand.heic")
    return open(hc.handbuilt(path), "rb").read()


@pytest.fixture(scope="module")
def scrubbed(built) -> bytes:
    return f1.scrub(built)


def _grids(data: bytes) -> dict[int, tuple[int, int, int, int]]:
    """Each `idat`-stored item's grid descriptor, read back through its OWN `iloc`
    offset: (rows, columns, width, height)."""
    layout = w.walk(data)
    idat = layout.idat.payload if layout.idat else b""
    out = {}
    for item_id, item in layout.items.items():
        if item.in_file or not item.extents:
            continue
        extent = item.extents[0]
        blob = idat[extent.idat_offset:extent.idat_offset + extent.length]
        if len(blob) < 8:
            continue
        rows, cols = blob[2] + 1, blob[3] + 1
        width, height = struct.unpack_from(">HH", blob, 4)
        out[item_id] = (rows, cols, width, height)
    return out


# --------------------------------------------------------------------------- #
# The corpus is a second implementation, and has to stay one
# --------------------------------------------------------------------------- #
def test_the_corpus_shares_no_code_with_the_scrubber():
    """The `pdf_corpus.py` rule. If the builder called our own writer, a
    misunderstanding of `iloc` would cancel itself out and every test below would
    agree with the bug instead of catching it."""
    import ast
    tree = ast.parse(open(hc.__file__).read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not [m for m in imported if m.split(".")[0] == "src"], imported


def test_it_is_the_shape_a_synthetic_file_cannot_be(built):
    """The four gaps this file exists to close, asserted as facts about the input
    rather than assumed."""
    layout = w.walk(built)
    primary = layout.items[layout.primary_id]
    assert primary.item_type == b"grid"
    assert primary.construction == w.CONSTRUCTION_IDAT, "bytes live in `idat`"
    assert primary.file_extents == []
    assert len(layout.derived_inputs()[layout.primary_id]) == 4, "a tiled picture"
    assert len(layout.auxiliary_ids()) == 3
    assert layout.thumbnail_ids()
    assert layout.by_type(b"uri "), "the segmentation-blob item"
    assert {i.item_type for i in layout.metadata_items} == {b"Exif", b"mime", b"uri "}


# --------------------------------------------------------------------------- #
# Content preservation — decoded, not merely parsed
# --------------------------------------------------------------------------- #
def test_the_composed_picture_survives(built, scrubbed, tmp_path):
    """libheif composes the 2x2 grid from four separate items. If `iloc` is wrong by
    one byte the tiles land in the wrong order or decode to noise, and the file still
    parses perfectly — the M4A lesson, which is why this decodes."""
    before = tmp_path / "before.heic"
    after = tmp_path / "after.heic"
    before.write_bytes(built)
    after.write_bytes(scrubbed)
    a, b = hc.decoded_pixels(str(before)), hc.decoded_pixels(str(after))
    assert a, "the decoder could not read the hand-built file at all"
    assert a == b
    assert len(a) == 128 * 128 * 3, "the grid was composed, not just one tile"


# --------------------------------------------------------------------------- #
# The leak M5 found: a tiled auxiliary image is not its 8-byte descriptor
# --------------------------------------------------------------------------- #
def test_dropping_a_tiled_auxiliary_image_drops_its_tiles(built, scrubbed):
    """An auxiliary image on an iPhone is a grid: `iloc` points at 8 bytes and the
    picture is in twelve separate items. Removing only the item named in `iinf` left
    every tile in `mdat` — 381 KB of a 1.17 MB photo — with nothing naming them, so
    every tag-based check called the file clean."""
    before, after = w.walk(built), w.walk(scrubbed)
    matte_tiles = set(before.derived_inputs()[hc._GRID_MATTE])
    assert matte_tiles, "control: the matte is tiled in the input"

    assert hc._GRID_MATTE not in after.items
    assert not (matte_tiles & set(after.items)), "the matte's tiles outlived it"


def test_nothing_survives_that_nothing_composes(scrubbed):
    """The general form, and the check that would have caught it.

    Connectivity, not naming, and deliberately undirected: deleting a tiled image
    removes the very `dimg` reference that made its tiles tiles, so a directed walk
    then reads the abandoned tiles as top-level items and calls the file clean. The
    question that survives the deletion is whether anything still relates these bytes
    to the photograph.
    """
    layout = w.walk(scrubbed)
    assert set(layout.items) == layout.connected_to(layout.primary_id)
    assert f1.residuals(scrubbed) == []


def test_the_hdr_gain_map_and_its_tiles_are_kept_together(built, scrubbed):
    """The one auxiliary image kept (limit #32) — and it has to keep its tiles, or
    what survives is a descriptor pointing at nothing."""
    before, after = w.walk(built), w.walk(scrubbed)
    gain_tiles = set(before.derived_inputs()[hc._GRID_GAIN])

    assert hc._GRID_GAIN in after.items
    assert gain_tiles <= set(after.items)
    assert [f1._aux_kind(a) for a in after.aux_types.values()] == ["hdrgainmap"]


def test_an_untiled_auxiliary_image_goes_too(built, scrubbed):
    """The single-item case, so the graph rule is not quietly only correct for
    grids."""
    assert hc._ITEM_MATTE_FLAT in w.walk(built).items
    assert hc._ITEM_MATTE_FLAT not in w.walk(scrubbed).items


# --------------------------------------------------------------------------- #
# `idat`: the offset trap, one level below `mdat`
# --------------------------------------------------------------------------- #
def test_every_surviving_grid_still_reads_its_own_descriptor(built, scrubbed):
    """`iloc` wrote **0** for every `idat`-stored item, because the model recorded -1
    and the writer clamped it. The primary grid survived that — it is written first —
    and everything beside it read the primary's descriptor instead of its own.

    The three grids here are deliberately different shapes, so a zeroed offset is a
    wrong answer rather than a lucky one.
    """
    assert _grids(built)[hc._GRID_PRIMARY] == (2, 2, 128, 128)
    assert _grids(built)[hc._GRID_GAIN] == (1, 2, 128, 64)

    after = _grids(scrubbed)
    assert after[hc._GRID_PRIMARY] == (2, 2, 128, 128)
    assert after[hc._GRID_GAIN] == (1, 2, 128, 64), "the gain map read the wrong bytes"


def test_idat_is_repacked_not_copied_whole(built, scrubbed):
    """`idat` is where grid descriptors live, so copying it through left the
    description of every removed auxiliary grid in the output — addressable by anyone
    who reads `iloc` rather than `iinf`."""
    before, after = w.walk(built), w.walk(scrubbed)
    assert len(after.idat.payload) < len(before.idat.payload)
    assert hc._GRID_MATTE not in _grids(scrubbed)
    # No ghost entries: every id in `iloc` is still described in `iinf`.
    assert all(item.item_type for item in after.items.values())


# --------------------------------------------------------------------------- #
# The other tables that name items by id
# --------------------------------------------------------------------------- #
def test_an_entity_group_stops_naming_what_was_removed(built, scrubbed):
    """`grpl` is a fourth table of item ids, and it was copied through verbatim."""
    def group_ids(data: bytes) -> list[int]:
        layout = w.walk(data)
        grpl = next((c for c in layout.meta.children if c.type == b"grpl"), None)
        assert grpl is not None
        out = []
        for box in w.children_of(grpl):
            count = struct.unpack_from(">I", box.payload, 8)[0]
            out += [struct.unpack_from(">I", box.payload, 12 + 4 * k)[0]
                    for k in range(count)]
        return out

    assert hc._GRID_MATTE in group_ids(built), "control"
    assert group_ids(scrubbed) == [hc._GRID_PRIMARY]


def test_no_reference_points_at_a_removed_item(scrubbed):
    layout = w.walk(scrubbed)
    for ref in layout.references:
        assert ref.from_id in layout.items, ref
        assert all(t in layout.items for t in ref.to_ids), ref


def test_orphaned_properties_are_pruned_and_renumbered(built, scrubbed):
    """Properties are shared BY INDEX, so pruning `ipco` means renumbering every
    surviving association — and skipping it left a real photo still advertising
    `semanticskinmatte` after the matte was gone."""
    def props(data: bytes) -> list[bytes]:
        layout = w.walk(data)
        iprp = next(c for c in layout.meta.children if c.type == b"iprp")
        ipco = next(c for c in w.children_of(iprp) if c.type == b"ipco")
        return [c.type for c in w.children_of(ipco)]

    assert props(built).count(b"auxC") == 3
    assert props(scrubbed).count(b"auxC") == 1, "a removed image's type is advertised"
    for gone in (hc.AUX_MATTE_TILED.encode(), hc.AUX_MATTE_FLAT.encode()):
        assert gone not in scrubbed
    assert hc.AUX_GAIN.encode() in scrubbed, "the kept image keeps its type"

    # ...and the surviving associations still point at properties that exist.
    layout = w.walk(scrubbed)
    iprp = next(c for c in layout.meta.children if c.type == b"iprp")
    kids = w.children_of(iprp)
    count = len(w.children_of(next(c for c in kids if c.type == b"ipco")))
    ipma = next(c for c in kids if c.type == b"ipma")
    _v, _f, entries = w.parse_ipma(ipma.payload)
    for item_id, assoc in entries:
        assert item_id in layout.items
        for index, _essential in assoc:
            assert 1 <= index <= count, (item_id, index)


# --------------------------------------------------------------------------- #
# The metadata itself
# --------------------------------------------------------------------------- #
def test_the_thumbnail_and_every_metadata_item_go(built, scrubbed):
    assert w.walk(built).thumbnail_ids(), "control"
    layout = w.walk(scrubbed)
    assert layout.thumbnail_ids() == set()
    assert layout.metadata_items == []
    assert hc.SENTINEL.encode() not in scrubbed
    assert b"bplist00" not in scrubbed, "the segmentation blob"


def test_exiftool_agrees_that_the_metadata_is_gone(scrubbed, tmp_path):
    """The measuring stick, not our own reader — the cross-check that found the
    `semanticskinmatte` advertisement in the first place."""
    if shutil.which("exiftool") is None:
        pytest.skip("exiftool not installed")
    path = tmp_path / "out.heic"
    path.write_bytes(scrubbed)
    proc = subprocess.run(["exiftool", "-a", "-u", "-G", str(path)],
                          capture_output=True, text=True)
    for banned in ("TestCam", "MZ-1", hc.SENTINEL, "GPSLatitude", "XMP"):
        assert banned not in proc.stdout, banned


# --------------------------------------------------------------------------- #
# Fail closed
# --------------------------------------------------------------------------- #
def test_scrubbing_twice_changes_nothing(scrubbed):
    """F1 is bit-preserving, so its output is a fixed point (limit #29)."""
    assert f1.scrub(scrubbed) == scrubbed


@pytest.mark.parametrize("cut", [0.25, 0.5, 0.9])
def test_a_truncated_file_is_refused(built, cut):
    with pytest.raises(ScrubError):
        f1.scrub(built[: int(len(built) * cut)])


def test_an_idat_extent_past_the_end_of_idat_is_refused(built):
    """`idat` extents were bounds-checked against nothing at all: an offset past the
    end of the box read whatever followed it, or silently returned short."""
    layout = w.walk(built)
    iloc = next(c for c in layout.meta.children if c.type == b"iloc")
    # The primary grid is the first entry; its offset field sits after
    # item_id(2) + method(2) + data_ref(2) + extent_count(2).
    at = iloc.offset + iloc.header_len + 4 + 2 + 2 + 2 + 2 + 2 + 2
    broken = bytearray(built)
    struct.pack_into(">I", broken, at, 0xFFFF)
    with pytest.raises(ScrubError):
        f1.scrub(bytes(broken))


def test_the_report_names_the_hard_structures(built, scrubbed):
    from src.scrub.formats.heic.handler import HeicHandler
    described = HeicHandler().describe(built)
    assert any(k.startswith("EXIF:") for k in described)
    assert "embedded thumbnail" in described
    assert "auxiliary images" in described
    # The aux line counts what the images COMPOSE, not the 8-byte descriptors:
    # understating it by four orders of magnitude is how the tiles got left behind.
    # Two removable auxiliary images (the tiled matte and the flat one); the gain
    # map is reported on its own line because it is kept on purpose.
    # 4 items: the tiled matte's descriptor, its two tiles, and the flat matte.
    assert described["auxiliary images"].startswith("2 depth/matte image(s) in 4 ")
    assert "HDR gain map" in described

    after = HeicHandler().describe(scrubbed)
    assert not any(k.startswith("EXIF:") for k in after)
    assert "auxiliary images" not in after
    assert "HDR gain map" in after, "what we kept has to stay in the report"


def test_the_cli_routes_and_scrubs_a_hand_built_file(built, tmp_path):
    """End to end through dispatch, so identification is covered too."""
    from src.scrub import cli
    src, dst = tmp_path / "in.heic", tmp_path / "out.heic"
    src.write_bytes(built)
    cli.scrub_file(str(src), str(dst), "F1")
    assert f1.residuals(dst.read_bytes()) == []
    assert os.path.getsize(dst) < os.path.getsize(src)
