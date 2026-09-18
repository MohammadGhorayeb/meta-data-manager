"""M4 — HEIC F1: drop the metadata items, rebuild the tables that locate everything.

The acceptance test for this format is **not** that the output parses. M4A taught the
lesson: a wrong offset table leaves a file that walks perfectly, reports the right
dimensions, and decodes to garbage. So the content check decodes the image through
`pillow-heif` — an implementation that shares no code with our walker — and compares
pixels.
"""
from __future__ import annotations

import pytest

from src.scrub.formats.heic import f1
from src.scrub.formats.heic import walker as w

from . import heic_corpus as hc

REAL = hc.real_samples()
needs_heif = pytest.mark.skipif(not hc.HAVE_HEIF, reason="pillow-heif absent")
needs_real = pytest.mark.skipif(
    not REAL, reason="no real camera HEICs here (see p4 plan §W5)")


@pytest.fixture
def torture(tmp_path) -> bytes:
    if not hc.HAVE_HEIF:
        pytest.skip("pillow-heif absent")
    return open(hc.torture(str(tmp_path / "t.heic")), "rb").read()


# --------------------------------------------------------------------------- #
# Content preservation — decoded, not merely parsed
# --------------------------------------------------------------------------- #
@needs_heif
def test_the_pixels_survive(torture):
    """Through an independent decoder, so a match is evidence rather than us agreeing
    with ourselves."""
    before = hc.decoded_pixels(torture)
    after = hc.decoded_pixels(f1.scrub(torture))
    assert before and after
    assert before == after


@needs_real
@needs_heif
@pytest.mark.parametrize("path", REAL, ids=[p.split("/")[-1] for p in REAL])
def test_real_camera_photos_survive_bit_for_bit_in_pixel_space(path):
    """The files a synthetic corpus cannot imitate: a 61-to-95-tile grid, six
    auxiliary images, a thumbnail, and Apple's segmentation plist."""
    data = open(path, "rb").read()
    out = f1.scrub(data)
    before, after = hc.decoded_pixels(data), hc.decoded_pixels(out)
    assert before and after, "the decoder could not read one side"
    assert before == after
    assert len(out) < len(data)


# --------------------------------------------------------------------------- #
# The metadata is gone
# --------------------------------------------------------------------------- #
@needs_heif
def test_every_metadata_item_is_removed(torture):
    out = f1.scrub(torture)
    assert w.walk(out).metadata_items == []
    assert f1.residuals(out) == []
    assert hc.SENTINEL.encode() not in out


@needs_real
def test_the_camera_and_the_place_are_gone():
    """What a person actually came to remove."""
    data = open(REAL[0], "rb").read()
    out = f1.scrub(data)
    for secret in (b"iPhone", b"Apple iOS"):
        assert secret in data, f"control: {secret!r} should be in the original"
        assert secret not in out, f"{secret!r} survived"


@needs_real
def test_the_segmentation_blob_goes(tmp_path):
    """Apple ships a description of the photo's *subject* — `PeopleRatio`,
    `SkinRatio` and a raster matte of where the people are — in a `uri ` item worth
    3-5% of the file. It is metadata about content, which is a different species from
    EXIF, and it goes."""
    with_blob = [p for p in REAL
                 if any(i.item_type == b"uri " for i in
                        w.walk(open(p, "rb").read()).items.values())]
    if not with_blob:
        pytest.skip("no sample in this corpus carries the segmentation item")
    data = open(with_blob[0], "rb").read()
    assert b"bplist00" in data, "control"
    out = f1.scrub(data)
    assert not [i for i in w.walk(out).items.values() if i.item_type == b"uri "]


@needs_real
def test_the_thumbnail_goes(tmp_path):
    """A second picture of the same scene — the Phase 1 lesson in a new container."""
    data = open(REAL[0], "rb").read()
    assert w.walk(data).thumbnail_ids(), "control: the original has a thumbnail"
    assert not w.walk(f1.scrub(data)).thumbnail_ids()


@needs_real
def test_an_orphaned_property_does_not_advertise_what_was_removed():
    """Properties are shared by index, so pruning `ipco` means renumbering every
    surviving association — and skipping that left a scrubbed photo still announcing

        AuxiliaryImageType: urn:com:apple:photo:2019:aux:semanticskinmatte

    after the matte itself was gone. The picture no longer carried Apple's
    person-segmentation output but still said the segmentation had run.
    """
    out = f1.scrub(open(REAL[0], "rb").read())
    for gone in (b"semanticskinmatte", b"semanticskymatte", b"portraiteffectsmatte",
                 b"styledeltamap", b"linearthumbnail"):
        assert gone not in out, gone

    # The only auxiliary image left is the HDR gain map, which is kept on purpose
    # (limit #32), so the check is that nothing ELSE is still announced rather than
    # that the string `aux:` is absent.
    surviving = set(w.walk(out).aux_types.values())
    assert {f1._aux_kind(a) for a in surviving} <= f1.KEEP_AUX_KINDS, surviving


@needs_real
def test_the_colour_profile_keeps_its_colours_and_loses_its_provenance():
    """Limit #14, in a third format.

    The ICC *header* names the device, the creator and the build date, and is zeroed.
    The tag data is the colour transform itself and is not touched, because rewriting
    it would change the picture — which is why `Copyright Apple Inc.` survives inside
    it and the report's cross-check is told to expect it there.

    Checked on the profile itself rather than on a string in the file: ExifTool
    renders `Display P3` from a structured `desc` tag, so it is not a literal in the
    bytes and searching for one tests nothing.
    """
    from src.scrub.standards import icc, isobmff

    out = f1.scrub(open(REAL[0], "rb").read())
    # `ipco` is not in the shared CONTAINERS set, so a plain `walk()` does not
    # descend into it -- the same fact that caused the empty-`iprp` bug. Use the
    # module's own helper rather than reproducing the mistake in the test.
    meta = next(b for b in isobmff.parse(out) if b.type == b"meta")
    iprp = next(c for c in meta.children if c.type == b"iprp")
    ipco = next(c for c in f1._children_of(iprp) if c.type == b"ipco")
    profiles = [c.payload[4:] for c in f1._children_of(ipco)
                if c.type == b"colr" and c.payload[:4] in (b"prof", b"rICC")]
    assert profiles, "every colour profile was dropped — the picture would shift"

    for blob in profiles:
        header = icc.parse_header(blob)
        assert header is not None and header.valid_signature, "profile is now invalid"
        # Provenance zeroed...
        assert header.manufacturer.strip(b"\x00") == b""
        assert header.creator.strip(b"\x00") == b""
        assert header.datetime_raw.strip(b"\x00") == b""
        # ...while the colour data itself is still there to render with.
        assert len(blob) > icc.HEADER_LEN


# --------------------------------------------------------------------------- #
# Identification and refusals
# --------------------------------------------------------------------------- #
def test_dispatch_routes_heic_without_stealing_m4a(tmp_path):
    """Both start `....ftyp`; only the brand separates them, and the M4A handler is
    registered first so its narrower claim runs first."""
    from src.scrub.dispatch import default_dispatcher

    from . import m4a_corpus as mc
    dispatcher = default_dispatcher()

    if hc.HAVE_HEIF:
        data = open(hc.synthetic(str(tmp_path / "s.heic")), "rb").read()
        assert dispatcher.resolve(data).format_id == "heic"
    if mc.HAVE_FFMPEG:
        audio = open(mc.torture_m4a(str(tmp_path / "a.m4a")), "rb").read()
        assert dispatcher.resolve(audio).format_id == "m4a"


@needs_heif
def test_a_truncated_file_is_refused_not_read_short(tmp_path):
    from src.scrub.errors import ScrubError
    data = open(hc.torture(str(tmp_path / "t.heic")), "rb").read()
    with pytest.raises(ScrubError):
        f1.scrub(data[: len(data) // 2])


@needs_heif
def test_scrubbing_twice_changes_nothing(tmp_path):
    """F1 is bit-preserving, so its output must be a fixed point (limit #29)."""
    data = open(hc.torture(str(tmp_path / "t.heic")), "rb").read()
    once = f1.scrub(data)
    assert f1.scrub(once) == once


@needs_heif
def test_the_report_names_what_it_removed(tmp_path):
    from src.scrub.formats.heic.handler import HeicHandler
    data = open(hc.torture(str(tmp_path / "t.heic")), "rb").read()
    described = HeicHandler().describe(data)
    assert any(k.startswith("EXIF:") for k in described)
    assert described != HeicHandler().describe(f1.scrub(data))
