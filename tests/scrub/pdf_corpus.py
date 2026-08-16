"""Deterministic PDF corpus builders for the Phase-3 tests.

PDFs here are written **by hand, byte by byte**, not through pikepdf. Two reasons,
both load-bearing:

1. *pikepdf cannot append an incremental update at all.* It always rewrites the
   whole document to a single revision — which is precisely the behaviour W3 says
   a scrub needs and therefore precisely what the corpus must NOT be built with.
   A corpus that cannot express multi-revision history cannot test for it.
2. The corpus must not share code with the scrubber. `src/scrub/formats/pdf/`
   will grow its own serializer (the W0 decision); if the corpus were built with
   it, every experiment would be measuring the scrubber against itself and any
   shared misunderstanding of the format would cancel out invisibly.

So this module is a small, deliberately dumb PDF writer that only knows classic
cross-reference tables. It is test-side and stays test-side.

Everything is byte-deterministic: fixed dates, fixed `/ID`s, fixed stream bytes,
no wall clock anywhere.
"""
from __future__ import annotations

import os
import shutil
import subprocess

HAVE_PDFTOTEXT = shutil.which("pdftotext") is not None

# Producers for the A2 peer set (M3). Named here so a caller can report *which*
# were unavailable rather than silently shrinking the peer set — limit #12.
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
HAVE_CHROME = os.path.exists(CHROME)
HAVE_SOFFICE = shutil.which("soffice") is not None
HAVE_CUPSFILTER = shutil.which("cupsfilter") is not None

# The document ID a producer writes. Fixed so the corpus is reproducible; the
# point of the experiment is that this SURVIVES a scrub (W0), not that it varies.
DOC_ID = b"0123456789abcdef0123456789abcdef"


# --------------------------------------------------------------------------- #
# A minimal classic-xref PDF writer
# --------------------------------------------------------------------------- #
def _obj(num: int, body: bytes) -> bytes:
    return b"%d 0 obj\n" % num + body + b"\nendobj\n"


def _stream_obj(num: int, extra: bytes, data: bytes) -> bytes:
    return (b"%d 0 obj\n<< /Length %d" % (num, len(data)) + extra + b" >>\nstream\n"
            + data + b"\nendstream\nendobj\n")


def _xref_section(entries: dict[int, int]) -> bytes:
    """A classic xref table for `entries` {objnum: byte offset}.

    Consecutive object numbers are grouped into subsections, which is what the
    spec requires and what makes an incremental update's xref small. Every entry
    is exactly 20 bytes (ISO 32000 §7.5.4) — get that wrong and readers silently
    resolve objects to the wrong offsets.
    """
    out = [b"xref\n"]
    nums = sorted(entries)
    runs: list[list[int]] = []
    for n in nums:
        if runs and n == runs[-1][-1] + 1:
            runs[-1].append(n)
        else:
            runs.append([n])
    for run in runs:
        start = run[0]
        if start == 0:
            out.append(b"0 %d\n" % len(run))
            out.append(b"0000000000 65535 f \n")
            run = run[1:]
            if run:
                out.append(b"".join(b"%010d 00000 n \n" % entries[n] for n in run))
            continue
        out.append(b"%d %d\n" % (start, len(run)))
        out.append(b"".join(b"%010d 00000 n \n" % entries[n] for n in run))
    return b"".join(out)


def _revision(base: bytes, objects: list[tuple[int, bytes]], *, size: int,
              root: int, info: int | None, prev: int | None,
              doc_id: bytes = DOC_ID, gen_id: bytes | None = None,
              first: bool = False) -> bytes:
    """Append one revision — body objects, xref table, trailer, `%%EOF`.

    `prev` is the byte offset of the *previous* revision's xref, i.e. the link
    that makes the file's whole edit history walkable. That chain is the subject
    of E-PDF-HISTORY.
    """
    body = bytearray(base)
    entries: dict[int, int] = {0: 0} if first else {}
    for num, payload in objects:
        entries[num] = len(body)
        body += payload
    xref_off = len(body)
    body += _xref_section(entries)

    trailer = [b"trailer\n<< /Size %d /Root %d 0 R" % (size, root)]
    if info is not None:
        trailer.append(b" /Info %d 0 R" % info)
    trailer.append(b" /ID [<%s> <%s>]" % (doc_id, gen_id or doc_id))
    if prev is not None:
        trailer.append(b" /Prev %d" % prev)
    trailer.append(b" >>\nstartxref\n%d\n%%%%EOF\n" % xref_off)
    body += b"".join(trailer)
    return bytes(body)


def _page_content(text: bytes, y: int = 720) -> bytes:
    return b"BT /F1 14 Tf 72 %d Td (" % y + text + b") Tj ET\n"


_HEADER = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"


def _info(num: int, author: bytes, title: bytes, producer: bytes = b"CorpusWriter 1.0",
          date: bytes = b"D:20240101120000Z") -> bytes:
    return _obj(num, b"<< /Title (" + title + b") /Author (" + author
                + b") /Producer (" + producer + b") /Creator (" + producer
                + b") /CreationDate (" + date + b") /ModDate (" + date + b") >>")


# --------------------------------------------------------------------------- #
# The E-PDF-HISTORY corpus: a document edited by appending
# --------------------------------------------------------------------------- #
def incremental_pdf(path: str, sentinel: str = "SECRET", n_revisions: int = 3) -> str:
    """A PDF whose visible text was *edited* — each edit appended, never rewritten.

    Revision 1 says `CONFIDENTIAL-REV1-<sentinel>` and is authored by
    `Author-REV1-<sentinel>`. Each later revision replaces the content stream and
    the `/Info` dictionary with something more innocuous, and revision `n` shows
    only the final, harmless text.

    Nothing is removed. Every earlier revision's objects are still in the file, at
    their original offsets, reachable by following `/Prev` — which is how a PDF is
    edited in the real world and why "the redacted version was published with the
    original text one layer down" keeps happening.
    """
    if n_revisions < 2:
        raise ValueError("history needs at least two revisions to be history")

    secrets = [f"CONFIDENTIAL-REV{i + 1}-{sentinel}".encode()
               for i in range(n_revisions - 1)]
    secrets.append(b"Public release text, nothing sensitive here.")

    # Revision 1: the whole document.
    objs = [
        (1, _obj(1, b"<< /Type /Catalog /Pages 2 0 R >>")),
        (2, _obj(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")),
        (3, _obj(3, b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]"
                    b" /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>")),
        (4, _obj(4, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica"
                    b" /Encoding /WinAnsiEncoding >>")),
        (5, _stream_obj(5, b"", _page_content(secrets[0]))),
        (6, _info(6, f"Author-REV1-{sentinel}".encode(),
                  f"Draft-REV1-{sentinel}".encode())),
    ]
    data = _revision(_HEADER, objs, size=7, root=1, info=6, prev=None, first=True)

    # Revisions 2..n: replace the content stream and /Info, append, link with /Prev.
    for i in range(1, n_revisions):
        prev = int(data.rsplit(b"startxref\n", 1)[1].split(b"\n", 1)[0])
        label = "FINAL" if i == n_revisions - 1 else f"REV{i + 1}"
        objs = [
            (5, _stream_obj(5, b"", _page_content(secrets[i]))),
            (6, _info(6, f"Author-{label}-{sentinel}".encode(),
                      f"Draft-{label}-{sentinel}".encode())),
        ]
        data = _revision(data, objs, size=7, root=1, info=6, prev=prev,
                         gen_id=b"%032d" % (i + 1))

    with open(path, "wb") as f:
        f.write(data)
    return path


def redacted_pdf(path: str, sentinel: str = "SECRET") -> str:
    """The other famous failure: one revision, text still there, a box drawn over it.

    No history at all — a single-revision file — so a scrub that correctly collapses
    revisions changes nothing about it. That is the point: it separates the leak
    E-PDF-HISTORY *can* close from the one it cannot, and it is the input W7's
    detector will be measured on.
    """
    secret = f"CONFIDENTIAL-{sentinel}".encode()
    content = (_page_content(secret)
               + b"0 0 0 rg\n60 710 300 30 re f\n")  # opaque black box, drawn after
    objs = [
        (1, _obj(1, b"<< /Type /Catalog /Pages 2 0 R >>")),
        (2, _obj(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")),
        (3, _obj(3, b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]"
                    b" /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>")),
        (4, _obj(4, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica"
                    b" /Encoding /WinAnsiEncoding >>")),
        (5, _stream_obj(5, b"", content)),
    ]
    data = _revision(_HEADER, objs, size=6, root=1, info=None, prev=None, first=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


# --------------------------------------------------------------------------- #
# The torture document: every locus at once
# --------------------------------------------------------------------------- #
# Built with pikepdf rather than by hand. The hand writer above exists because
# pikepdf cannot express incremental updates; this file is single-revision, so the
# library is simply the less error-prone way to attach a dozen odd objects. That it
# carries qpdf's own fingerprint is realistic — real inputs come from real tools.
TORTURE_SECRETS = [
    b"Author-SECRET", b"Title-SECRET", b"Keywords-SECRET",   # /Info
    b"xmp-creator-SECRET",                                   # XMP, document level
    b"xmp-on-image-SECRET",                                  # XMP hanging off an XObject
    b"annot-author-SECRET",                                  # annotation /T
    b"piece-info-SECRET",                                    # /PieceInfo private data
    b"spider-url-SECRET",                                    # /SpiderInfo capture URL
    b"ocg-creator-SECRET",                                   # /OCProperties /CreatorInfo
    b"/Users/secret/path.docx",                              # /Launch target
    b"TestCam",                                              # EXIF in the embedded JPEG
]


def _xmp(text: bytes) -> bytes:
    return (b'<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>'
            b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
            b'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            b'<rdf:Description xmlns:xmp="http://ns.adobe.com/xap/1.0/">'
            b"<xmp:CreatorTool>" + text + b"</xmp:CreatorTool>"
            b"</rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end=\"w\"?>")


def torture_pdf(path: str) -> str:
    """One page carrying every locus the W2 list names that F1 is expected to clear.

    Deliberately includes the two that no object-graph walk finds on its own — a page
    `/Thumb` and an **inline image** whose `ID` payload is a full JPEG with EXIF — plus
    XMP hanging off an image rather than off the catalog, because "delete
    `Root.Metadata`" passes a naive test and leaves that one behind.
    """
    import pikepdf

    from tests.scrub import corpus as jpeg_corpus

    jpeg = jpeg_corpus.build_torture_jpeg()          # EXIF + GPS + thumbnail + trailer
    pdf = pikepdf.new()
    page_content = (b"BT /F1 14 Tf 72 720 Td (Visible document text.) Tj ET\n"
                    b"q 100 0 0 60 72 600 cm /Im0 Do Q\n"
                    b"q 40 0 0 40 72 500 cm\n"
                    b"BI /W 8 /H 8 /CS /RGB /F /DCTDecode ID " + jpeg + b"\nEI Q\n")

    image = pikepdf.Stream(pdf, jpeg)
    image.Type, image.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
    image.Width, image.Height, image.BitsPerComponent = 64, 48, 8
    image.ColorSpace, image.Filter = pikepdf.Name.DeviceRGB, pikepdf.Name.DCTDecode
    # XMP on an XObject, not on the catalog — the locus a document-level-only scrub
    # walks straight past.
    image.Metadata = pikepdf.Stream(pdf, _xmp(b"xmp-on-image-SECRET"))

    thumb = pikepdf.Stream(pdf, jpeg)
    thumb.Width, thumb.Height, thumb.BitsPerComponent = 64, 48, 8
    thumb.ColorSpace, thumb.Filter = pikepdf.Name.DeviceRGB, pikepdf.Name.DCTDecode

    page = pdf.add_blank_page(page_size=(612, 792))
    page.obj.Contents = pikepdf.Stream(pdf, page_content)
    page.obj.Resources = pikepdf.Dictionary(
        Font=pikepdf.Dictionary(F1=pikepdf.Dictionary(
            Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
            BaseFont=pikepdf.Name.Helvetica)),
        XObject=pikepdf.Dictionary(Im0=pdf.make_indirect(image)))
    page.obj.Thumb = pdf.make_indirect(thumb)
    page.obj.PieceInfo = pikepdf.Dictionary(
        ADBE_Illustrator=pikepdf.Dictionary(
            LastModified=pikepdf.String("D:20240101120000Z"),
            Private=pikepdf.String("piece-info-SECRET")))
    page.obj.SpiderInfo = pikepdf.Dictionary(
        S=pikepdf.Name.SPS, F=8, C=pikepdf.String("spider-url-SECRET"))
    page.obj.Annots = pikepdf.Array([pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Text,
        Rect=pikepdf.Array([10, 10, 30, 30]),
        T=pikepdf.String("annot-author-SECRET"),
        M=pikepdf.String("D:20240102130000Z"),
        Contents=pikepdf.String("a comment"),
        A=pikepdf.Dictionary(S=pikepdf.Name.Launch,
                             F=pikepdf.String("/Users/secret/path.docx"))))])

    pdf.Root.Metadata = pikepdf.Stream(pdf, _xmp(b"xmp-creator-SECRET"))
    pdf.Root.OCProperties = pikepdf.Dictionary(
        OCGs=pikepdf.Array([pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name.OCG, Name=pikepdf.String("Layer 1"),
            Usage=pikepdf.Dictionary(CreatorInfo=pikepdf.Dictionary(
                Creator=pikepdf.String("ocg-creator-SECRET"),
                Subtype=pikepdf.Name.Artwork))))]),
        D=pikepdf.Dictionary(Order=pikepdf.Array([])))
    with pdf.open_metadata() as _:
        pass                                   # force the XMP packet to be written
    pdf.Root.Metadata = pikepdf.Stream(pdf, _xmp(b"xmp-creator-SECRET"))
    pdf.docinfo["/Author"] = "Author-SECRET"
    pdf.docinfo["/Title"] = "Title-SECRET"
    pdf.docinfo["/Keywords"] = "Keywords-SECRET"
    pdf.docinfo["/Producer"] = "TortureWriter 1.0"
    pdf.save(path, deterministic_id=True)
    return path


# --------------------------------------------------------------------------- #
# Reading the corpus back — used by the experiment and by callers that need to
# know what "the truth" was before a scrub.
# --------------------------------------------------------------------------- #
def pdftotext(path: str) -> str:
    """Extract text the way an investigator would. Empty string if it will not open."""
    p = subprocess.run(["pdftotext", "-q", path, "-"], capture_output=True)
    return p.stdout.decode("utf-8", "replace") if p.returncode == 0 else ""
