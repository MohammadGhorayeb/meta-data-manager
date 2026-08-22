# Phase 3 Plan — Document Containers: PDF → OOXML

Phase 2 closed with audio fully characterised. Documents are the harder problem and
the more interesting one: this is where the project's flagged open question lives —
**no published tool achieves A2 at F2 for PDF** — and where the benchmark tools have
a documented, published failure we can measure ourselves (OOXML RSIDs survive every
surveyed scrubber, MAT2 included).

Leaves before containers, as always: PDF embeds JPEG (`DCTDecode`) and PNG image
streams, and DOCX embeds JPEG/PNG plus `docProps/thumbnail.jpeg`. Phase 1 built those
handlers, so the recursion is tractable now in a way it would not have been first.

Everything plugs into the Phase 0 harness: the scrubber is a `Scrubber` behind the
`scrub {in} {out} --fidelity` CLI, format knowledge lands as a `FormatPlugin`, and
"done" is a validated Pareto matrix in `tests/harness/results/`, never inspection.

**Pareto targets:**
- **PDF** — A1 at F1. A2 is the open question: characterise honestly what F2
  (structural rewrite, no rasterisation) can and cannot reach, and only claim what
  the differential test supports. F3 = rasterise-and-rebuild (Dangerzone's approach),
  which trivially defeats A2 by destroying the document's text layer — a real option
  with a real cost, so it is offered rather than assumed.
- **OOXML/DOCX** — A1 at F1/F2. The named target is **RSIDs**, which no surveyed tool
  removes; clearing them is a benchmark row we win outright, exactly as M4A was.

Tooling: **pikepdf 10.8.0 / qpdf 12.3.2** is already a dependency and is the
implementation candidate for PDF (Apache-2.0, so bundle-friendly — unlike Ghostscript
and exiv2). No `qpdf`, `gs` or `pdftk` binary is installed, but **poppler 26.04 is**
(`pdftoppm`, `pdfimages`, `pdffonts`, `pdfdetach`) — that is what the F3 rasteriser
will shell out to, on the same never-linked-always-subprocess footing as `ffmpeg`,
`lame` and `jpegtran`. DOCX is a ZIP of XML, so the standard library covers it;
`olefile`/`oletools` only if legacy `.doc` enters scope.

---

## 0. Measured ground truth (W0 spike)

Everything below was measured against the pinned pikepdf 10.8.0 / qpdf 12.3.2, not
assumed. It exists because the phase's central design question — *do we let qpdf write
our bytes?* — is answerable in an afternoon and unanswerable by reading.

**The scrubber-fingerprint guard fails qpdf output.** Running the project's own
`fingerprint_guard.common_substrings` over four pikepdf-written files (tiny synthetic
PDFs, 746–1511 bytes) yields 616 common runs, 175 of them introduced by the tool, and
**3 maximal signatures** after the guard's own subtraction:

| bytes | signature |
|---:|---|
| 103 | `%PDF-1.3\n%\xbf\xf7\xa2\xfe\n1 0 obj\n<< /Pages 3 0 R /Type /Catalog >>…` |
| 89 | the xref table's free-list and entry formatting |
| 40 | ` 0 obj\n<< /Filter /FlateDecode /Length 4` |

The first is qpdf's constant 4-byte binary header comment plus its catalog-first object
layout. ISO 32000 §7.5.2 says a producer *should* emit a binary comment; the specific
bytes are arbitrary and are themselves a per-producer tell (the Skia-written
`docs/p1_report.pdf` uses `%\xd3\xeb\xe9\xe1`). This is not patchable piecemeal — the
signature spans the header *and* the object layout — and swapping in our own constant
just makes it our constant, which is precisely the mistake `flac/f1.py:43-47` records.

**`/ID[0]` is inherited from the input and survives the scrub.** This was not on the
original locus list and is the sharper finding. qpdf preserves the *original document
ID* and regenerates only `/ID[1]`, so a scrubbed file still carries the identifier that
links it to every other revision of the same document — an A1 leak that a `/Info`-and-XMP
scrub leaves completely intact. Neither the default save nor `deterministic_id=True`
removes it.

**`/ID` cannot be removed through the pikepdf API** — qpdf rewrites the array whatever
the trailer says. It *can* be removed by byte surgery on a classic-xref file, verified:
the trailer dictionary sits after the xref table and `startxref` points at the xref
offset, so shrinking the trailer shifts nothing any offset references, and the result
re-opens with `attempt_recovery=False` and zero warnings. That does not hold for
xref-stream files, where `/ID` lives inside the compressed stream.

**`static_id=True` writes `31415926535897932384626433832795`** — qpdf's hardcoded pi
constant, identical across different input documents. It identifies the library, not
merely "scrubbed", and is strictly worse than the empty Vorbis comment the FLAC guard
caught. (This is masked on any input that already has an `/ID`, because inheritance wins;
it only shows up once the input genuinely has none. Worth knowing before someone
"verifies" it on the wrong file and concludes the flag is safe.)

**Corrected from the planning pass:** a default save is **deterministic**, not random —
byte-identical across a wall-clock gap and across five separate interpreter processes
with varied `PYTHONHASHSEED`. The default `/ID` is content-derived. Set
`deterministic_id=True` explicitly anyway rather than depending on an undocumented
default, but the harness's repeat-floor is not in danger from this.

Other measured settings, all load-bearing:

- `pikepdf.open` defaults are **fail-open** (`attempt_recovery=True`,
  `suppress_warnings=True`), contrary to the doctrine in `src/scrub/errors.py`. Open with
  both off and treat any warning as `ParseError`.
- `pdf.open_metadata()` stamps `pikepdf 10.8.0` into XMP *and* `/Info`, plus a wall-clock
  `xmp:MetadataDate`. Delete `Root.Metadata` as a raw object; never call it unguarded.
- `compress_streams=False, stream_decode_level=none` preserves the raw stream multiset
  exactly — F1's mechanism holds.
- `normalize_content=True` **changes decoded page content** and inflated a 295 KB file to
  507 KB. Unusable for F2; our content-stream canonicalisation has to be our own.

### What MAT2 actually does to a PDF (measured, not read from source)

Run on `docs/p1_report.pdf` with MAT2 0.14.0, text recovered with `pdftotext`:

| | size | text recovered |
|---|---:|---:|
| original | 295 KB | 6,923 chars |
| `mat2 --inplace` (default) | **1,297 KB** | **5 chars** |
| `mat2 --inplace --lightweight` | 225 KB | 7,105 chars |

So the widely-repeated "MAT2 destroys PDF text" is true of the **default** path only —
which renders every page to a 200 DPI PNG and embeds it, quadrupling the file. The
`--lightweight` path re-renders through a Cairo `PDFSurface`, which emits real
text-showing operators and subset fonts, and text survives intact.

The defensible benchmark claim is therefore narrower than "MAT2 destroys text", and still
strong: **MAT2 offers no bit-preserving and no lossless-structural tier for PDF at all —
both of its paths are re-renders.** F1 and F2 are capability it does not have, at either
setting. Write it that way in `docs/benchmark.md`, or we repeat the overclaim pattern
that `docs/limits.md` exists to prevent.

### The W0 decision: we write the serializer

`src/scrub/formats/pdf/serialize.py`. pikepdf/qpdf stays as the object-graph reader and
semantic layer; we emit the bytes. This is the doctrinally consistent answer —
`standards/isobmff.py` has both `parse()` and `serialize()`, `flac/f1.py` writes block
headers by hand, and PDF would otherwise be the first format where we outsource byte
layout to a third party and then argue past our own guard. It also settles `/ID`, the
header comment, whitespace, `/Length` direct-vs-indirect and object ordering in one
place instead of four workarounds. We already need a walker that computes offsets; the
serializer is its mirror.

The alternative — keep qpdf, declare its header comment and `/ID` in
`mandatory_constants()`, and widen limit #9 from "recognisably cleaned" to "recognisably
cleaned *by a qpdf-family tool*" — is recorded here as the road not taken, because it
publishes a materially weaker claim than PNG and FLAC already do.

One practical note for whoever builds the guard corpus: `common_substrings` is roughly
O(n·m) and hung past 120 s on two 300 KB PDFs. Use small inputs, exactly as
`gen_matrix_m4a._diverse()` uses 0.6 s clips. And PDF syntax *is* ASCII keywords, so at
`min_len=4` the guard surfaces `obj`, `endobj`, `xref`, `/Type`… — `mandatory_constants()`
for PDF is a real design problem, not a five-line list.

---

## 1. Work items (dependency order)

### W1 — PDF structure walker, serializer, and content-stream tokenizer
`src/scrub/formats/pdf/`. Three modules, because W0 put byte emission in our hands:

**`walker.py`** — enough of the file structure to account for every byte and fail closed
on anything unexplained, mirroring the MP3/FLAC/ISOBMFF walkers:
- header, cross-reference table or stream, trailer, and **every `startxref` chain**;
- object inventory with generation numbers;
- **incremental-update sections** — the critical one, see W3;
- object streams (`ObjStm`) and cross-reference streams, which hide objects from a
  naive scan;
- linearisation ("fast web view") artefacts, which are themselves a producer tell;
- junk before `%PDF` (the spec allows 1024 bytes, and offsets become relative to it)
  and anything after the final `%%EOF`;
- **hybrid-reference files** — a classic trailer carrying `/XRefStm` has *two* cross-
  reference structures that may point at different objects, so old and new readers see
  different documents. A genuine steganographic channel. Cheap detector: open with
  `ignore_xref_streams=True` versus the default and diff the object graphs.

**`serialize.py`** — the mirror of the walker, per the W0 decision. Deterministic object
numbering, our own (or no) header comment, no `/ID`, fixed whitespace and xref style.

**`content.py`** — a content-stream tokenizer, needed three times over: **inline images**
(`BI … ID <binary> EI`) are invisible to any object-graph walk and pikepdf will not find
them; F2's canonicalisation needs it; the redaction detector (W7) needs it.

Frame the two-parser design as an **accounting ledger**, not "disagreement is the
detector" — qpdf silently repairs broken xrefs and silently drops orphans, so
disagreement is guaranteed on almost any real file. The walker enumerates every revision
and body object, pikepdf gives the reachable set, the difference is the *removed* set,
and we then assert that set is absent from the output. That is the shape `m4a/f1.py`
already uses for `mdat`.

### W2 — PDF F1, **recursive from day one**
F1 rebuilds the document to a single revision — history cannot be removed by appending —
while preserving content. The invariant is **not** "raw stream bytes identical". That
reading would leave every embedded JPEG's EXIF and thumbnail in place, which is failure
mode #4, and `cli.scrub_file` would then either raise `ContentError` on every file (tier
unusable) or ship a false claim. The invariant is: **each embedded leaf's content-bearing
payload is bit-identical per that leaf format's own F1 definition.** Recursion is part of
F1's definition here, not a later work item, because any ordering that ships F1 before
recursion ships a false claim in between.

Direct loci:
- `/Info` dictionary — Title, Author, Subject, Keywords, Creator, **Producer**,
  CreationDate, ModDate, plus arbitrary custom keys.
- **XMP metadata** — reuse `src/scrub/standards/xmp.py` from Phase 1 rather than
  writing a second parser. PDFs routinely carry BOTH `/Info` and an XMP packet with
  the same values, so clearing one is the classic half-scrub. Note XMP can hang off
  *any* object — XObjects, fonts, the Catalog — not just the document. `xmpMM:History`
  and `xmpMM:DerivedFrom` are a full provenance chain, and an `/Info` ↔ XMP disagreement
  is itself forensic evidence.
- **`/ID[0]`**, which W0 showed is inherited through a qpdf rewrite and survives.
- Embedded file attachments (`/EmbeddedFiles`), which can be *anything* — recurse through
  `dispatch.default_dispatcher()`, re-entrant with a depth limit, because a PDF can
  attach a PDF.
- Annotations: `/T` (the author's name), `/M`, `/RC`, `/Popup`, `/IRT`; and **`/AP`
  appearance streams**, which can still render text that `/Contents` no longer says.
- `/AcroForm` + **XFA** — field values, `/DR`, and XFA's `<xfa:datasets>` XML carrying
  filled data and the template author.

Loci that are easy to miss, and each of which is a named test case:
- **Page `/Thumb`** — the per-page embedded thumbnail. The exact PDF analogue of the JPEG
  thumbnail leak `framework.md:39` calls the most commonly missed locus.
- **Inline images** — invisible to object-graph walks (see W1).
- **`/PieceInfo`** — Illustrator and InDesign stash the *entire private source document*
  in `AIPrivateData`, on the Catalog, on each page, and on form XObjects.
- **`/SMask` / `/Mask`** — a soft mask can retain content removed from the base image.
- **`/OCProperties → /OCGs → /Usage → /CreatorInfo`** — literally names the creating app.
- **Font internals** beyond the subset tag: `OS/2.achVendID` and `fsType`, `post`, CFF
  `Notice`/`FullName`, `DSIG`, TrueType `head.created/modified`, **`/ToUnicode` CMaps**,
  and **subset glyph order**, which is order-of-first-use and so partially reconstructs
  text order.
- **`/FileSpec /F` and `/UF`, `/GoToR` and `/Launch` targets** — full local filesystem
  paths. `/Link /A /URI` leaks intranet URLs.
- **`/SpiderInfo`** — web capture records the source URL and capture time of every page.
- **Filter chain composition** (`[/ASCII85Decode /FlateDecode]` versus bare
  `/FlateDecode`), `/DecodeParms /Predictor`, and `/Length` direct versus indirect — all
  classic producer tells.
- **The `%PDF-1.x` version header itself**, which a rewrite preserves from the input
  unless we pin it.
- **Signature `/Contents`** — the PKCS#7 blob holds the signer's full certificate chain;
  a *removed* signature leaves a zeroed `/ByteRange` gap that is itself a tell.
- Trailer `/Size` exceeding the highest used object number reveals deletions.
- `/Lang`, `/ViewerPreferences`, `/MarkInfo`, `/PageLabels`, `/Outlines`, `/AA`, and
  `/StructTreeRoot`'s `/RoleMap`, `/ClassMap`, `/Alt` and `/ActualText` — the last two
  can hold text not visible on the page.

Recursion, which is what makes this F1 rather than a tag wipe:
- `DCTDecode` → `jpeg.f1.scrub()`; the entropy-coded scan stays bit-identical. Its output
  is shorter (the trailer is dropped), so `/Length` must be rewritten — the direct
  analogue of `m4a/f1.py` patching `stco`/`co64` after boxes move.
- `FlateDecode` image XObjects → decoded pixel bytes identical.
- `/ICCBased` → `standards/icc.py:sanitize()`, which already exists and already
  recomputes the profile ID.
- `/FontFile2` / `/FontFile3` → table-level strip, glyf and CFF charstrings untouched.

Assert **per-object, not per-multiset**: walk both graphs in the same structural order
(page → `/Contents`, page → `/Resources/XObject/*`, `/FontFile*`, `/ICCBased`) and compare
pairwise, so a transposition cannot pass. Read it like `m4a/f1.py`'s
`out_mdat.payload != audio → raise`.

`residuals()` must **not** port the FLAC/M4A magic-scan pattern. A whole-file
`\xff\xd8\xff` scan hits inside every Flate stream by chance *and* inside every
legitimately embedded JPEG. Scan decoded stream content, per stream, classified by filter
and role — the same lesson `m4a/f1.py:137` already records for its metadata-region-only
scan.

### W3 — Incremental-update history (the leak that defines this phase)
A PDF is edited by **appending** a new revision, leaving the old objects in the file.
Text "deleted" three revisions ago is still there in full, and this is the single most
famous PDF disclosure mode — redactions published with the original text one layer
down. Consequences for us:
- the scrub must **rewrite** the document, never append — an appended cleanup leaves
  the dirty original directly above it in the same file;
- the residual check must confirm no earlier revision survives, by walking the
  `startxref` chain and asserting exactly one;
- **redaction is not scrubbing**: even in a rewritten file, text covered by a black
  rectangle is still in the content stream. Out of scope to *fix*, but it must be
  *said*, because users assume otherwise. See W7 for the detector, and note the
  attribution correction there.

### W4 — `PdfPlugin` + the A2 channel, split by producer
PDF carries **two producers in one file**, exactly as M4A does, and the A2 cells must
name which one leaked rather than averaging them:

- the **serializer** — object ordering and numbering, xref style (table versus stream),
  `ObjStm` usage, compression choices, header comment bytes, the `/ID` scheme,
  linearisation, whitespace and `/Length` conventions;
- the **layout engine** — the content-stream operator sequence, font subsetting, and
  glyph geometry.

`structural_features` therefore reports two named key groups, mirroring the
`structural_features` / `coded_audio_digest` split in `tests/harness/plugins/m4a.py`.

Do **not** over-apply the M4A lesson here. That fix kept `struct:size` as a separately
named `_ENCODER_KEYS` channel and moved only the coded-content *digest* out; the lesson
was "don't judge a format against its own compressed content in the categorical
channel", not "drop side channels". Because the peer corpus holds the **source document
constant** across producers, size is a legitimate producer signal for PDF and excluding
it would be hiding evidence. Note also that `tests/harness/oracle/fields.py:99` hardcodes
`struct:size` before consulting the plugin — the plugin cannot exclude it, only an
experiment's key tuple can.

### W5 — PDF F2, and the honest A2 answer
Canonical re-serialisation through our own writer (W1) so object order, compression and
xref style come from one producer — **plus content-stream canonicalisation plus
font-internals normalisation**. qpdf's own `normalize_content` is not an option: W0
measured it changing decoded page content and inflating a 295 KB file to 507 KB.

The layout channel is **not** monolithic, and treating it as such is the single most
likely way this phase publishes a wrong residual. Normalisable at F2 with no rendering
change: number precision and formatting, operator token style, `Td`/`TD`/`Tm` choice,
`Tj` splitting granularity, subset tag naming, font table internals, resource naming,
filter chain composition, the version header, MediaBox/CropBox precision. **Not**
normalisable without re-typesetting: glyph positions and advances, line-break points,
word spacing, and which glyphs are in the subset and in what order.

So the honest F2 residual is **glyph geometry**, not "the layout engine". **Do not accept
an A2@F2 fail until content-stream canonicalisation actually exists** — otherwise the
published residual is an artefact of unfinished work rather than a measured limit, and
`scripts/check_evidence.py` will happily re-confirm it forever.

### W6 — PDF F3 (rasterise) + E-PDF-RASTER
`pdftoppm` renders each page at a pinned DPI and pikepdf rebuilds. This is the same
technique as MAT2's default path and Dangerzone, and the plan should say so: the
contribution of this phase is F1 and F2, not F3.

Structural A2 should pass. The residual moves into pixel space — **glyph geometry
surviving rasterisation**: positions, advances, line breaks, font choice, all inherited
from the original producer's layout. Poppler's own hinting is uniform across every file
we emit, so that part is anonymity-within-class, exactly like `jpeg/f3.py`'s libjpeg DQT.

**Measure it, don't assert it.** E-PDF-RASTER classifies the producer from glyph-position
and ink statistics, with `controls_valid()`. And test the **DPI knob**: sub-pixel glyph
position is what carries the signal, so if the classifier falls to chance at 150 DPI but
not at 300, an impossibility assertion becomes a Pareto trade — which is the kind of
result this project is built to produce.

### W7 — Redaction-risk detector (`formats/pdf/redaction.py`)
Warns, never fixes. Scope v1 to three cheap, unambiguous checks: text rendering mode 3
or 7 (invisible), text whose bounding box falls outside the `CropBox`, and text drawn
before a filled rectangle that covers it in the same content stream with no transparency.
"Text under opaque fills" in full generality needs graphics state, blend modes,
transparency groups and z-order — a mini renderer, and it will eat the phase.

Two things to get right:
- **Do not cite Bland for this check.** Bland et al. 2023 is glyph-*advance* recovery —
  the surviving neighbours of a removed word constrain what the word was, an
  A3-flavoured attack. This detector addresses the naive TSA/NSA-style failure where the
  glyphs are simply still there. Cite Bland for the residual, not for the check.
- **Do not wire it into `verify()`.** `cli.scrub_file` raises `ContentError` on any
  residual and writes no output; a redaction *risk* is a property of the input's content,
  not a scrub failure, and blocking the scrub over it is wrong. Expose
  `risks(data) -> list[str]` and surface it through a separate reporting path
  (`scripts/scrub_flow_report.py` is the precedent), not the exit-code map that
  `tests/scrub/test_cli_dispatch.py` asserts on.

### W8 — OOXML/DOCX walker + F1
A ZIP of XML parts. The traps, each of which has bitten real tools:
- **ZIP local-header and central-directory timestamps must match after a rewrite**, or
  the file is subtly corrupt;
- `docProps/core.xml` (creator, lastModifiedBy, revision), `app.xml` (Company,
  Template, TotalTime), `custom.xml`;
- **RSIDs** in `settings.xml` and throughout `document.xml` — revision-save IDs that
  link edits to editing sessions, and **survive every surveyed scrubber including
  MAT2**;
- `people.xml`, comments, and tracked changes with author names;
- embedded media (reuse Phase 1) and **`docProps/thumbnail.jpeg`**, a rendered preview
  of the document's first page with its own metadata;
- the macOS AppleDouble ZIP entries that crashed a tool during Step 2 research —
  a robustness target, not just a metadata one.

### W9 — `DocxPlugin` + E-RSID
`structural_features`: RSID inventory, ZIP entry order, compression levels per entry,
timestamp scheme. E-RSID then measures the benchmark claim directly: do RSIDs survive
MAT2 (expected: yes) and our F1 (target: no)?

---

## 2. Experiments

| # | Question | Status |
|---|---|---|
| **E-PDF-HISTORY** | Does an earlier revision survive our scrub? Build a PDF with N incremental updates, scrub, and carve for text that was "deleted" in revision 1. | ✅ baseline measured (§2.1); our own row lands with M2 |
| **E-PDF** | Do object order, xref style and font-subset tags identify the producing application, and does the canonical rewrite (F2) erase them? Reported per channel — serializer versus layout. | ✅ at `raw`/`F1` (§2.3); the F2 column needs M4 |
| **E-PDF-LAYOUT** | After F2 normalises the serializer, is the producer still recoverable from the **content-stream operators**? The `E-ENGINE` analogue, in operator space. | 🔜 |
| **E-PDF-RASTER** | After F3 destroys the operators, is the producer still recoverable from the **rendered pixels**? And does lowering DPI kill it? | 🔜 |
| **E-RSID** | Do RSIDs survive MAT2 and ExifTool, and does our F1 clear them? The published claim, measured on our own corpus. | 🔜 |
| **E-DOCX-THUMB** | Is `docProps/thumbnail.jpeg` cleared, and is its own EXIF cleared with it? | 🔜 |

### 2.1 E-PDF-HISTORY, measured (M1)

`tests/scrub/pdf_corpus.py` + `tests/scrub/e_pdf_history.py`. The corpus is written
**by hand, byte by byte**, for a reason that only became obvious once tried: pikepdf
*cannot* append an incremental update — it always rewrites to a single revision, which
is exactly the behaviour under test. A corpus built with it could not express history
at all. It is also kept clear of `src/scrub/formats/pdf/`, so no experiment ends up
measuring the scrubber against its own misunderstanding of the format.

Three attacks, because each defeats a different bad fix:

- **rollback** — truncate after an earlier `%%EOF`, open the prefix, read it with
  `pdftotext`. Recovers the earlier draft *as a document*.
- **carve** — raw byte search, which catches a tool that breaks the `/Prev` chain
  while leaving the old object bytes in place.
- **object ledger** — object definitions physically present versus reachable from the
  trailer, split into **superseded** (a number defined more than once — what an
  incremental update leaves) and **orphaned** (present but unreachable). An
  orphan-only check calls the raw corpus clean, because every number in it is still
  reachable; the stale thing is the *earlier definition* of a live number.

Measured on a 3-revision document (secrets in revisions 1–2, public text in 3):

| cleaner | revisions | stale objs | text | recoverable |
|---|---:|---:|---|---|
| untouched (control) | 3 | 4 | kept | every planted secret, and both earlier `/Info`s |
| pikepdf/qpdf rewrite | 1 | 0 | kept | **nothing** |
| MAT2 default | 2 | 9 | **destroyed** | MAT2's own `/Producer` + wall-clock date |
| MAT2 `--lightweight` | 2 | 9 | kept | MAT2's own `/Producer` + wall-clock date |
| ExifTool `-all=` | **4** | 5 | kept | every planted secret, and the `/Info` it "cleared" |

Three findings worth carrying forward:

1. **A whole-document rewrite is the entire mechanism.** The plain pikepdf rewrite
   strips no metadata whatsoever and still leaves nothing recoverable. So when F1
   lands, the credit for defeating history belongs to the rewrite, not to the scrub —
   which is why the two are measured separately here, before F1 exists to conflate
   them.
2. **ExifTool `-all=` *adds* a revision.** It edits PDFs by appending, so it removes
   nothing at all; it warns about this itself ("PDF edits are reversible. Deleted
   tags may be recovered!"). A user who reaches for the obvious tool gets a file that
   is bigger, looks clean, and still contains every draft.
3. **MAT2 clears `/Info` by appending an incremental update of its own.** The
   document's own history really is destroyed by the re-render — genuine capability,
   and better than ExifTool here. But roll back one revision and MAT2's output names
   `cairo 1.18.4` and carries a wall-clock `CreationDate` **with the operator's UTC
   offset**: when the scrub was run, to the second, and roughly where. Reproduced on
   a real 295 KB PDF as well as the synthetic corpus, on both MAT2 paths. By this
   project's own definition of done (no producer string, no mtime stamping) that
   output fails the fingerprint guard — so this is a benchmark row, in
   `docs/benchmark.md` Evidence 6.

What M1 does **not** settle, recorded so the phase never overstates it: redaction.
`pdf_corpus.redacted_pdf()` is a *single-revision* file with text under an opaque
black rectangle. Collapsing revisions does nothing for it — there is no history —
and `pdftotext` reads the secret straight out. Different leak, W7's problem, and it
has a test of its own so the distinction cannot quietly erode.

### 2.2 M2 as built — walker, serializer, tokenizer, recursive F1

`src/scrub/formats/pdf/{walker,serialize,content,f1,handler}.py`, registered in
dispatch. Tests in `tests/scrub/test_pdf.py`; the harness plugin is
`tests/harness/plugins/pdf.py`.

**Verified against six real producers** — Skia (Chrome and the project's own reports),
LibreOffice, macOS Quartz via `cupsfilter`, cairo via MAT2 (xref *streams* and object
streams), ExifTool's incremental output, and the synthetic corpus. Text is
byte-identical through `pdftotext` in every case, output is one revision, and the
serializer's own orphan check passes.

The binary header comment turns out to be a clean per-producer constant, which is the
sharpest confirmation of the W0 decision: Skia `%\xd3\xeb\xe9\xe1`, cairo
`%\xb5\xed\xae\xfb`, LibreOffice `%\xc3\xa4\xc3\xbc\xc3\xb6\xc3\x9f`, Quartz a
twelve-byte one, qpdf `%\xbf\xf7\xa2\xfe`. We emit none.

**Four bugs the corpus caught, each worth keeping in mind:**

1. **Direct dictionaries were invisible.** The first strip pass walked only *indirect*
   objects, so `/OCProperties → /OCGs → /Usage → /CreatorInfo` and an annotation's
   `/A` launch action — both ordinarily direct sub-dictionaries — survived. The
   residual check shared the blind spot and reported clean. Both now use one
   generator: **a verifier with less reach than the scrubber cannot see what the
   scrubber missed.**
2. **`EI` occurs inside JPEG data.** The inline-image terminator scan found a
   whitespace-bounded `EI` inside the embedded image, truncating it and mis-parsing
   the rest of the page. Fixed by starting the scan after the JPEG's own `EOI`,
   which the JPEG walker already knows how to find. Not a corner case — it happened
   on the first torture file built.
3. **Tokenizing a window walks into binary.** The inline dictionary reader tokenized
   a 512-byte lookahead to get one token, and that window runs straight into the
   image payload. Hence `next_token()`: read one token, advance, stop at `ID`.
4. **Indirect `/Length` would have emitted an orphan.** cairo writes `/Length` as a
   separate object; the traversal numbered it, the dictionary rendered `/Length`
   direct, and nothing then referenced the object. Caught by turning the accounting
   ledger on our own output — a serializer that emits an unreferenced object is
   producing exactly the residue this phase removes.

**Determinism** is checked out of process with `PYTHONHASHSEED` varied, per §5: the
in-process floor test shares one hash seed across all five repeats, so a `set`-ordered
output path would look perfectly deterministic there and differ on the next CLI call.

**What F1 refuses** rather than half-scrubbing: encrypted, signed, XFA, attachments,
junk before the header, hybrid `/XRefStm`, and anything after the final `%%EOF`.
Encryption is checked from the bytes *before* opening — pikepdf raises `PasswordError`
first, so a later check would never run and the user would get a password complaint
instead of the reason.

**Residuals, measured and now in `docs/limits.md` (#13–#15):** embedded font programs
keep `name`/`OS/2`/`head` (measured: `head.created` intact on a scrubbed LibreOffice
file), and ICC profiles keep their descriptive tags while the header is already
zeroed. Both name the foundry or the platform rather than the author — an A2 channel,
and M4's problem.

### 2.3 E-PDF, measured at `raw` and `F1` (M3)

`tests/scrub/e_pdf.py`, `tests/harness/plugins/pdf.py`, matrix in
`tests/harness/results/pdf_irreversible_scrubber.json`.

**Peer set: five producers, one document.** Chrome/Skia, LibreOffice, macOS Quartz via
`cupsfilter`, and the two pikepdf synthetics — which differ deliberately on *both*
channels (xref table vs xref streams; `Td` with integer coordinates and `Tj` versus
`Tm` with decimals and `TJ`). The layout half of that is the honest stand-in for "a
different typesetter", exactly as FLAC's compression levels stand in for "a different
encoder", and it is named as a stand-in rather than dressed up as two real engines.
Extracted text is byte-identical across all five, so content really is held constant.

| channel | `raw` | `F1` |
|---|---|---|
| **serializer** | header, binary comment, xref kind, object streams, trailer keys, indirect `/Length` | **nothing — closed** |
| **layout** | operators, number precision, font subsets, glyph digest, stream count | all five still leak |
| **size** | leaks | leaks |

Controls valid on all three channels. So **A2@F1 = fail, and the failure is precisely
and only the layout engine plus size** — which is what M4 is built from. "A2 fails"
alone would have said the same thing whether F1 had closed one channel or neither.

Two corrections made before this was published, both the kind that would have shipped
a wrong claim quietly:

- **`indirect_lengths` counted streams, not indirect lengths.** The feature's name
  claimed a serializer trait while its computation measured a layout one, so the
  first run reported the serializer channel as still leaking after F1. It is now
  `/Length N 0 R` matched properly, with stream count split out under *layout* —
  where it belongs, since what a file embeds is the typesetter's decision.
- **The fingerprint guard failed three times before it passed**, and only the third
  cause was ours. First the "diverse" inputs were four near-identical documents, so
  the guard reported *their* shared structure as our signature. Then, with real
  diversity, what remained was the PDF skeleton — which is the `mandatory_constants()`
  problem W0 predicted: PDF's syntax *is* ASCII keywords, and unlike FLAC's short
  magic prologue its mandatory skeleton is interleaved through the whole file.
  Resolved by declaring **the empty document our own writer emits**, generated rather
  than transcribed: any run common to every output that is a substring of a document
  with no content is by construction pure structure. Because that declaration is
  broad, `test_matrix_pdf.py` checks it rather than trusting it — the skeleton must
  contain no producer string, no timestamp and no padding run, so adding a
  `/Producer` tomorrow fails a test even though the guard would still pass. The last
  two residuals were corpus artefacts again (every document had a font, every stream
  was Flate-compressed), fixed by varying both.

**What M4 now has to normalise**, in the order W5 predicts it can: number formatting
and operator token style (losslessly), then `Td`/`TD`/`Tm` choice and `Tj` splitting,
then font-table internals and subset tag naming. **Glyph geometry — where each glyph
actually sits, and which glyphs are in the subset — is the predicted floor**, and
`struct:glyph_digest` is already the key that will report whether F2 clears it. The
test asserts that key is leaking today, so an F2 that "passes" by breaking the
measurement rather than the leak cannot slip through.

Phase 2's design rules carry over and are not negotiable: **assert the controls** (if
the attack cannot identify the producer of an unscrubbed file, its failure on a
scrubbed one proves nothing); **verdicts are significance tests, not thresholds** on a
statistic with a wide standard error; and **content that carries the signal** —
a one-line PDF has no font-subset variety to fingerprint.

**The peer corpus** is the same source document rendered by different producers — Chrome
headless (Skia), LibreOffice, and macOS Quartz via `cupsfilter` — **plus two synthetics
built directly with pikepdf that differ in serializer choices**. The synthetics are not
padding: `cupsfilter` is macOS-only and Chrome is unlikely on the CI runner, so without
them `check_evidence.py` would report every A2 cell as `not_tested` on Linux and the
published verdicts would go permanently unchallenged. Absent producers make the matrix
say *not measured*, never *clean* — the `shineenc` pattern, and limit #12 already exists
because this project has been bitten by exactly this.

### 2.4 M4 as built — F2, and the honest A2-at-F2 answer

`src/scrub/formats/pdf/canon.py` (content-stream canonicaliser),
`src/scrub/formats/pdf/f2.py` (the tier), matrix in
`tests/harness/results/pdf_irreversible_scrubber.json`.

W5's rule was: do not accept an A2@F2 fail until content-stream canonicalisation
actually exists. It exists now, so the fail below is a measurement rather than an
artefact of unfinished work.

**What F2 normalises.** One content stream per page (ISO 32000 §7.8.2 — the reader
concatenates them anyway); number spelling; `Td`/`TD`/`T*` folded into the absolute
`Tm` they amount to; consecutive shows merged into one `TJ`; canonical strings, names
and whitespace; one compression filter at one level; font subset tags reassigned in
first-appearance order.

**The rewrite needs no font metrics, which is what makes it safe.** `Td`/`TD`/`T*` are
relative to the *line* matrix `Tlm`, and showing text advances only `Tm` — so `Tlm` is
trackable exactly without glyph widths. The one case that would need widths is a
second show after the first has advanced `Tm`, and that is handled by *merging* the
run rather than computing where it got to.

| channel | `raw` | `F1` | `F2` |
|---|---|---|---|
| **serializer** | leaks | **closed** | closed |
| **layout** | all five leak | all five leak | **text-operator vocabulary collapses to one value for four of five**; `glyph_digest`, `font_subsets`, `number_style`, `stream_count` and the graphics vocabulary still leak |
| **size** | leaks | leaks | leaks |

So **A2@F2 = fail, and what survives is substance rather than style.** The two
synthetics — built to differ on serializer *and* on layout spelling — become
indistinguishable on the text channel, which is the result the split was added to be
able to see. `struct:text_operators` was added to the plugin for exactly the reason M3
split serializer from layout one level up: a single `operators` key reports the same
verdict whether F2 collapsed most of the channel or none of it. `struct:operators`
stays alongside it, so nothing is hidden by the split.

The one producer that still differs on text vocabulary is macOS Quartz, and it differs
because it genuinely sets `Tc`. Dropping the operator would make the cell pass and
change the rendering — the wrong trade, so the leak stays and is named.

**Content preservation is checked in pixel space, not only against ourselves.**
`canon.painted()` — glyphs shown in order, non-rewritten operators with operands, and
the replayed absolute position and spacing at each show — shares a state machine with
the rewriter, so agreeing with it proves less than agreeing with poppler. All five
producers render **byte-identical PNGs at 150 DPI** before and after F2, and extracted
text is unchanged. A negative-control test mutates canonical output four ways (a glyph
changed, a line moved 1 pt, a leading swallowed, a drawing operator dropped) and
asserts the invariant catches each, so the content promise cannot pass vacuously.

Three things went wrong before this was published, all of the shipping-a-wrong-claim
species:

- **`need_tm` was cleared on an empty flush.** A `Tf` between a `Td` and the text it
  positions dropped the pending move, so the rewritten `Td` never became a `Tm` and
  the line rendered at the previous position. Silent, and invisible to any check that
  only compares which glyphs were painted — which is why `painted()` carries replayed
  geometry as its third component rather than ink alone.
- **Leading was treated as text-object state.** `TL` is *graphics* state (ISO 32000
  §9.3): it survives `ET` and is saved and restored by `q`/`Q`, so a `T*` in one text
  object can depend on a `TL` set in an earlier one. Resetting it per `BT` moves every
  line that relies on that.
- **Invoked streams inherit their initial text state.** A Form XObject, a tiling
  pattern and a Type3 glyph procedure start in the *caller's* state, not the default
  one. Assuming zero there emits a `0 Tw` that overrides the caller's spacing and
  resolves `T*` against the wrong leading. `canon.canonicalize(..., inherits=True)`
  now starts those as "inherited and unknown", refuses to resolve a `T*` it cannot
  resolve, and F2 leaves that one stream alone rather than refusing the document —
  with `residuals()` reporting it, so it fails loudly rather than passing as normalised.

**The fingerprint guard failed the moment F2 landed, and it was right to.** The
signature was ` 0 obj\n<< /Filter /FlateDecode /Length ` and ` >>\nstream\nx\xda` —
every stream re-encoded at deflate level 9. Two separate fixes came out of it, and the
distinction between them matters:

- The *shape* (`/Filter /FlateDecode` on every stream) is a constant of the canonical
  form, so it is **declared**: `empty_document_skeleton()` now generates an F2 skeleton
  as well as an F1 one, and the existing M3 argument covers it — a run common to every
  output that is a substring of a document with no content is structure by construction.
- The *level* is not declared, it is **measured**. `78 9C` = 6, `78 DA` = 9, `78 01` = 1;
  across the peer corpus four of five producers emit level 6 and one emits level 1, and
  none emits 9. Level 9 did not normalise our output, it labelled it. Pinned to 6 —
  the same "join the largest crowd that exists" reasoning the Phase 6 decoy-metadata
  item records for mandatory timestamps.

A third constant was removed rather than declared: the first cut emitted `0 Tw 0 Tc`
before every show run. Spacing is now written only when it actually changes, so a
document that never sets it comes out with no spacing operator at all — the FLAC
empty-Vorbis-comment lesson, which is that a constant you can simply omit should never
be normalised instead.

---

---

## 3. Milestones

| # | Deliverable | Status |
|---|---|---|
| **M0** | W0 spike — measured qpdf's byte layout against our own fingerprint guard; serializer decision recorded | ✅ |
| **M1** | PDF corpus + E-PDF-HISTORY: incremental-update history provably gone | ✅ baseline (§2.1) — attacks + controls land; our own row needs M2 |
| **M2** | PDF walker + serializer + tokenizer; **recursive** F1 + plugin + dispatch; `/Info`, XMP, `/ID[0]`, `/Thumb`, inline images and embedded-JPEG EXIF all cleared | ✅ (§2.2) |
| **M3** | E-PDF at `raw` and `F1` — which channel leaks, measured before F2 is designed | ✅ (§2.3) |
| **M4** | PDF F2 (canonical serialise + content-stream canonicalisation) + matrix — the honest A2-at-F2 answer, whatever it is | ✅ (§2.4) — A2@F2 fails; text-operator spelling closed, glyph geometry is the residual |
| **M5** | PDF F3 + E-PDF-RASTER incl. the DPI knob; redaction detector | 🔜 |
| **M6** | DOCX walker + F1 + plugin + matrix, ZIP timestamps consistent | 🔜 |
| **M7** | E-RSID: RSIDs cleared where MAT2 leaves them; benchmark row written | 🔜 |

M1 comes before the walker deliberately: E-PDF-HISTORY is binary, provable, the headline
result of the phase, and depends on none of the contested design points. M3 comes before
F2 for the reason recorded in W5 — designing F2 before measuring which features actually
leak is normalising by guesswork.

Phase 3 is **done** when both formats have validated matrices, every residual is in
`docs/limits.md`, the PDF A2-at-F2 frontier is characterised rather than asserted, and
the RSID benchmark row is measured on our own corpus.

---

## 4. Known limits expected to come out of this phase

Recorded here as predictions to be tested, not conclusions — they go to
`docs/limits.md` only once measured. New PDF rows start at **#13**; limit **#8** must be
edited when PDF lands, since it enumerates the supported formats.
- **Redaction is not metadata scrubbing.** Text hidden under a black box remains in
  the content stream. We will not fix that, and users must be told plainly. W7 detects
  and warns; it does not repair.
- **Glyph geometry** — positions, advances, line breaks, subset composition — is the
  expected F2 residual, and it survives F3 rasterisation into pixel space. Predicted to
  be of the PRNU/Sorell species: irreducible without re-typesetting, which changes
  rendering. E-PDF-LAYOUT and E-PDF-RASTER are what turn that prediction into a number,
  and the DPI knob is what might turn it into a Pareto trade instead of an impossibility.
- **F3 destroys selectable text.** Rasterisation preserves the *visible* text that hard
  constraint #1 names, but a scrubbed file can no longer be searched, copied from, or
  read by a screen reader. That is a real cost to a real user and it goes in
  `docs/limits.md` **before** the tier is built, not after.
- **Scanned PDFs are images**, so PRNU applies exactly as it does in Phase 1 — a
  structural impossibility inherited, not a new one.
- **Digital signatures** are invalidated by any rewrite. That is unavoidable and worth
  stating up front: a signed PDF cannot be scrubbed and stay signed.

## 5. Determinism, which the in-process floor test cannot fully see

The harness demands byte-identical output across five repeats *and* across the CI Python
3.11–3.14 matrix. W0 measured the current defaults as deterministic, including across
five separate interpreters with varied `PYTHONHASHSEED` — but two hazards remain:

- **`InProcessScrubber` runs all five repeats in one process**, sharing one hash seed. Any
  code path that iterates a `set` to decide output *order* would look perfectly
  deterministic to `floor.py` and differ on the next CLI invocation. PDF has far more
  string-keyed iteration — dictionary keys, name objects, resource dicts — than any
  format so far. Sort explicitly, and add a **subprocess-based** determinism test. This
  is a latent hole in the existing harness, not only a PDF concern.
- **Do not recompress with Python's `zlib`.** pikepdf wheels statically bundle qpdf and
  its zlib, so letting that layer compress pins output to the pinned wheel; compressing
  in Python makes the bytes a function of the interpreter build.

`pdftoppm` version drift will change F3 pixels between a laptop and CI. That is
deterministic per machine so `floor.py` passes, and `check_evidence.py` compares verdicts
only — survivable, but pin the DPI and output format explicitly and say so in the matrix
reason rather than letting a reader assume the bytes are portable.
