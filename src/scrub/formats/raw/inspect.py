"""What a camera raw's metadata says -- for the scrub report, never for a tier.

Lists what F1 removes, by the IFD it was found in: identity (serials, owners,
counters), when and where (dates, GPS, maker-note time zones), what wrote it
(software, XMP), and the previews that carry metadata of their own. Values are
decoded where they are text or numbers a person would recognise; a binary block is
reported by size.
"""
from __future__ import annotations

from ...standards import tiff_ifd as t
from ...standards import tiff_values as tv
from . import f1, identity


def describe(data: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        tree = t.parse(data, strict=False, magics=t.RAW_MAGICS)
        note = t.makernote(data, tree)
    except Exception:                                     # noqa: BLE001
        return out

    common = {(f.ifd, f.tag): f.name for f in identity.COMMON}
    for ifd in tree.ifds:
        for e in ifd.entries:
            name = (common.get((ifd.name, e.tag)) or f1._BLANK_ANYWHERE.get(e.tag)
                    or f1._REMOVE_ANYWHERE.get(e.tag) or f1._REMOVE_IN_DNG.get(e.tag))
            if ifd.name == "GPSIFD":
                name = tv.tag_name("GPS", e.tag) or f"GPS tag {e.tag:#06x}"
            if name is None:
                continue
            value = tv.decode(data, ifd.order, e) if e.tag not in (0x02BC, 0x83BB,
                                                                    0x8649) else None
            if value or e.tag in f1._REMOVE_ANYWHERE:
                out[f"{ifd.name}:{name}"] = value or f"({e.data_length} bytes)"
        if ifd.get(f1.TAG_SEMANTIC_NAME) is not None:
            out[f"{ifd.name}:semantic mask"] = "a map of the scene's content"

    if note is not None:
        out["MakerNote"] = f"{note.vendor}, {note.length} bytes"
        ifds = {i.name: i for i in note.ifds}
        fields = (identity.MAKERNOTE_FIELDS.get(note.vendor, ())
                  + f1.MAKERNOTE_DATES.get(note.vendor, ()))
        for f in fields:
            ifd = ifds.get(f.ifd)
            e = ifd.get(f.tag) if ifd is not None else None
            if e is None:
                continue
            value = None if f.span else tv.decode(data, ifd.order, e)
            if value or f.span:
                out[f"{note.vendor.capitalize()}:{f.name}"] = (
                    value or f"({f.span[1]} bytes)")

    try:
        previews = f1._previews(data, tree, note)
    except Exception:                                     # noqa: BLE001
        previews = []
    for where, at, length in previews:
        # Per preview, never fatal: a damaged one that F1 drops anyway (a semantic
        # mask) must not take the rest of the report down with it -- found by fuzz.
        try:
            if f1._has_preview_metadata(bytes(data[at:at + length])):
                out[where] = "carries its own metadata"
        except Exception:                                 # noqa: BLE001
            out[where] = "(could not be read)"
    return out
