"""What a JPEG's metadata says — for the scrub report.

Separate from `segments.py` because the two answer different questions. The walker
asks *where is every metadata byte*, which is what scrubbing needs; this asks *what
does it say*, which is what a person reading a report needs. Keeping them apart
means the tiers do not carry a value decoder they never use.

Coverage is exactly the walker's coverage, which is the point: the report shows what
this tool can see, and a locus it cannot model is absent from both the report and
the scrub.
"""
from __future__ import annotations

from ...standards import icc, iptc_iim, tiff_ifd, tiff_values
from . import segments as seg

# Segment kinds that carry no metadata: the picture itself and its coding tables.
_CONTENT = {"soi", "eoi", "sos", "dqt", "dht", "dri", "dac"}

_LABEL = {
    "app0_jfif": "JFIF header", "app0_jfxx": "JFIF extension",
    "app1_xmp": "XMP packet", "app1_xmp_extended": "XMP (extended)",
    "app2_icc": "ICC profile", "app2_mpf": "MPF (multi-picture)",
    "app13_photoshop": "Photoshop/IPTC", "app14_adobe": "Adobe APP14",
    "com": "JPEG comment",
}


def _exif_fields(payload: bytes) -> dict[str, str]:
    """Named EXIF tags with their values, plus a count of everything else.

    The unnamed tags are counted rather than listed: an exhaustive dump would bury
    the three things a user actually came to remove — the camera, the time and the
    place — and a report nobody reads protects nobody.
    """
    out: dict[str, str] = {}
    tiff = tiff_ifd.strip_exif_prefix(payload)
    try:
        tree = tiff_ifd.parse(tiff)
    except Exception:                                     # noqa: BLE001
        return {"EXIF": f"({len(payload)} bytes, unparseable)"}

    unnamed = 0
    for ifd in tree.ifds:
        for entry in ifd.entries:
            name = tiff_values.tag_name(ifd.name, entry.tag)
            if name is None:
                unnamed += 1
                continue
            value = tiff_values.decode(tiff, tree.byte_order, entry)
            if value:
                out[f"EXIF:{name}"] = value
    if unnamed:
        out["EXIF:other tags"] = f"{unnamed} more"
    for thumb in tree.thumbnails:
        out[f"EXIF:{thumb.in_ifd} thumbnail"] = f"({thumb.length} bytes)"
    return out


def describe(data: bytes) -> dict[str, str]:
    """locus -> what it says. Ordered as the file orders it."""
    out: dict[str, str] = {}
    structure = seg.walk(data)

    for s in structure.segments:
        if s.kind in _CONTENT or s.kind.startswith("sof"):
            continue
        payload = s.payload
        if s.kind == "app1_exif":
            out.update(_exif_fields(payload))
        elif s.kind == "com":
            out["JPEG comment"] = payload.decode("utf-8", "replace")
        elif s.kind == "app1_xmp":
            out["XMP packet"] = f"({len(payload)} bytes)"
        elif s.kind == "app2_icc":
            out["ICC profile"] = _icc_name(payload) or f"({len(payload)} bytes)"
        elif s.kind == "app13_photoshop":
            out["Photoshop/IPTC"] = _photoshop(payload)
        else:
            out[_LABEL.get(s.kind, s.kind)] = f"({len(payload)} bytes)"

    if structure.trailer:
        out["trailing bytes"] = f"({len(structure.trailer)} bytes after EOI)"
    return out


def _icc_name(payload: bytes) -> str | None:
    """The profile's device and platform, which is what it actually gives away.

    An ICC profile names a colour space, not a person — limit #14 measured it as
    narrowing to "this came off a Mac" rather than to an individual. Reporting the
    manufacturer and platform says exactly that much and no more.
    """
    try:
        header = icc.parse_header(payload[len(icc.JPEG_ICC_SIG) + 2:])
    except Exception:                                     # noqa: BLE001
        return None
    if header is None or not header.valid_signature:
        return None
    bits = [_ascii(header.color_space), _ascii(header.platform),
            _ascii(header.manufacturer)]
    return " / ".join(b for b in bits if b) or None


def _photoshop(payload: bytes) -> str:
    """IPTC field count, or the resource size when there is no IPTC block."""
    try:
        resources = iptc_iim.parse_8bim(payload)
        found = iptc_iim.find_iptc(resources)
        parts = []
        if found:
            datasets = iptc_iim.iptc_datasets(found[0].data)
            if datasets:
                parts.append(f"{len(datasets)} IPTC field(s)")
        thumbs = iptc_iim.find_thumbnail_resources(resources)
        if thumbs:
            # A Photoshop resource thumbnail is a second picture of the image,
            # with its own history -- worth naming rather than counting.
            parts.append(f"{len(thumbs)} embedded thumbnail(s)")
        if parts:
            return ", ".join(parts)
    except Exception:                                     # noqa: BLE001
        pass
    return f"({len(payload)} bytes)"


def _ascii(raw: bytes) -> str:
    text = raw.decode("ascii", "replace").strip().strip("\x00")
    return "".join(c for c in text if c.isprintable())
