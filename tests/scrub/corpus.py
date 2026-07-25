"""Synthetic image-corpus builders for the P1 unit tests.

Two philosophies (harness README §3), both used:
  - **crafted marker streams** for walker/standards soundness — a bug is in our
    code, not a library quirk;
  - a **real, decodable** Pillow+piexif base with extra metadata segments bolted
    on, so we can assert *pixel identity* after scrubbing while still exercising
    every locus (Exif+GPS+thumbnail, ICC, COM, XMP, ExtendedXMP, MPF, IPTC/8BIM,
    trailer).

Extra APPn segments don't affect JPEG decoding, so the torture file stays
decodable — which is what lets `test_f1` prove content preservation.
"""
from __future__ import annotations

import io
import os
import struct

import piexif
from PIL import Image

from src.scrub.formats.jpeg import segments as _seg
from src.scrub.standards import icc as icc_mod
from src.scrub.standards import xmp as xmp_mod

# --------------------------------------------------------------------------- #
# Real, decodable base image
# --------------------------------------------------------------------------- #
def make_pixels(w: int = 64, h: int = 48, seed: int = 0) -> Image.Image:
    """A gradient + deterministic pseudo-noise so pHash has texture (F3 later).
    `seed` varies the pattern for building distinct peer/diverse images."""
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = ((x * 5 + seed * 11) % 256,
                        (y * 7 + seed * 13) % 256,
                        ((x + y) * 3 + seed * 17) % 256)
    return img


def fake_icc_profile(creator: bytes = b"SECR") -> bytes:
    """A minimal but structurally valid ICC profile carrying identifying fields
    (creator/manufacturer sentinels) so tests can prove it was present and then
    dropped."""
    prof = bytearray(140)
    struct.pack_into(">I", prof, 0, len(prof))   # profile size
    prof[4:8] = b"APPL"                            # preferred CMM
    prof[8:12] = bytes([0x02, 0x40, 0, 0])        # version 2.4
    prof[12:16] = b"mntr"                          # device class
    prof[16:20] = b"RGB "                          # colour space
    prof[20:24] = b"XYZ "                          # PCS
    prof[24:36] = bytes(12)                        # datetime
    prof[36:40] = b"acsp"                          # signature
    prof[40:44] = b"APPL"                          # platform
    prof[48:52] = b"APPL"                          # manufacturer
    prof[52:56] = b"MZ01"                          # model
    prof[80:84] = creator                          # creator sentinel
    return bytes(prof)


def make_exif_payload(with_gps: bool = True, with_thumb: bool = True) -> bytes:
    """APP1-Exif payload (starts with b'Exif\\0\\0') via piexif, including a GPS
    IFD and an IFD1 thumbnail with its own tags — the classic missed loci."""
    zeroth = {
        piexif.ImageIFD.Make: b"TestCam",
        piexif.ImageIFD.Model: b"MZ-1",
        piexif.ImageIFD.Software: b"secret-app 1.0",
    }
    exif = {piexif.ExifIFD.DateTimeOriginal: b"2020:01:01 12:00:00"}
    gps = {}
    if with_gps:
        gps = {
            piexif.GPSIFD.GPSLatitudeRef: b"N",
            piexif.GPSIFD.GPSLatitude: [(51, 1), (30, 1), (0, 1)],
            piexif.GPSIFD.GPSLongitudeRef: b"W",
            piexif.GPSIFD.GPSLongitude: [(0, 1), (7, 1), (0, 1)],
        }
    first = {}
    thumb = None
    if with_thumb:
        t = Image.new("RGB", (16, 12), (10, 20, 30))
        b = io.BytesIO()
        t.save(b, format="JPEG", quality=70)
        thumb = b.getvalue()
        first = {piexif.ImageIFD.Make: b"TestCam"}
    d = {"0th": zeroth, "Exif": exif, "GPS": gps, "1st": first, "thumbnail": thumb}
    return piexif.dump(d)


def _content_only(data: bytes) -> bytes:
    """Keep just the image-defining segments of a JPEG (drop all metadata), so we
    can rebuild a fully-controlled corpus on top. Uses the walker, not f1, to
    stay non-circular with the code under test."""
    s = _seg.walk(data)
    out = bytearray()
    for sg in s.segments:
        if sg.is_content:
            out += data[sg.offset:sg.end]
    return bytes(out)


def _plain_jpeg(quality: int = 92) -> bytes:
    buf = io.BytesIO()
    make_pixels().save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def make_base_jpeg(quality: int = 92) -> bytes:
    """Decodable JPEG assembled deterministically: content-only base + a
    non-canonical JFIF APP0, Exif APP1 (GPS + thumbnail), ICC APP2, and a COM.
    Fully controlled ordering so tests are stable across Pillow/piexif versions."""
    content = _content_only(_plain_jpeg(quality))
    return insert_after_soi(content, noncanonical_jfif(), exif_app1(),
                            icc_app2(), com_segment())


# --------------------------------------------------------------------------- #
# Craftable segments (bolt onto the base or use in pure marker-stream tests)
# --------------------------------------------------------------------------- #
def _app_segment(marker: int, payload: bytes) -> bytes:
    length = (2 + len(payload)).to_bytes(2, "big")
    return bytes([0xFF, marker]) + length + payload


def noncanonical_jfif() -> bytes:
    """A JFIF APP0 that is NOT our canonical form (version 1.02, 300 dpi units) so
    the F1 canonicalization/rewrite path is exercised."""
    payload = (b"JFIF\x00" + bytes([1, 2]) + bytes([1])   # ver 1.02, units=dpi
               + (300).to_bytes(2, "big") + (300).to_bytes(2, "big")
               + bytes([0, 0]))                            # no thumbnail
    return _app_segment(0xE0, payload)


def exif_app1() -> bytes:
    return _app_segment(0xE1, make_exif_payload())


def icc_app2(profile: bytes | None = None) -> bytes:
    profile = profile or fake_icc_profile()
    payload = icc_mod.JPEG_ICC_SIG + bytes([1, 1]) + profile   # chunk 1 of 1
    return _app_segment(0xE2, payload)


def com_segment(text: bytes = b"secret comment leak") -> bytes:
    return _app_segment(0xFE, text)


def xmp_app1(text: bytes = b"<x:xmpmeta>secret gps + creator</x:xmpmeta>") -> bytes:
    return _app_segment(0xE1, xmp_mod.XMP_SIG + text)


def ext_xmp_app1(guid: bytes = b"A" * 32, full_len: int = 8, offset: int = 0,
                 data: bytes = b"EXTDATA!") -> bytes:
    body = guid + full_len.to_bytes(4, "big") + offset.to_bytes(4, "big") + data
    return _app_segment(0xE1, xmp_mod.EXT_XMP_SIG + body)


def mpf_app2() -> bytes:
    # MPF marker + minimal (fake) MP header; enough to be classified as app2_mpf.
    return _app_segment(0xE2, b"MPF\x00" + b"\x00" * 16)


def photoshop_app13() -> bytes:
    """APP13 with an IPTC-IIM record (0x0404) and an 8BIM thumbnail (0x040C)."""
    def bim(rid: int, data: bytes) -> bytes:
        block = b"8BIM" + rid.to_bytes(2, "big") + b"\x00\x00"  # empty pascal name
        block += len(data).to_bytes(4, "big") + data
        if len(data) % 2:
            block += b"\x00"
        return block

    # IPTC dataset: 0x1C | record=2 | dataset=120 (caption) | len | value
    caption = b"secret location caption"
    iptc = bytes([0x1C, 2, 120]) + len(caption).to_bytes(2, "big") + caption
    thumb = b"\x00" * 32  # stand-in thumbnail bytes
    payload = b"Photoshop 3.0\x00" + bim(0x0404, iptc) + bim(0x040C, thumb)
    return _app_segment(0xED, payload)


def insert_after_soi(data: bytes, *segs: bytes) -> bytes:
    """Insert segments right after SOI (valid position for APPn)."""
    return data[:2] + b"".join(segs) + data[2:]


def append_trailer(data: bytes, trailer: bytes = b"TRAILERSECRET") -> bytes:
    return data + trailer


def build_torture_jpeg() -> bytes:
    """Decodable content base + every metadata locus + trailer, in one file:
    non-canonical JFIF, Exif (GPS+thumbnail), ICC, COM, XMP, ExtendedXMP, MPF,
    IPTC/8BIM (with 8BIM thumbnail), and post-EOI trailer bytes."""
    content = _content_only(_plain_jpeg())
    data = insert_after_soi(content, noncanonical_jfif(), exif_app1(),
                            icc_app2(), com_segment(), xmp_app1(),
                            ext_xmp_app1(), mpf_app2(), photoshop_app13())
    return append_trailer(data)


# --------------------------------------------------------------------------- #
# Harness corpora: A1 variants (content-identical) + diverse guard inputs
# --------------------------------------------------------------------------- #
def make_jpeg_a1_variants(tmpdir: str, n_variants: int = 3,
                          n_repeats: int = 5) -> list[list[str]]:
    """A1 corpus in the harness shape (list of variant-groups of repeat paths).

    Every file shares byte-identical image content (same content-only base, so
    the same entropy-coded bytes); variant i differs only in metadata carrying a
    sentinel (AAAA.. / BBBB.. / CCCC..) in a COM and in EXIF. After a correct F1
    scrub all variants collapse to identical bytes -> A1 pass, empty floor. A
    scrubber that left any metadata-variant byte behind would separate the variants
    -> A1 fail. Content-identity here is a theorem (shared base), not a measure."""
    base = _content_only(_plain_jpeg())
    groups: list[list[str]] = []
    for i in range(n_variants):
        sentinel = bytes([65 + i]) * 16          # AAAA.. / BBBB.. / CCCC..
        variant = insert_after_soi(
            base,
            noncanonical_jfif(),                 # same for all -> canonicalized
            com_segment(b"SENTINEL" + sentinel),  # variant-specific metadata
            _app_segment(0xE1, make_exif_payload_with(sentinel)),
        )
        paths = []
        for r in range(n_repeats):
            p = os.path.join(tmpdir, f"jpeg_a1_v{i}_r{r}.jpg")
            with open(p, "wb") as f:
                f.write(variant)
            paths.append(p)
        groups.append(paths)
    return groups


def make_exif_payload_with(sentinel: bytes) -> bytes:
    """EXIF payload carrying the sentinel in the Make/Software tags (a second
    variant-correlated locus beyond the COM)."""
    d = {
        "0th": {
            piexif.ImageIFD.Make: b"CAM-" + sentinel,
            piexif.ImageIFD.Software: b"sw-" + sentinel,
        },
        "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None,
    }
    return piexif.dump(d)


def _textured_pixels(w: int, h: int, kind: str, seed: int) -> Image.Image:
    """Genuinely varied, information-rich textures so a fingerprint-guard corpus
    doesn't share content-dependent Huffman/scan bytes by accident. Small/flat
    images produce degenerate (near-identical) Huffman tables that masquerade as
    a tool signature; rich content makes jpegtran's optimized tables diverge, so
    only true tool signatures survive as cross-input invariants."""
    import math
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            if kind == "grad":
                px[x, y] = ((x * 7 + seed + y) % 256, (y * 11 + x) % 256,
                            ((x + y) * 5 + seed) % 256)
            elif kind == "noise":
                px[x, y] = ((x * 131 + y * 17 + seed) % 256,
                            (x * 7 + y * 251 + seed * 3) % 256,
                            (x * 97 + y * 53 + seed * 7) % 256)
            elif kind == "rings":
                v = int(128 + 127 * math.sin(((x * x + y * y) ** 0.5 + seed) / 3))
                w2 = int(128 + 127 * math.cos((x - y + seed) / 5))
                px[x, y] = (v % 256, w2 % 256, (v ^ w2) % 256)
            else:  # weave
                px[x, y] = ((x * 3 + y * y) % 256, (200 + x * y) % 256,
                            (y * 4 + seed + x) % 256)
    return img


def diverse_jpeg_inputs(tmpdir: str, n: int = 4) -> list[str]:
    """Genuinely diverse inputs (varied size, rich texture, quality, chroma
    subsampling, and metadata) for the fingerprint guard: after scrubbing, the
    only cross-input invariants left in our output must be format-mandatory /
    standard-encoder structure (see JpegPlugin.mandatory_constants), not a tool
    signature. Diversity in subsampling + rich texture is what stops
    content-dependent DHT/SOF bytes from coinciding and masquerading as one."""
    specs = [
        (128, 96, "noise", 80, "4:2:0"),
        (144, 112, "rings", 88, "4:4:4"),
        (112, 128, "weave", 74, "4:2:2"),
        (160, 104, "grad", 90, "4:4:4"),
        (104, 136, "noise", 83, "4:2:0"),
        (120, 120, "rings", 78, "4:2:2"),
    ]
    paths = []
    for i in range(n):
        w, h, kind, q, sub = specs[i % len(specs)]
        buf = io.BytesIO()
        _textured_pixels(w, h, kind, seed=i + 1).save(
            buf, format="JPEG", quality=q, subsampling=sub)
        data = _content_only(buf.getvalue())
        segs: list[bytes] = []
        if i % 2 == 0:
            segs.append(noncanonical_jfif())      # JFIF present only on evens
        else:
            segs.append(exif_app1())              # EXIF-only on odds
        segs.append(com_segment(f"diverse-{i}".encode()))
        data = insert_after_soi(data, *segs)
        p = os.path.join(tmpdir, f"diverse_{i}.jpg")
        with open(p, "wb") as f:
            f.write(data)
        paths.append(p)
    return paths
