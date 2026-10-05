"""RawPlugin — harness-side format knowledge for camera RAW (FormatPlugin).

Content identity is decoded, never parsed: LibRaw's sensor data AND a render with the
camera's own white balance, because the survey showed a Canon passing the first
with its colour gone. Returns b"" when rawpy is absent, so a caller reports *not
measured* rather than *unchanged*.

The A2 channel is stated rather than run (see gen_matrix_raw): make, model and lens
model are kept on purpose -- the decoder chooses the camera's colour profile by them
-- so "which model made this" survives by design, and the question worth asking,
"which camera BODY", needs several files from two bodies of one model, which the
corpus does not have. `structural_features` is the container layout, for the day it
does.
"""
from __future__ import annotations

import hashlib

from src.scrub.standards import tiff_ifd as t


class RawPlugin:
    format_id = "raw"

    def matches(self, header: bytes, path: str = "") -> bool:
        return header[:4] in (b"II*\x00", b"MM\x00*", b"IIRO", b"IIRS", b"IIU\x00") \
            or header[:16] == b"FUJIFILMCCD-RAW " or header[4:12] == b"ftypcrx "

    def annotate(self, in_path: str, offset: int) -> str | None:
        """The IFD, value or thumbnail an offset falls in, for evidence labels."""
        try:
            data = open(in_path, "rb").read()
            tree = t.parse(data, strict=False, magics=t.RAW_MAGICS)
        except Exception:
            return None
        for locus in t.loci(tree):
            if locus.offset <= offset < locus.offset + locus.length:
                return locus.name
        note = t.makernote(data, tree)
        if note is not None and note.offset <= offset < note.offset + note.length:
            return f"MakerNote({note.vendor})@+{offset - note.offset}"
        return None

    def canonical_content(self, path: str) -> bytes:
        try:
            import rawpy
        except ImportError:
            return b""
        with rawpy.imread(path) as r:
            h = hashlib.sha256(r.raw_image.tobytes())
            h.update(r.postprocess(use_camera_wb=True, no_auto_bright=True).tobytes())
        return h.digest()

    def mandatory_constants(self) -> list[bytes]:
        """What cleaning a preview leaves in EVERY output, declared rather than
        hidden (limit #9's species: a file looks cleaned, never cleaned *from
        what*), and generated from the code that writes it:

          * the cleaned JPEG is padded back to its length with zeros after EOI;
          * with the EXIF segment gone, a kept colour profile becomes the first
            segment after SOI, where a camera writes EXIF;
          * and zeros wherever a value was blanked or an image dropped: nothing in a
            raw can move, so removing anything leaves a zero run of its length
            (limit #45). Declared up to the size the corpus produces.

        Bounded by a test asserting none carries a locus."""
        from src.scrub.formats.raw import f1
        return [f1.PREVIEW_PAD_MARK, f1.PREVIEW_ICC_FIRST, f1.BLANKED_RUN]

    def structural_features(self, path: str) -> dict:
        try:
            data = open(path, "rb").read()
            tree = t.parse(data, strict=False, magics=t.RAW_MAGICS)
            note = t.makernote(data, tree)
        except Exception:
            return {}
        return {
            "magic": tree.magic,
            "byte_order": tree.byte_order,
            "ifds": tuple(i.name for i in tree.ifds),
            "makernote": note.vendor if note else "none",
            "size": len(data),
        }
