"""TiffPlugin — harness-side format knowledge for plain TIFF.

Content identity is decoded, never parsed: every page's pixels through Pillow.
`structural_features` is the A2 channel -- what the writing library decided: byte
order, the IFD chain, each IFD's tag set, compression, strip layout, whether a
colour profile is embedded and which, and the size.
"""
from __future__ import annotations

import hashlib

from src.scrub.standards import icc
from src.scrub.standards import tiff_ifd as t


class TiffPlugin:
    format_id = "tiff"

    def matches(self, header: bytes, path: str = "") -> bool:
        return header[:4] in (b"II*\x00", b"MM\x00*")

    def annotate(self, in_path: str, offset: int) -> str | None:
        try:
            tree = t.parse(open(in_path, "rb").read(), strict=False)
        except Exception:                                 # noqa: BLE001
            return None
        for locus in t.loci(tree):
            if locus.offset <= offset < locus.offset + locus.length:
                return locus.name
        return None

    def canonical_content(self, path: str) -> bytes:
        from PIL import Image
        h = hashlib.sha256()
        with Image.open(path) as im:
            for k in range(getattr(im, "n_frames", 1)):
                im.seek(k)
                if im.size == (0, 0):
                    continue
                try:
                    h.update(im.convert("RGBA").tobytes())
                except OSError:                           # a thumbnail-only IFD
                    continue
        return h.digest()

    def mandatory_constants(self) -> list[bytes]:
        """The zeros a blanked value or dropped block leaves -- nothing in a TIFF
        may move, so a removed value keeps its length (as in RAW, limit #45). A
        single fill character: the guard strips it from the edges of a run's pieces
        and cuts at runs of it, never at a lone byte."""
        return [b"\0"]

    def structural_features(self, path: str) -> dict:
        try:
            data = open(path, "rb").read()
            tree = t.parse(data, strict=False)
        except Exception:                                 # noqa: BLE001
            return {}
        feats = {"byte_order": tree.byte_order,
                 "ifds": tuple(i.name for i in tree.ifds),
                 "tag_sets": tuple(tuple(sorted(e.tag for e in i.entries))
                                   for i in tree.ifds),
                 "size": len(data)}
        ifd0 = tree.ifd("IFD0")
        if ifd0 is not None:
            for tag, key in ((0x0103, "compression"), (0x0116, "rows_per_strip"),
                             (0x011C, "planar")):
                e = ifd0.get(tag)
                feats[key] = e.raw_value if e is not None else None
            strips = ifd0.get(0x0111)
            feats["strip_count"] = strips.count if strips is not None else 0
            prof = ifd0.get(0x8773)
            feats["icc"] = (icc.description(t.value_bytes(data, prof)) or "?"
                            if prof is not None else None)
        return feats
