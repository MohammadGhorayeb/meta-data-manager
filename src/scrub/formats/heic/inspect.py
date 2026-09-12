"""What a HEIC's metadata says — for the scrub report."""
from __future__ import annotations

import plistlib

from ...standards import tiff_ifd, tiff_values
from . import walker as w


def _exif_fields(blob: bytes) -> dict[str, str]:
    """The EXIF item, read with the Phase 1 TIFF reader unmodified — the reuse that
    made HEIC the cheap next step."""
    out: dict[str, str] = {}
    # The item payload begins with a 4-byte offset to the TIFF header.
    tiff = blob[4:]
    if tiff[:6] == b"Exif\x00\x00":
        tiff = tiff[6:]
    try:
        tree = tiff_ifd.parse(tiff)
    except Exception:                                     # noqa: BLE001
        return {"EXIF": f"({len(blob)} bytes, unparseable)"}
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
    return out


def _segmentation(blob: bytes) -> dict[str, str]:
    """Apple's `uri ` item: a binary plist of semantic-segmentation output.

    Worth naming rather than counting bytes, because it is a different species from
    the rest of the file's metadata — not a fact about the camera but a machine-made
    claim about the *subject*: whether there are people in the frame and how much of
    it they occupy.
    """
    try:
        obj = plistlib.loads(blob)
    except Exception:                                     # noqa: BLE001
        return {"Apple metadata blob": f"({len(blob)} bytes)"}
    if not isinstance(obj, dict):
        return {"Apple metadata blob": f"({len(blob)} bytes)"}

    out = {"Apple segmentation blob": f"({len(blob)} bytes)"}
    for value in obj.values():
        if not isinstance(value, dict):
            continue
        if "PeopleRatio" in value:
            out["Apple:PeopleRatio"] = f"{value['PeopleRatio']:.3f}"
        if "SkinRatio" in value:
            out["Apple:SkinRatio"] = f"{value['SkinRatio']:.4f}"
    mattes = sum(1 for v in obj.values() if isinstance(v, bytes) and len(v) > 4096)
    if mattes:
        out["Apple:segmentation mattes"] = f"{mattes} raster mask(s)"
    return out


def describe(data: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        layout = w.walk(data)
    except Exception:                                     # noqa: BLE001
        return out

    for item in sorted(layout.metadata_items, key=lambda i: i.item_id):
        extents = item.file_extents
        if not extents:
            continue
        blob = data[extents[0].offset:extents[0].end]
        if item.item_type == b"Exif":
            out.update(_exif_fields(blob))
        elif item.item_type == b"mime":
            out[f"XMP packet (item {item.item_id})"] = f"({item.size} bytes)"
        elif item.item_type == b"uri ":
            out.update(_segmentation(blob))

    thumbs = layout.thumbnail_ids()
    if thumbs:
        size = sum(layout.items[t].size for t in thumbs if t in layout.items)
        out["embedded thumbnail"] = f"{len(thumbs)} item(s), {size} bytes"
    aux = layout.auxiliary_ids()
    if aux:
        size = sum(layout.items[a].size for a in aux if a in layout.items)
        out["auxiliary images"] = (f"{len(aux)} depth/matte image(s), {size} bytes "
                                   f"— derived from the subject")
    return out
