"""Unit tests for the shared standard modules."""
from __future__ import annotations

import pytest

from src.scrub.errors import ParseError
from src.scrub.standards import icc, iptc_iim, tiff_ifd, xmp
from tests.scrub import corpus


# --------------------------- tiff_ifd --------------------------------------- #
def test_tiff_ifd_walks_all_ifds_and_thumbnail():
    payload = corpus.make_exif_payload(with_gps=True, with_thumb=True)
    assert tiff_ifd.has_exif_prefix(payload)
    tiff = tiff_ifd.strip_exif_prefix(payload)
    tree = tiff_ifd.parse(tiff, strict=True)

    names = {ifd.name for ifd in tree.ifds}
    assert "IFD0" in names
    assert "ExifIFD" in names
    assert "GPSIFD" in names       # GPS sub-IFD reached
    assert "IFD1" in names         # thumbnail IFD reached via next-IFD pointer
    assert tree.thumbnails, "IFD1 thumbnail not located"

    loc_names = {l.name for l in tiff_ifd.loci(tree)}
    assert any("thumbnail" in n for n in loc_names)
    assert any("GPSIFD" in n for n in loc_names)


def test_tiff_ifd_rejects_bad_header():
    with pytest.raises(ParseError):
        tiff_ifd.parse(b"XXXX\x00\x00\x00\x08", strict=True)


def test_tiff_ifd_nonstrict_truncates_instead_of_raising():
    tiff = tiff_ifd.strip_exif_prefix(corpus.make_exif_payload())
    # corrupt the IFD0 offset to point past EOF
    bad = tiff[:4] + (len(tiff) + 100).to_bytes(4, "little") \
        if tiff[:2] == b"II" else tiff[:4] + (len(tiff) + 100).to_bytes(4, "big")
    tree = tiff_ifd.parse(bad, strict=False)
    assert tree.truncated


# ------------------------------- xmp ---------------------------------------- #
def test_xmp_standard_and_extended_detection():
    std = corpus.xmp_app1()[4:]           # strip FF E1 + length -> payload
    assert xmp.is_standard(std)
    assert not xmp.is_extended(std)

    ext_payload = corpus.ext_xmp_app1(guid=b"B" * 32, full_len=6, offset=0,
                                      data=b"ABCDEF")[4:]
    assert xmp.is_extended(ext_payload)
    chunk = xmp.parse_extended_segment(ext_payload)
    assert chunk.guid == b"B" * 32
    assert chunk.full_length == 6
    assert chunk.data == b"ABCDEF"


def test_xmp_extended_reassembles_by_offset():
    c1 = xmp.ExtendedChunk(guid=b"G" * 32, full_length=6, offset=3, data=b"DEF")
    c2 = xmp.ExtendedChunk(guid=b"G" * 32, full_length=6, offset=0, data=b"ABC")
    out = xmp.reassemble_extended([c1, c2])
    assert out[b"G" * 32] == b"ABCDEF"   # ordered by offset, not arrival


def test_xmp_find_packet_span():
    buf = b"junk<?xpacket begin='x'?>DATA<?xpacket end='w'?>tail"
    span = xmp.find_packet_span(buf)
    assert span is not None
    start, end = span
    assert buf[start:end].startswith(b"<?xpacket begin")
    assert buf[start:end].endswith(b"?>")


# ------------------------------- icc ---------------------------------------- #
def test_icc_header_parse_and_sanitize():
    prof = corpus.fake_icc_profile(creator=b"SECR")
    hdr = icc.parse_header(prof)
    assert hdr.valid_signature
    assert hdr.creator == b"SECR"
    assert hdr.manufacturer == b"APPL"

    clean = icc.sanitize(prof)
    ch = icc.parse_header(clean)
    assert ch.creator == b"\x00\x00\x00\x00"
    assert ch.manufacturer == b"\x00\x00\x00\x00"
    # profile ID recomputed and self-consistent
    assert ch.profile_id == icc.compute_profile_id(clean)
    assert ch.profile_id != b"\x00" * 16


def test_icc_reassemble_jpeg_app2():
    prof = corpus.fake_icc_profile()
    half = len(prof) // 2
    seg1 = icc.JPEG_ICC_SIG + bytes([1, 2]) + prof[:half]
    seg2 = icc.JPEG_ICC_SIG + bytes([2, 2]) + prof[half:]
    # order-independent reassembly
    assert icc.reassemble_jpeg_app2([seg2, seg1]) == prof


def test_icc_reassemble_detects_missing_chunk():
    prof = corpus.fake_icc_profile()
    seg1 = icc.JPEG_ICC_SIG + bytes([1, 2]) + prof
    with pytest.raises(ValueError):
        icc.reassemble_jpeg_app2([seg1])   # says total=2 but only chunk 1 given


# ----------------------------- iptc_iim ------------------------------------- #
def test_iptc_8bim_parse_finds_iptc_and_thumbnail():
    payload = corpus.photoshop_app13()[4:]   # strip FF ED + length
    resources = iptc_iim.parse_8bim(payload)
    ids = {r.id for r in resources}
    assert iptc_iim.IPTC_RESOURCE_ID in ids
    assert any(r.id in iptc_iim.THUMBNAIL_RESOURCE_IDS for r in resources)

    iptc_res = iptc_iim.find_iptc(resources)[0]
    datasets = iptc_iim.iptc_datasets(iptc_res.data)
    assert datasets and datasets[0].value == b"secret location caption"
