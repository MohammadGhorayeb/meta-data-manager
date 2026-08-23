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
# The A2 peer set: one document, many producers
# --------------------------------------------------------------------------- #
# Real prose, not a one-liner. A single line of text has no font-subset variety and
# almost no positioning operators, so a peer set built on one would show "no producer
# separation" for the reason that there is nothing to separate — the vacuous pass
# Phase 2's design rules exist to prevent.
SOURCE_TEXT = """Quarterly Review - Regional Operations

The programme closed the quarter ahead of schedule, with 4,182 units
delivered against a target of 3,900. Field teams in the northern and
coastal districts reported no material delays.

Budget variance stood at 2.4% under plan. The largest single line was
equipment leasing at 118,400, followed by transport at 61,250.

Recommendations:
  1. Extend the coastal contract by two quarters.
  2. Retire the legacy fleet before the next audit window.
  3. Consolidate reporting into a single monthly cycle.

Prepared for internal circulation only. Figures are provisional until
the external audit concludes in the following period.
"""


def _run_quiet(cmd, **kw) -> bool:
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=120, **kw)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return p.returncode == 0


def _synthetic(path: str, style: str, text: str | None = None) -> str:
    """A PDF built directly with pikepdf, in one of two deliberate house styles.

    These are **not padding**. `cupsfilter` is macOS-only and Chrome is unlikely on
    a CI runner, so without a producer pair that exists everywhere `check_evidence`
    would report every PDF A2 cell as `not_tested` on Linux and the published
    verdicts would go permanently unchallenged — limit #12, which exists because
    this project has been bitten by exactly that.

    The two styles differ on **both** channels, deliberately:
      * serializer — xref table versus xref streams, and whether objects are packed
        into object streams;
      * layout — `Td` with integer coordinates and one `Tj` per line, versus `Tm`
        with decimal coordinates and `TJ` arrays.
    The layout half is the honest stand-in for "a different typesetter", exactly as
    FLAC's compression levels stand in for "a different encoder": it is the same
    kind of choice a real layout engine makes, named as a stand-in rather than
    dressed up as two real engines.
    """
    import pikepdf

    lines = (text if text is not None else SOURCE_TEXT).strip().split("\n")
    body = [b"BT /F1 11 Tf"]
    for i, line in enumerate(lines):
        text = line.replace("\\", "").replace("(", "").replace(")", "").encode("latin-1")
        if style == "td_int":
            body.append(b"1 0 0 1 %d %d Tm (" % (72, 720 - 16 * i) + text + b") Tj")
        else:
            body.append(b"1 0 0 1 %.2f %.2f Tm [(" % (72.0, 720.0 - 15.75 * i)
                        + text + b")] TJ")
    body.append(b"ET")

    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    page.obj.Contents = pikepdf.Stream(pdf, b"\n".join(body) + b"\n")
    page.obj.Resources = pikepdf.Dictionary(
        Font=pikepdf.Dictionary(F1=pikepdf.Dictionary(
            Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
            BaseFont=pikepdf.Name.Helvetica if style == "td_int"
            else pikepdf.Name.Courier,
            Encoding=pikepdf.Name.WinAnsiEncoding)))
    pdf.docinfo["/Producer"] = f"SynthWriter-{style}"
    mode = (pikepdf.ObjectStreamMode.disable if style == "td_int"
            else pikepdf.ObjectStreamMode.generate)
    pdf.save(path, deterministic_id=True, object_stream_mode=mode)
    return path


def a1_variants(tmpdir: str, n_variants: int = 3, n_repeats: int = 5):
    """Same page, metadata differing only by a per-variant sentinel.

    A correct F1 collapses them to identical bytes -> A1 pass. The sentinel goes into
    both `/Info` **and** an XMP packet, because a PDF routinely carries the same value
    in both and clearing one is the classic half-scrub.
    """
    import pikepdf

    groups = []
    for i in range(n_variants):
        sentinel = chr(65 + i) * 6
        base = _synthetic(os.path.join(tmpdir, f"a1_v{i}.pdf"), "td_int")
        with pikepdf.open(base, allow_overwriting_input=True) as pdf:
            pdf.docinfo["/Author"] = f"Author-{sentinel}"
            pdf.docinfo["/Title"] = f"Title-{sentinel}"
            pdf.docinfo["/Keywords"] = f"GPS-{sentinel}-48.85N"
            pdf.Root.Metadata = pikepdf.Stream(
                pdf, _xmp(f"xmp-creator-{sentinel}".encode()))
            pdf.save(base, deterministic_id=True)
        with open(base, "rb") as f:
            variant = f.read()
        paths = []
        for r in range(n_repeats):
            p = os.path.join(tmpdir, f"a1_v{i}_r{r}.pdf")
            with open(p, "wb") as f:
                f.write(variant)
            paths.append(p)
        groups.append(paths)
    return groups


def diverse_inputs(tmpdir: str, n: int = 4) -> list[str]:
    """Small but genuinely varied PDFs for the fingerprint guard.

    Diversity is the guard's whole premise: it reports byte runs common to every
    output, on the assumption that anything shared must have come from the tool. Feed
    it four documents with the same object graph and it reports *their* shared
    structure as our signature — measured, on a first version of this function that
    varied only page size and text. So these vary page count, font, resource shape
    and whether an image is embedded, which changes the object graph itself.

    Small on purpose too: `common_substrings` is roughly O(n·m) and W0 measured it
    hanging past 120 s on two 300 KB PDFs. These are a few KB each, the same
    discipline `gen_matrix_m4a._diverse()` uses with its 0.6 s clips.
    """
    import pikepdf

    from tests.scrub import corpus as jpeg_corpus

    fonts = ["/Helvetica", "/Courier", "/Times-Roman", "/Symbol"]
    out = []
    for i in range(n):
        p = os.path.join(tmpdir, f"div_{i}.pdf")
        pdf = pikepdf.new()
        for page_index in range(1 + i % 3):             # 1, 2 or 3 pages
            page = pdf.add_blank_page(page_size=(400 + 37 * i, 500 + 53 * i))
            text = (f"Sample {i}.{page_index} " * (1 + i)).strip().encode("latin-1")
            if i == 0:
                # No font at all — vector marks only. A corpus where every document
                # has a `/Font` resource makes the page dictionary's shape common to
                # every output, and the guard correctly reports that as a constant.
                body = b"0.2 0.4 0.6 rg\n%d %d 120 80 re f\n" % (40, 300)
                resources = pikepdf.Dictionary()
            else:
                body = (b"BT /F%d %d Tf %d %d Td (" % (i, 8 + 2 * i, 30 + 7 * i,
                                                       400 + 11 * i)
                        + text + b") Tj ET\n")
                resources = pikepdf.Dictionary(
                    Font=pikepdf.Dictionary(**{f"F{i}": pikepdf.Dictionary(
                        Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
                        BaseFont=pikepdf.Name(fonts[i % len(fonts)]))}))
            if i % 2:                                    # half carry an image
                image = pikepdf.Stream(pdf, jpeg_corpus.make_base_jpeg())
                image.Type, image.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
                image.Width, image.Height = 64, 48
                image.BitsPerComponent, image.ColorSpace = 8, pikepdf.Name.DeviceRGB
                image.Filter = pikepdf.Name.DCTDecode
                resources.XObject = pikepdf.Dictionary(
                    **{f"Im{i}": pdf.make_indirect(image)})
                body += b"q 80 0 0 60 %d 100 cm /Im%d Do Q\n" % (20 + i, i)
            page.obj.Contents = pikepdf.Stream(pdf, body)
            page.obj.Resources = resources
        pdf.docinfo["/Producer"] = f"DiverseWriter-{i}"
        # Compression varies too: F1 passes raw stream bytes and their filter through
        # untouched, so a corpus compressed uniformly makes `/Filter /FlateDecode`
        # common to every output — the input's convention showing up as ours.
        pdf.save(p, deterministic_id=True, compress_streams=bool(i % 2))
        out.append(p)
    return out


# Distinct documents, for an attack that has to generalise across content.
#
# `producers()` above holds the document constant and varies the producer, which is
# what a *structural* A2 comparison needs. A pixel-space classifier cannot be fed that
# corpus: with one document, "which producer made this page" collapses into "which of
# these five images have I seen before", and leave-one-out would score 100% on memory
# rather than on any producer trait. So E-PDF-RASTER needs several documents per
# producer and a classifier that is never shown the held-out page's own document.
#
# The paragraphs differ in line count, line length and digit content, so glyph
# positions genuinely differ between documents rather than being the same page with a
# word changed.
_DOC_BODIES = [
    """Field teams in the northern and coastal districts reported no material
delays this period. Budget variance stood at 2.4% under plan, and the
largest single line was equipment leasing at 118,400.""",
    """Maintenance windows were consolidated into a single monthly cycle
beginning in the second period. Three of the eleven depots have not yet
migrated, and their schedules remain provisional pending the audit.
Transport costs fell to 61,250 from 68,900 in the prior comparison.""",
    """The legacy fleet is scheduled for retirement before the next audit
window opens.""",
    """Headcount closed at 214 against an approved establishment of 230.
Vacancy concentration remains highest in the coastal region, where six
of the fourteen open roles have been unfilled for more than two quarters.
Recruitment spend was 41,700 for the period, against 38,000 planned,
and the variance is attributed entirely to agency fees.""",
    """Inventory turns improved to 6.1 from 5.4. Shrinkage was 0.9% of
throughput, within tolerance but above the 0.6% recorded a year earlier.""",
    """A revised escalation path was agreed with the regional office and
takes effect immediately. Incidents at severity two or above now route
to the duty manager within fifteen minutes rather than one hour.""",
    """Capital projects: the coastal contract extension was approved for two
further quarters at 240,000. The depot consolidation study was deferred.
No other commitments were entered into during the period under review.""",
    """Compliance testing covered 38 of 40 required controls. The two
outstanding items are scheduled for the following cycle and neither is
rated as a material weakness by the external reviewer.""",
]


def _document_text(index: int) -> str:
    """One source document: a stable heading, a varying body, a stable footer."""
    body = _DOC_BODIES[index % len(_DOC_BODIES)]
    return (f"Quarterly Review - Section {index + 1}\n\n{body}\n\n"
            "Prepared for internal circulation only. Figures are provisional\n"
            "until the external audit concludes in the following period.\n")


def _produce_one(tmpdir: str, source: str, tag: str) -> dict[str, str]:
    """Render one source text through every producer this machine has.

    Returns producer -> path, omitting any producer that failed or is absent — the
    caller reports the gap rather than quietly shrinking the peer set.
    """
    out: dict[str, str] = {}
    for name, style in (("synth_td_int", "td_int"), ("synth_tm_real", "tm_real")):
        out[name] = _synthetic(os.path.join(tmpdir, f"{name}__{tag}.pdf"), style,
                               text=open(source, encoding="utf-8").read())

    if HAVE_CHROME:
        p = os.path.join(tmpdir, f"chrome_skia__{tag}.pdf")
        if _run_quiet([CHROME, "--headless", "--disable-gpu", "--no-first-run",
                       "--no-default-browser-check", "--no-pdf-header-footer",
                       f"--print-to-pdf={p}", source]):
            out["chrome_skia"] = p

    if HAVE_SOFFICE:
        sub = os.path.join(tmpdir, f"lo__{tag}")
        os.makedirs(sub, exist_ok=True)
        if _run_quiet(["soffice", "--headless", "--convert-to", "pdf",
                       "--outdir", sub, source]):
            p = os.path.join(sub, os.path.basename(source).replace(".txt", ".pdf"))
            if os.path.exists(p):
                out["libreoffice"] = p

    if HAVE_CUPSFILTER:
        p = os.path.join(tmpdir, f"quartz_cups__{tag}.pdf")
        with open(p, "wb") as fh:
            try:
                done = subprocess.run(["cupsfilter", source], stdout=fh,
                                      stderr=subprocess.DEVNULL, timeout=120)
            except (OSError, subprocess.TimeoutExpired):
                done = None
        if done is not None and done.returncode == 0 and os.path.getsize(p):
            out["quartz_cups"] = p
    return out


def documents(tmpdir: str, n_docs: int = 6) -> dict[str, list[str]]:
    """producer -> one path per distinct document, aligned by index across producers.

    A producer that could not render every document is dropped entirely rather than
    contributing a short row, so the classifier never sees an unbalanced peer set —
    an absent producer must make the cell say *not measured*, never *clean*.
    """
    per_doc: list[dict[str, str]] = []
    for i in range(n_docs):
        source = os.path.join(tmpdir, f"doc{i}.txt")
        with open(source, "w", encoding="utf-8") as f:
            f.write(_document_text(i))
        per_doc.append(_produce_one(tmpdir, source, f"d{i}"))

    complete = set.intersection(*(set(d) for d in per_doc)) if per_doc else set()
    return {name: [d[name] for d in per_doc] for name in sorted(complete)}


def available_producers() -> dict[str, bool]:
    """Which producers this machine can actually run. Absent ones are reported as
    absent so the matrix can say *not measured* rather than *clean*."""
    return {"chrome_skia": HAVE_CHROME, "libreoffice": HAVE_SOFFICE,
            "quartz_cups": HAVE_CUPSFILTER, "synth_td_int": True,
            "synth_tm_real": True}


def producers(tmpdir: str, repeats: int = 3) -> dict[str, list[str]]:
    """A2 peer set: the SAME source document through different PDF producers.

    Content is held constant and the producer varies — the shape every A2 peer set
    in this project takes. A producer that is not installed is simply missing from
    the returned dict; callers report that rather than quietly shrinking the set.
    """
    source = os.path.join(tmpdir, "source.txt")
    with open(source, "w", encoding="utf-8") as f:
        f.write(SOURCE_TEXT)

    out: dict[str, list[str]] = {}
    for name in ("synth_td_int", "synth_tm_real"):
        style = "td_int" if name.endswith("td_int") else "tm_real"
        out[name] = [_synthetic(os.path.join(tmpdir, f"{name}__r{r}.pdf"), style)
                     for r in range(repeats)]

    if HAVE_CHROME:
        paths = []
        for r in range(repeats):
            p = os.path.join(tmpdir, f"chrome_skia__r{r}.pdf")
            # No --user-data-dir: measured hanging indefinitely with one (Chrome
            # never finishes creating the throwaway profile), while the default
            # profile renders in ~2s. --print-to-pdf does not write to the profile.
            if _run_quiet([CHROME, "--headless", "--disable-gpu", "--no-first-run",
                           "--no-default-browser-check", "--no-pdf-header-footer",
                           f"--print-to-pdf={p}", source]):
                paths.append(p)
        if paths:
            out["chrome_skia"] = paths

    if HAVE_SOFFICE:
        paths = []
        for r in range(repeats):
            sub = os.path.join(tmpdir, f"lo{r}")
            os.makedirs(sub, exist_ok=True)
            if _run_quiet(["soffice", "--headless", "--convert-to", "pdf",
                           "--outdir", sub, source]):
                p = os.path.join(sub, "source.pdf")
                if os.path.exists(p):
                    paths.append(p)
        if paths:
            out["libreoffice"] = paths

    if HAVE_CUPSFILTER:
        paths = []
        for r in range(repeats):
            p = os.path.join(tmpdir, f"quartz_cups__r{r}.pdf")
            with open(p, "wb") as fh:
                try:
                    done = subprocess.run(["cupsfilter", source], stdout=fh,
                                          stderr=subprocess.DEVNULL, timeout=120)
                except (OSError, subprocess.TimeoutExpired):
                    done = None
            if done is not None and done.returncode == 0 and os.path.getsize(p):
                paths.append(p)
        if paths:
            out["quartz_cups"] = paths

    return {k: v for k, v in out.items() if len(v) == repeats}


# --------------------------------------------------------------------------- #
# Reading the corpus back — used by the experiment and by callers that need to
# know what "the truth" was before a scrub.
# --------------------------------------------------------------------------- #
def pdftotext(path: str) -> str:
    """Extract text the way an investigator would. Empty string if it will not open."""
    p = subprocess.run(["pdftotext", "-q", path, "-"], capture_output=True)
    return p.stdout.decode("utf-8", "replace") if p.returncode == 0 else ""
