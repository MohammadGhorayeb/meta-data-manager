"""Identity tags inside colour profiles (`docs/p6_tail_plan.md` §14).

The real-file sweep found display profiles embedded in vendor TIFFs and PDFs, each
naming its screen ("Apple Cinema Display") and carrying Apple's MakeAndModel tag
(`mmod`: maker, model and a per-unit number). `icc.sanitize` zeroed only the
header; it now blanks every tag that names a device, a person or a date in any
profile that is not a published colour space -- and the colour transform must come
out the same, judged by LittleCMS.
"""
from __future__ import annotations

import io
import struct

import pytest
from PIL import Image, ImageCms

from src.scrub.formats.tiff import f1 as tiff_f1
from src.scrub.standards import icc

NAME = "Alice's studio display"
COPYRIGHT = "Calibrated by SENTINEL-OWNER"
MMOD = struct.pack(">4s4xIIII", b"mmod", 0x0610, 0x9216, 0x0200001C, 0xB6D1C200) + bytes(16)


def _desc_v2(text: str) -> bytes:
    ascii_ = text.encode() + b"\x00"
    return (b"desc" + bytes(4) + struct.pack(">I", len(ascii_)) + ascii_
            + bytes(4) + struct.pack(">I", 0) + bytes(2) + bytes(1) + bytes(67))


def _mluc(text: str) -> bytes:
    utf16 = text.encode("utf-16-be")
    return (b"mluc" + bytes(4) + struct.pack(">II", 1, 12) + b"enUS"
            + struct.pack(">II", len(utf16), 28) + utf16)


def _text(text: str) -> bytes:
    return b"text" + bytes(4) + text.encode() + b"\x00"


def _dtim() -> bytes:
    return b"dtim" + bytes(4) + struct.pack(">6H", 2026, 10, 1, 9, 30, 0)


def build(desc: bytes, extra: dict[bytes, bytes]) -> bytes:
    """LittleCMS's sRGB profile with its `desc` replaced and tags added, every
    offset rewritten."""
    base = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    table = {sig: base[off:off + size] for sig, off, size in icc.tags(base)}
    table[b"desc"] = desc
    table.update(extra)
    n = len(table)
    data_at = icc.HEADER_LEN + 4 + 12 * n
    entries, blob = b"", b""
    for sig, body in table.items():
        body += b"\x00" * (-len(body) % 4)
        entries += struct.pack(">4sII", sig, data_at + len(blob), len(body))
        blob += body
    header = bytearray(base[:icc.HEADER_LEN])
    total = data_at + len(blob)
    header[0:4] = struct.pack(">I", total)
    header[84:100] = bytes(16)
    return bytes(header) + struct.pack(">I", n) + entries + blob


def _personal(version: int = 2) -> bytes:
    desc = _desc_v2(NAME) if version == 2 else _mluc(NAME)
    return build(desc, {b"cprt": _text(COPYRIGHT), b"mmod": MMOD, b"calt": _dtim(),
                        b"dmdd": _mluc("SENTINEL-MODEL")})


def _transform(profile: bytes) -> bytes:
    img = Image.radial_gradient("L").resize((32, 32)).convert("RGB")
    return ImageCms.profileToProfile(
        img, ImageCms.ImageCmsProfile(io.BytesIO(profile)),
        ImageCms.createProfile("sRGB")).tobytes()


@pytest.mark.parametrize("version", [2, 4])
def test_identity_tags_are_blanked_and_colour_is_unchanged(version):
    profile = _personal(version)
    name = NAME.encode() if version == 2 else NAME.encode("utf-16-be")
    planted = [name, COPYRIGHT.encode(), "SENTINEL-MODEL".encode("utf-16-be"),
               MMOD[8:24]]
    assert all(p in profile for p in planted)
    clean = icc.sanitize(profile)
    assert [p for p in planted if p in clean] == []
    assert len(clean) == len(profile)
    assert icc.description(clean) == ""
    assert _transform(clean) == _transform(profile)          # LittleCMS, unchanged
    assert icc.sanitize(clean) == clean                       # idempotent


def test_a_device_profile_is_never_standard_whatever_it_is_called():
    spoofed = build(_desc_v2("sRGB IEC61966-2.1"), {b"mmod": MMOD})
    assert not icc.is_standard(spoofed)
    assert MMOD[8:24] not in icc.sanitize(spoofed)


def test_a_published_colour_space_keeps_its_tags():
    standard = build(_desc_v2("sRGB IEC61966-2.1"),
                     {b"cprt": _text("Copyright International Color Consortium")})
    assert icc.is_standard(standard)
    clean = icc.sanitize(standard)
    assert icc.description(clean) == "sRGB IEC61966-2.1"
    assert b"International Color Consortium" in clean


def test_a_tiff_carrying_a_display_profile_comes_out_without_its_identity():
    """End to end through a format that keeps profiles: TIFF F1 (the shared rule
    also serves GIF, WebP, HEIC and PDF)."""
    profile = _personal(4)
    img = Image.new("RGB", (8, 8), (10, 200, 30))
    buf = io.BytesIO()
    img.save(buf, "TIFF", icc_profile=profile)
    out = tiff_f1.scrub(buf.getvalue())
    assert NAME.encode("utf-16-be") not in out and MMOD[8:24] not in out
    assert tiff_f1.residuals(out) == []
    with Image.open(io.BytesIO(out)) as im:
        kept = im.info["icc_profile"]
    assert _transform(kept) == _transform(profile)
