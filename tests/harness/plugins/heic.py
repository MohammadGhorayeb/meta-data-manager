"""HeicPlugin — harness-side format knowledge for HEIC (FormatPlugin).

Two producers live in one HEIC, the same split M4A forced this project to name:

  * the **muxer** — which brand it stamps, the order it writes `meta`'s tables in,
    the `iloc` field widths it chose, whether the picture is one image or a grid of
    tiles and how many, which optional boxes exist. None of that depends on the
    picture, so it fingerprints the writing software the way a DQT fingerprints a
    JPEG encoder.
  * the **HEVC encoder** — the coded tile data itself.

`structural_features` is the muxer channel only. The coded-image digest is exposed
separately and deliberately kept OUT of it, for the reason written up in `m4a.py`:
putting compressed content into the categorical A2 channel made M4A the only format
judged against its own content while MP3's channel was headers-only, so the two could
never pass or fail on the same yardstick. HEIC's F1 is a container tier and does not
re-encode anything, so the coded tiles are a *stated residual*, not a hidden one.
"""
from __future__ import annotations

import hashlib

from src.scrub.formats.heic import walker as w
from src.scrub.standards import isobmff as iso


class HeicPlugin:
    format_id = "heic"

    def matches(self, header: bytes, path: str = "") -> bool:
        # `....ftyp` is shared by every ISOBMFF file, so the brand decides — the same
        # gate the handler uses, for the same reason.
        return len(header) >= 12 and header[4:8] == b"ftyp" and w.looks_like_heic(
            header)

    def annotate(self, in_path: str, offset: int) -> str | None:
        try:
            boxes = iso.parse(open(in_path, "rb").read())
        except Exception:
            return None
        best = None
        for root in boxes:
            for box in root.walk():
                if box.offset <= offset < box.end:
                    if best is None or box.offset >= best.offset:
                        best = box
        if best is None:
            return None
        return f"{best.type.decode('latin-1', 'replace')}@+{offset - best.offset}"

    def canonical_content(self, path: str) -> bytes:
        """Decoded pixels — the content identity, through an independent decoder.

        Returns b"" when `pillow-heif` is absent so a caller reports *not measured*
        rather than *unchanged*. Note what this oracle cannot see: libheif renders
        the primary item and ignores the HDR gain map, so a gain-map change is
        invisible here (limit #32).
        """
        from tests.scrub import heic_corpus as hc
        return hc.decoded_pixels(path)

    def mandatory_constants(self) -> list[bytes]:
        # Format-required by ISO/IEC 23008-12 and 14496-12: every HEIC has these, so
        # they are not our mark on the file.
        return [b"ftyp", b"meta", b"mdat", b"hdlr", b"pitm", b"iinf", b"infe",
                b"iloc", b"iprp", b"ipco", b"ipma", b"ispe", b"hvcC", b"hvc1",
                b"mif1", b"heic"]

    def structural_features(self, path: str) -> dict:
        """A2 structural channel: what the muxer chose, not what the camera saw.

        Image *dimensions* are deliberately absent — those are content. Tile
        geometry is not: how a producer cuts one picture into tiles is its own
        choice, and it is the most legible thing about an iPhone HEIC.
        """
        try:
            data = open(path, "rb").read()
            layout = w.walk(data)
        except Exception:
            return {}

        fmt = layout.iloc_format
        primary = layout.items[layout.primary_id]
        tiles = layout.derived_inputs().get(layout.primary_id, [])
        ftyp = next((b for b in layout.boxes if b.type == b"ftyp"), None)
        compatible = ()
        if ftyp is not None:
            body = ftyp.payload[8:]
            compatible = tuple(body[i:i + 4].decode("latin-1", "replace")
                               for i in range(0, len(body) - 3, 4))

        properties: tuple[str, ...] = ()
        iprp = next((c for c in layout.meta.children if c.type == b"iprp"), None)
        if iprp is not None:
            ipco = next((c for c in w.children_of(iprp) if c.type == b"ipco"), None)
            if ipco is not None:
                properties = tuple(c.type.decode("latin-1", "replace")
                                   for c in w.children_of(ipco))

        return {
            "brand": w.brand(data).decode("latin-1", "replace"),
            "compatible_brands": compatible,
            "top_level_order": tuple(b.type.decode("latin-1", "replace")
                                     for b in layout.boxes),
            "meta_table_order": tuple(c.type.decode("latin-1", "replace")
                                      for c in layout.meta.children),
            "iloc_widths": (fmt.version, fmt.offset_size, fmt.length_size,
                            fmt.base_offset_size, fmt.index_size),
            "primary_kind": primary.item_type.decode("latin-1", "replace"),
            "tile_count": len(tiles),
            "item_kinds": tuple(sorted(
                {i.item_type.decode("latin-1", "replace") for i in
                 layout.items.values()})),
            "reference_kinds": tuple(sorted(
                {r.kind.decode("latin-1", "replace") for r in layout.references})),
            "property_inventory": properties,
            "hvcc": self._hvcc_digest(layout),
        }

    def _hvcc_digest(self, layout) -> str:
        """The HEVC decoder configuration record — profile, level, tier and the
        parameter sets the encoder emitted. An encoder's settings, not the scene's."""
        iprp = next((c for c in layout.meta.children if c.type == b"iprp"), None)
        if iprp is None:
            return "none"
        ipco = next((c for c in w.children_of(iprp) if c.type == b"ipco"), None)
        if ipco is None:
            return "none"
        blobs = b"".join(c.payload for c in w.children_of(ipco)
                         if c.type == b"hvcC")
        return hashlib.sha1(blobs).hexdigest()[:16] if blobs else "none"

    def coded_image_digest(self, path: str) -> str:
        """Hash of the coded tiles, deliberately NOT in `structural_features`.

        F1 is a container tier: it moves the tiles and never re-encodes them, so this
        separates producers by construction. Reporting it inside the categorical A2
        channel would make that arithmetic look like a discovery; it is a property of
        the tier, and belongs in the cell's stated reason instead.
        """
        try:
            data = open(path, "rb").read()
            layout = w.walk(data)
        except Exception:
            return ""
        blob = b"".join(
            data[e.offset:e.end]
            for item in sorted(layout.items.values(), key=lambda i: i.item_id)
            for e in item.file_extents if item.item_type == b"hvc1")
        return hashlib.sha1(blob).hexdigest()[:16]
