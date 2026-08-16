"""PdfPlugin — harness-side format knowledge for PDF (FormatPlugin).

PDF carries **two producers in one file**, exactly as M4A does, and the A2 cells must
name which one leaked rather than averaging them:

* the **serializer** — object ordering and numbering, xref style (table versus
  stream), `ObjStm` usage, compression choices, the binary header comment, the `/ID`
  scheme, linearisation, whitespace and `/Length` conventions. All of this is settled
  by our own writer, so F1 already normalises it.
* the **layout engine** — the content-stream operator sequence, font subsetting and
  glyph geometry. F1 does not touch it, and only part of it is normalisable at all
  (W5): number formatting, operator style and `Tj` splitting can be canonicalised
  losslessly, but glyph positions, advances and subset composition cannot be changed
  without re-typesetting, which would change what the page looks like.

So `structural_features` reports two named key groups, mirroring the
`structural_features` / `coded_audio_digest` split in `plugins/m4a.py`.

`struct:size` is deliberately left in. The M4A lesson was "don't judge a format
against its own compressed content in the categorical channel", not "drop side
channels" — and because the peer corpus holds the *source document* constant across
producers, size is a legitimate producer signal here. (`oracle/fields.py:99` hardcodes
`struct:size` before consulting the plugin anyway; only an experiment's key tuple can
exclude it.)
"""
from __future__ import annotations

import hashlib
import re
import subprocess

from src.scrub.formats.pdf import content as ct
from src.scrub.formats.pdf import serialize as ser
from src.scrub.formats.pdf import walker as w

# Keys naming the SERIALIZER — how the bytes were laid out.
SERIALIZER_KEYS = ("struct:header", "struct:binary_comment", "struct:xref_kind",
                   "struct:revisions", "struct:object_streams", "struct:linearized",
                   "struct:trailer_keys", "struct:indirect_lengths")

# Keys naming the LAYOUT ENGINE — how the page was typeset.
#
# `stream_count` sits here rather than with the serializer, and the distinction was
# worth getting right: how many stream objects a file has is decided by what the
# layout engine embedded (fonts, images, per-page content), not by how the bytes were
# laid out. An earlier version computed it under the name `indirect_lengths`, which
# made F1 look as though it left a serializer trait behind when what actually
# survived was a layout one.
LAYOUT_KEYS = ("struct:operators", "struct:number_style", "struct:font_subsets",
               "struct:glyph_digest", "struct:stream_count")

_INDIRECT_LENGTH = re.compile(rb"/Length\s+\d+\s+\d+\s+R")


def empty_document_skeleton() -> bytes:
    """What our serializer emits for a document with **no content at all**.

    Generated, not transcribed, so it cannot drift from the writer. A one-page PDF
    with an empty content stream and no resources: everything in these bytes is
    structure the format requires plus the canonical form we chose, and none of it
    can carry provenance, because there is no provenance in the input to carry.
    """
    import pikepdf

    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    page.obj.Contents = pikepdf.Stream(pdf, b"")
    page.obj.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary())
    return ser.serialize(pdf)


class PdfPlugin:
    format_id = "pdf"

    def matches(self, header: bytes, path: str = "") -> bool:
        return header[:5] == w.MAGIC

    def annotate(self, in_path: str, offset: int) -> str | None:
        try:
            with open(in_path, "rb") as f:
                layout = w.walk(f.read())
        except Exception:
            return None
        for i, rev in enumerate(layout.revisions):
            if offset < rev.eof_end:
                start = layout.revisions[i - 1].eof_end if i else 0
                return f"revision{i}@+{offset - start}"
        return None

    def canonical_content(self, path: str) -> bytes:
        """The document's extractable text is the content identity.

        Not the rendered pixels: rasterising costs seconds per page and pins the
        answer to the installed poppler build, while text is what a reader loses if
        a scrub goes wrong. F3 will rasterise and destroy the text layer by design,
        so that tier needs a pixel-space identity of its own — which is a reason to
        add one there, not to make every tier pay for it here.
        """
        p = subprocess.run(["pdftotext", "-q", path, "-"], capture_output=True)
        return hashlib.sha1(p.stdout).digest() if p.returncode == 0 else b""

    def mandatory_constants(self) -> list[bytes]:
        """Format-required invariants the fingerprint guard must not read as a tool
        signature — recorded in the matrix's `excluded` block, never hidden.

        PDF is harder here than any format so far, and the difficulty is worth
        stating rather than papering over. Its syntax **is** ASCII keywords, so at
        `min_len=4` the guard surfaces `obj`, `endobj`, `xref` and `/Type` on every
        conformant file ever written. Worse, unlike FLAC or PNG — a short magic
        prologue then content — a PDF's mandatory skeleton is *interleaved through
        the whole file*: catalog, page tree, page objects, xref table and trailer are
        present in every document no matter what it contains.

        So the last entry is **the empty document our own writer produces**,
        generated rather than hand-listed. Any byte run common to every output that
        is a substring of that skeleton is, by construction, pure structure: it
        carries no content and no provenance because the document it came from has
        none. Anything *not* in it is real signal and the guard still fails on it.

        Declaring our canonical numbering this way is the same judgement
        `plugins/flac.py` already records for its last-block flag — a canonical form
        is a constant we choose, and the alternatives are worse: inherit the input
        producer's numbering (that is *their* fingerprint) or randomise it
        (non-deterministic, which the harness floor forbids). It marks a file as
        canonically rewritten, never as rewritten *from what* — limit #9.

        Because that declaration is broad, it is checked rather than trusted:
        `test_matrix_pdf.py` asserts the undeclared residue contains no producer
        string, no timestamp and no padding run, so adding a `/Producer` tomorrow
        fails a test even though the guard itself would still pass.
        """
        return [
            ser.VERSION,                       # the pinned header
            b"obj", b"endobj", b"stream", b"endstream",
            b"xref", b"trailer", b"startxref", b"%%EOF",
            b"0000000000 65535 f \n",          # the mandatory free-list head
            b" 00000 n \n",                    # the 20-byte in-use entry format
            b"/Type", b"/Catalog", b"/Pages", b"/Page", b"/Kids", b"/Count",
            b"/Parent", b"/Contents", b"/Resources", b"/MediaBox", b"/Length",
            b"/Root", b"/Size", b"/Filter", b"/Font", b"/XObject", b"/Subtype",
            empty_document_skeleton(),
        ]

    def structural_features(self, path: str) -> dict:
        """A2 structural channel, split by which producer each key accuses."""
        try:
            with open(path, "rb") as f:
                data = f.read()
            layout = w.walk(data)
        except Exception:
            return {}

        features = {
            # --- serializer ---
            "header": layout.header.decode("latin-1"),
            "binary_comment": (layout.binary_comment or b"<none>").hex(),
            "xref_kind": tuple(r.xref_kind for r in layout.revisions),
            "revisions": len(layout.revisions),
            "object_streams": layout.object_streams,
            "linearized": layout.linearized,
            "trailer_keys": _trailer_keys(data),
            # An indirect /Length is `/Length 12 0 R` — a real serializer choice
            # (cairo makes it, our writer never does). Counting `/Length ` instead
            # would count streams, which is a layout trait wearing a serializer name.
            "indirect_lengths": len(_INDIRECT_LENGTH.findall(data)),
            "stream_count": data.count(b"\nstream"),
        }
        features.update(_layout_features(path))
        return features


def _trailer_keys(data: bytes) -> tuple[str, ...]:
    """Which keys the trailer carries, in sorted order. `/ID` and `/Info` are the two
    that matter, and W0 showed both survive a qpdf rewrite."""
    tail = data[data.rfind(b"trailer"):]
    return tuple(sorted(k for k in ("/Root", "/Size", "/Info", "/ID", "/Prev",
                                    "/Encrypt", "/XRefStm") if k.encode() in tail))


def _layout_features(path: str) -> dict:
    """Content-stream and font traits — the layout engine's channel.

    `glyph_digest` hashes the text-positioning operators only. Deliberately not the
    whole content stream: colour and path operators vary with the document, while
    `Td`/`Tm`/`TJ` are where the typesetter's own geometry lives, which is the residual
    W5 predicts F2 cannot remove without re-typesetting.
    """
    import pikepdf

    try:
        pdf = pikepdf.open(path)
    except Exception:
        return {}
    operators: list[bytes] = []
    positioning: list[bytes] = []
    reals = 0
    with pdf:
        for page in pdf.pages:
            contents = page.obj.get("/Contents")
            streams = ([] if contents is None
                       else list(contents) if isinstance(contents, pikepdf.Array)
                       else [contents])
            for stream in streams:
                if not isinstance(stream, pikepdf.Stream):
                    continue
                try:
                    body = stream.read_bytes()
                except Exception:
                    continue
                for op in ct.operations(body):
                    operators.append(op.operator)
                    if op.operator in (b"Td", b"TD", b"Tm", b"TJ", b"Tj", b"Tf"):
                        positioning.append(op.operator + b"|"
                                           + b",".join(t.raw for t in op.operands))
                    reals += sum(1 for t in op.operands
                                 if t.kind == "number" and b"." in t.raw)
        subsets = tuple(sorted({
            str(font.get("/BaseFont") or "")
            for page in pdf.pages
            for font in ((page.obj.get("/Resources") or {}).get("/Font") or {}).values()
            if isinstance(font, pikepdf.Dictionary)}))
    return {
        "operators": tuple(sorted(set(o.decode("latin-1") for o in operators))),
        # How many numbers carry a decimal point: producers differ sharply in how
        # much precision they emit, and it is normalisable losslessly at F2.
        "number_style": reals,
        "font_subsets": subsets,
        "glyph_digest": hashlib.sha1(b"\n".join(positioning)).hexdigest()[:16],
    }
