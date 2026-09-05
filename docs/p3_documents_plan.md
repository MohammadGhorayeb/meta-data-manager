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

### The shared layer: `formats/ooxml/`, not `formats/docx/`

DOCX, XLSX and PPTX are the same container with three different main parts. The ZIP
writer, the OPC relationship graph, `[Content_Types].xml`, `docProps/*` and
`docProps/thumbnail.*` are **identical across all three**, and Phase 6's long tail is
where the other two land. So the package machinery goes in `src/scrub/formats/ooxml/`
and `formats/docx/` is its first consumer — the CLAUDE.md rule that a shared module is
written once, because a missed copy is a leak. Nothing here is speculative
generalisation: `docProps/app.xml` leaks `TitlesOfParts` in a DOCX and **sheet names**
in an XLSX through the same element.

### W8 — OPC/ZIP byte-level spike (measure before choosing a writer)

The W0 lesson applied to a second container: **do not pick a writer before measuring
what the peers write.** For PDF that spike caught qpdf's constant header comment and
its inherited `/ID[0]`. The ZIP equivalents are harder to see, because Python's
`zipfile` *looks* neutral and is not — it stamps `create_system` into version-made-by
from the host OS, so the same input scrubbed on macOS and on the CI runner could
differ in a byte that names the operator's platform, while `floor.py` (five repeats,
one machine) calls it perfectly deterministic.

Dump from the raw bytes, per producer, never through a library's tidy view:

| Field | Why it is a producer channel |
|---|---|
| `version made by` / `create system` | Word writes FAT; `zipfile` writes the host OS. Names the platform. |
| `version needed`, GP bit 3 (data descriptor), bit 11 (UTF-8) | Streaming writers set bit 3; desktop apps do not. |
| Extra fields (`UT`, `Ux`, NTFS `0x000a`) | Info-ZIP and Windows write **nanosecond mtimes** here, next to a date field everyone assumes is the only clock. |
| MS-DOS date/time | Word writes the ZIP epoch `1980-01-01`; LibreOffice and `textutil` write **local wall clock**. The scheme itself is the fingerprint. |
| Per-entry compression method **and inferred level** | ZIP stores *raw* deflate — there is no `78 9C` zlib header to read the level off, unlike PDF W5. Level must be recovered by recompressing at 1–9 and matching bytes. Do that; do not guess. |
| Entry order, and whether directory entries (`word/`) are stored | An ordering fingerprint identical in kind to PDF object order. |
| `external attr`, archive comment, ZIP64 usage | Unix permission bits leak the umask. |
| `__MACOSX/` + `._*` AppleDouble entries | macOS's own resource forks, carrying Finder metadata — and the Step 2 crash target. |

Deliverable: the table filled in for Word, LibreOffice, macOS `textutil`, MAT2 output
and stdlib `zipfile`, plus a recorded decision — stdlib with every field pinned, or our
own writer — in the same form as the W0 serializer decision. **A pinned `zipfile` is
acceptable only if every field above can actually be pinned from Python**; the spike
answers that rather than assuming it either way.

### W9 — Locus census (`E-DOCX-LOCI`): enumerate before scrubbing

Phase 1's discipline was "prove every duplicate locus is cleared, not just the named
tags". DOCX has more loci than any format so far, and the ones that matter are the ones
no tool's README mentions. The census is a script that inventories the corpus and
prints a disposition per locus — **drop / keep / recurse / refuse** — so F1 is written
against a list rather than against memory.

Known loci, grouped by how much they are talked about:

- **Named everywhere:** `docProps/core.xml` (creator, lastModifiedBy, revision,
  created/modified, **lastPrinted**), `docProps/app.xml` (Application, AppVersion,
  Company, Manager, **Template**, TotalTime).
- **Named rarely:** `w:rsid*` in `settings.xml` and throughout `document.xml`;
  `docProps/custom.xml`; `word/people.xml`; comments and tracked changes with author
  and date; `docProps/thumbnail.jpeg` — a rendered first page **with its own EXIF**,
  so it recurses into the Phase 1 handler.
- **Named almost nowhere, and each one measured on the corpus before it is claimed:**
  - `w15:docId` — a **persistent per-document GUID** in `settings.xml`, stable across
    saves, which links two files to one original outright.
  - `w14:paraId` / `w14:textId` — **per-paragraph persistent IDs** that travel with a
    paragraph when it is pasted into another document. A local probe (§2.6) found
    MAT2 0.14.0 removing `w:rsid*` and **leaving these**, which would make them, not
    RSIDs, the current surviving session-ID channel.
  - `w:attachedTemplate` — a relationship target that is frequently a **UNC path**
    containing a username or a network share.
  - `docProps/custom.xml` `MSIP_Label_*` — Microsoft Purview sensitivity labels
    carrying a **tenant GUID**, i.e. the organisation, not just the person.
  - `app.xml` `HeadingPairs`/`TitlesOfParts` — leaks **heading text** of sections that
    may since have been deleted.
  - `w:bookmarkStart w:name="_GoBack"` — where the cursor was at the last save.
  - `w:proofErr`, `w:proofState` — which words the speller flagged, so the
    **dictionary and language configuration** of the machine.
  - `w:lastRenderedPageBreak` — layout state written by whichever renderer opened it.
  - `word/fontTable.xml` — effectively an **installed-font inventory**.
  - `word/embeddings/*.bin` (OLE: a whole other document), `word/vbaProject.bin`
    (macros, with their own author and paths), `customXml/itemProps*.xml`
    (SharePoint/records payloads), and `_rels` targets with absolute local paths.
  - `mc:AlternateContent` fallbacks — a second rendering of the same object, with its
    own metadata, which a walk over the primary branch never sees.

### W10 — OPC walker (read-only) + `claims()` + the refusal list

`PK\x03\x04` is the magic of every ZIP ever made, so unlike every format so far the
prefix decides nothing: `claims()` must open the central directory and require
`[Content_Types].xml` **and** `word/document.xml`, or an XLSX, PPTX, ODT, EPUB or JAR
will be handed to the DOCX handler. Read the central directory, not a sequential scan
of local headers — they can disagree, and which one a reader trusts is itself an
attack surface.

Hostile inputs the walker must survive rather than crash on, each a real file that
exists: AppleDouble entries, ZIP64, data descriptors with unknown sizes, duplicate
entry names (two parts, one name — readers disagree on which wins), `../` path
traversal in names, entries not listed in `[Content_Types].xml`, and relationship
targets pointing outside the package.

Fail closed, with a stated reason, on: **encrypted** OOXML (a CFB wrapper, not a ZIP —
also a dispatch trap, since the magic is `D0 CF 11 E0`), **digitally signed** packages,
**macro-enabled** packages, and **OLE embeddings** (a whole document needing its own
pass). The precedent is PDF's refusal list, and the rule is the same: a refusal is
honest, a half-scrub is not.

### W11 — Our ZIP writer + the cross-process determinism test

Whatever W8 decides, the output is written with: one compression level (**measured
from the peer corpus, not picked** — the PDF deflate-level-6 lesson, where level 9
did not normalise our output, it labelled it), the `1980-01-01` MS-DOS constant
(**which is what Word itself writes** — joining the largest existing crowd, not
inventing a value), no extra fields, no directory entries, no archive comment, no data
descriptors, and canonical entry order. Local header and central directory must agree
field for field; a mismatch is the classic subtly-corrupt rewrite.

This work item also pays off the debt recorded in §5: `InProcessScrubber` runs its five
repeats in **one interpreter with one hash seed**, so any code path that iterates a
`set` or an unordered dict to decide output order looks deterministic to `floor.py` and
differs on the next CLI invocation. DOCX has more string-keyed iteration — part names,
namespace maps, attribute sets, relationship IDs — than every previous format combined.
So: a **subprocess determinism test** across several `PYTHONHASHSEED` values, applied
to **every** format, not just this one. It is a latent hole in the harness that DOCX
merely makes likely to matter.

### W12 — DOCX F1: surgical deletion, recursive, fail-closed

F1 is bit-preserving in the sense PDF F1 established: **we only delete.** Parts are
dropped whole; attributes and elements are excised from the parts that survive; every
byte we do not delete stays exactly as it was, and the extracted document text is
byte-identical. The ZIP container is rewritten by W11 because container layout is not
content.

Consistency is where this format bites. Dropping a part means dropping its
`[Content_Types].xml` override **and** its relationship entry, or Word shows the
"unreadable content" dialog and the scrub has destroyed the document while reporting
success. So the walker's rels graph is not a nicety, and the acceptance test is not
"did we write a file" but "does it still open".

Recursion is free here and must be used: `word/media/*` and `docProps/thumbnail.jpeg`
go through the Phase 1 JPEG/PNG handlers. **EMF/WMF has no handler** — those can carry
text and even a printer name — so it is a refusal or a documented gap, decided by
whether the corpus contains any.

One decision this work item must settle rather than drift on: **tracked changes and
comments are not metadata, they are content that is not currently displayed.**
Accepting all revisions changes what a reader sees with markup on, so it violates
constraint #1 at F1. The recommendation is to **refuse** documents carrying them at F1
(they are among the most damaging leaks in the format, and half-handling them is
worse than saying no) and to accept-and-flatten at **F2**, where the tier already
licenses a rewrite. Measure the corpus first; decide in the plan; do not let the code
decide by accident.

### W13 — `DocxPlugin`: two channels, named separately

M4A has a muxer and an encoder; PDF has a serializer and a layout engine; DOCX has a
**packager** and a **document model**, and the A2 cells name which one leaked instead
of averaging them.

- `pkg:` — entry order and entry set, per-entry compression method and level,
  timestamp scheme, version-made-by, external attributes, directory entries, package
  size.
- `model:` — XML declaration style (`"` versus `'`, `standalone`), self-closing tag
  spacing, the namespace declaration set and prefix choices, attribute order, the
  style inventory in `styles.xml`, the `settings.xml` key set, `w:sectPr` defaults,
  `fontTable` contents, whether `theme1.xml` exists at all, and paragraph-representation
  idioms.

`canonical_content` is two-level, mirroring how PDF checks content preservation in
pixel space rather than against our own invariant: the cheap gate is the concatenated
`w:t` text with `w:tab`/`w:br` normalised, required to match exactly; the real gate is
`soffice --convert-to pdf` → `pdftoppm -r 150 -png` → pixel hash, the same rig the PDF
tier already uses. Whether that runs in CI or is reported as *not measured there* (the
limit-#12 precedent for Apple's AAC) is a cost decision: `libreoffice-writer` is a
large runner install and this repo already builds `shineenc` from source rather than
let a headline claim go unverified.

### W14 — DOCX F2: XML canonicalisation, and the honest A2-at-F2 answer

The direct analogue of PDF's content-stream canonicaliser, one layer up: re-serialise
every surviving part through **one writer** — fixed namespace prefixes with unused
declarations dropped, sorted attributes, no pretty-printing, one encoding declaration,
one self-closing style — so that the producer's *XML spelling* stops being readable.
Rendering must be identical, verified in pixel space by the W13 rig.

The prediction, recorded now so measurement can falsify it: spelling closes and
**substance does not**. What should survive is the document *model* — which styles a
producer defines and names, which compat settings it writes, how it chooses to
represent a paragraph — the DOCX species of PDF's glyph geometry. If that holds, A2@F2
fails and the residual is named rather than rounded away.

### W15 — DOCX F3: decided by W14's residual, not assumed

PDF's F3 was rasterise. The DOCX analogue is a **round-trip through one engine**
(LibreOffice), which regenerates the document model and would collapse producers to
LibreOffice's own — at a real cost in formatting fidelity that must be measured, not
waved at.

This is the one place in the project where F3 might genuinely *win* where PDF's did
not, and the reason is structural: PDF F3 moved the residual into pixels, where the
typesetter's geometry is still painted; a DOCX round-trip **regenerates the symbolic
model itself**. Or it fails, because LibreOffice preserves enough of what it reads.
Either answer is publishable; neither is assumable, so W15 is built only after W14 has
measured what is actually left.

---

## 2. Experiments

| # | Question | Status |
|---|---|---|
| **E-PDF-HISTORY** | Does an earlier revision survive our scrub? Build a PDF with N incremental updates, scrub, and carve for text that was "deleted" in revision 1. | ✅ baseline measured (§2.1); our own row lands with M2 |
| **E-PDF** | Do object order, xref style and font-subset tags identify the producing application, and does the canonical rewrite (F2) erase them? Reported per channel — serializer versus layout. | ✅ at `raw`/`F1` (§2.3); the F2 column needs M4 |
| **E-PDF-LAYOUT** | After F2 normalises the serializer, is the producer still recoverable from the **content-stream operators**? The `E-ENGINE` analogue, in operator space. | 🔜 |
| **E-PDF-RASTER** | After F3 destroys the operators, is the producer still recoverable from the **rendered pixels**? And does lowering DPI kill it? | 🔜 |
| **E-DOCX-LOCI** | Where does metadata actually live in a DOCX, per producer? A census with a disposition (drop/keep/recurse/refuse) for every locus found, so F1 is written against a list rather than from memory. | ✅ (§2.8) |
| **E-SESSION-ID** | Do editing-session identifiers survive ExifTool, MAT2 and us? Covers `w:rsid*`, **`w14:paraId`/`w14:textId`** and **`w15:docId`** — not only the RSIDs the literature names. Supersedes the narrower **E-RSID**, because a §2.6 probe found MAT2 0.14.0 clearing RSIDs and leaving the paragraph IDs. | ✅ (§2.13) |
| **E-DOCX-THUMB** | Is `docProps/thumbnail.jpeg` cleared, and is its own EXIF cleared with it? | ✅ (§2.13) — we drop the part; MAT2 strips its EXIF but keeps the picture |
| **E-DOCX** | Do package layout and XML idiom identify the producing application, and does F2's canonicalisation erase them? Reported per channel — packager versus document model. | ✅ at `raw`/`F1`/`F2` (§2.12, §2.14) |
| **E-DOCX-ROUNDTRIP** | After an F3 round-trip through one engine, is the producer still recoverable — and what does the round-trip cost the formatting? | ✅ (§2.15) — recoverable from one key (a primary-production trace); the formatting cost is **not measurable** here, and is reported as such |

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

### 2.5 M5 as built — F3, the DPI knob that isn't, and the redaction advisory

`src/scrub/formats/pdf/f3.py`, `src/scrub/formats/pdf/redaction.py`,
`tests/scrub/e_pdf_raster.py`.

**F3.** Every page rendered by one poppler build at one pinned resolution (150 DPI),
re-encoded through one JPEG setting, rebuilt around the images by our own serializer.
The technique is MAT2's and Dangerzone's and the plan says so; the contribution of
this phase is F1 and F2.

**Structurally it works, and that is the least interesting thing about it.** E-PDF at
F3 shows the serializer channel closed and the layout channel down to a single key —
`number_style` — which leaks for one reason only: LibreOffice sets A4 where every
other producer sets Letter. Page size is *content*, so that residual is correct rather
than a normalisation we failed to make.

**Publishing that as "A2@F3 passes" would have been the overclaim this project exists
to avoid.** The page is an image now; the typesetter's geometry is painted into it.
So the A2@F3 cell is decided by **E-PDF-RASTER**, which attacks the rendered pixels.

| condition | accuracy | chance | p | legibility |
|---|---|---|---|---|
| unscrubbed (control) | 1.00 | 0.20 | <0.0001 | — |
| F3 @ 300 DPI | 1.00 | 0.20 | <0.0001 | print quality |
| F3 @ 150 DPI | 1.00 | 0.20 | <0.0001 | legible in print |
| F3 @ 72 DPI | 1.00 | 0.20 | <0.0001 | legible on screen |
| F3 @ 36 DPI | 1.00 | 0.20 | <0.0001 | barely legible |
| F3 @ 18 DPI | 1.00 | 0.20 | <0.0001 | **body text unreadable** |

Leave-one-**document**-out nearest neighbour over ink geometry, five producers × six
distinct documents, attacker rendering at a fixed 150 DPI in every condition. The
held-out page's own document is excluded from the reference set entirely, so the
classifier has to generalise across content rather than recognise a page it has seen.

**The DPI knob does not exist, and finding that out corrected our own reasoning.** W6
predicted a possible Pareto trade on the assumption that *sub-pixel* glyph placement
carried the signal, in which case coarser rendering would blur it. The ablation says
otherwise:

| feature subset | accuracy |
|---|---|
| full vector | 1.00 |
| column profile alone (margins, text extent) | 1.00 |
| row profile alone (line spacing, baselines) | 0.83 |
| **ink density alone** | **0.17 — chance** |
| full vector, page size held constant | 1.00 |

The signal is **coarse-scale layout geometry**, not sub-pixel detail, and downsampling
leaves it completely intact. The ablation is not decoration: a classifier at 100%
proves the producer is identifiable, not *what from*, and "glyph geometry survives"
would otherwise have been an interpretation laid over a number that could equally have
come from page size — LibreOffice's A4 alone would separate it. Ink density at chance
rules that out, and restricting the peer set to the Letter-size producers rules it out
again.

So the residual is confirmed as the PRNU/Sorell species the plan predicted:
**irreducible without re-typesetting**, and F3 relocates it rather than removing it.
That is a stronger statement than W6 expected to be able to make, and it is stated as
a measurement rather than an assertion.

**A new locus space.** The matrix schema allowed `byte`, `field` and `structural`, and
recording a pixel-domain residual under `structural` would have filed it under the
very space the tier closes. `pixel` and `signal` were added — the second so the audio
experiments have somewhere honest to point when their residuals are next written up.

**W7, the redaction advisory.** Three checks — invisible text (rendering mode 3 or 7),
text outside the crop box, and text drawn under an opaque fill that covers it — each
cheap and unambiguous. It **warns and never fixes**, it is advisory so it never fails
a scrub, and it deliberately under-reports: a rotated fill, a fill in another stream,
or any page that has touched a named graphics state is not reported, because "text
under an opaque fill" in full generality needs blend modes, transparency groups and
z-order, which is a small renderer and would eat the phase. A warning that fires on
ordinary drawing gets ignored and then protects nobody, so three negative-control
tests assert it stays quiet on a stroked box, a fill elsewhere on the page, and a fill
drawn *before* the text (a highlight, not a cover).

The uncomfortable fact behind it is asserted rather than left implicit: a test checks
that the words under a black box **still come out of `pdftotext` after a successful
scrub**. Every tier preserves content, so they must — and if that test ever fails, a
tier has started destroying content.

---

### 2.6 DOCX opening probe — what is on this machine, and one inherited claim under strain

A scouting probe, run before the DOCX work items were written. It is **n=1 file per
tool on one machine**, so nothing here is a result: it is what E-SESSION-ID and
E-DOCX-LOCI are pointed at. Recorded because two of its observations change the shape
of the plan.

**Producers available offline, which decides the corpus.** The A2 peer set must be
buildable from tools a runner can install, because the e3 precedent is that corpus
binaries are never committed — only generators are:

| Producer | Availability | Notes |
|---|---|---|
| LibreOffice (`soffice --convert-to docx`) | local **and** installable in CI | Writes **no** `w:rsid*` from a plain-text source; 10 entries, no directory entries; **local wall-clock** ZIP timestamps |
| macOS `textutil -convert docx` | local only (Cocoa) | 8 entries, an *empty* `core.xml`, and a nonstandard **`docProps/meta.xml`** no other producer writes |
| stdlib `zipfile` synthetic | everywhere | The producer we control, as the pikepdf synthetics were for PDF |
| MAT2 output | local and CI | Both a benchmark and, usefully, a fourth *producer* with its own XML idiom |
| **Microsoft Word** | **local only, and not committable** | The producer that actually writes RSIDs. Real files exist on this machine but carry personal content, so they stay git-ignored and Word is reported as a **locally-measured, not-automated** peer — the limit-#12 precedent for Apple's AAC encoder |

**Word writes `1980-01-01` into its own ZIP timestamps.** The W8/W11 decision to take
the reproducible-builds constant was reasoned from first principles (a mandatory field
should join the largest crowd rather than invent a value); the probe shows the largest
crowd is *Word itself*. LibreOffice and `textutil` both write local wall clock, which
makes the timestamp **scheme** a producer channel before any individual date is read.

**The inherited RSID claim is under strain, and this is the important one.** CLAUDE.md
carries "OOXML RSIDs survive every surveyed scrubber including MAT2 (Müller)" as a
known conclusion. On a real Word document, **MAT2 0.14.0 removed every `w:rsid*`** from
both `settings.xml` and `document.xml` — and left `w14:paraId` and `w14:textId` on
every paragraph, which are persistent per-paragraph identifiers of the same species,
travelling with a paragraph when it is pasted into another document. Two of the values
it left (`005EDE06`, and the `673488B4`/`265B6059` pair) sit in the same 8-hex-digit
space as the RSID pool it had just deleted.

If E-SESSION-ID confirms it, the phase's headline is not "we clear what MAT2 leaves"
but something better: **the literature's named leak has been fixed upstream, and the
surviving channel is one nobody names.** That is a correction to a claim this project
inherited rather than measured, and per the project's own rule it does not go anywhere
near the proposal until it is measured on a real corpus with controls. It is written
here as a probe so it cannot quietly become an assumption in the meantime.

---

### 2.7 M6 as measured — E-DOCX-ZIP, and why we write the container ourselves

`tests/scrub/docx_corpus.py`, `tests/scrub/e_docx_zip.py`,
`tests/scrub/test_e_docx_zip.py`. Run the table with
`python -m tests.scrub.e_docx_zip`.

The census reads the ZIP bytes itself and never uses `zipfile` to inspect, because
`zipfile` normalises away most of what is being measured: it returns a tidy `ZipInfo`
with no view of *which* of the two copies of each header it read or whether they
agreed.

| field | cocoa_textutil | libreoffice | mat2_out | msword | synth_zipfile |
|---|---|---|---|---|---|
| **entries** | 8 | 10 | 10 | 11 | 3 |
| **create system** | FAT(0) | FAT(0) | **Unix(3)** | FAT(0) | **Unix(3)** |
| **version made by** | 20 | 20 | 20 | **45** | 20 |
| **GP flags** | 0x0000 | **0x0808** | 0x0000 | **0x0006** | 0x0000 |
| **timestamp scheme** | wall-clock | wall-clock | epoch-1980 | **epoch-1980** | epoch-1980 |
| **extra fields** | none | none | none | **MS-OPC-growth-hint** | none |
| **external attr** | 0x00000000 | 0x00000000 | **0x01800000** | 0x00000000 | **0x01800000** |
| **zlib level** | **6** | **6** | **6** | **not-zlib** | 5-9 |
| **deflate flag bits** | normal | normal | normal | **super-fast** | normal |
| **local/central disagree** | 0 | 0 | 0 | 0 | 0 |

Entry order differs per producer and is a clean structural channel on its own: Word
writes `[Content_Types].xml` first and `docProps/*` last; LibreOffice writes
`docProps/*` **first** and `[Content_Types].xml` **last**; `textutil` writes a
`docProps/meta.xml` that no other producer emits at all.

**Compression level had to be recovered, and the first attempt was wrong.** A ZIP
stores raw deflate with no zlib wrapper, so there is no `78 9C` byte to read the level
off the way PDF W5 could. Recompressing the entry's own plaintext at every level and
taking the first byte-exact match reported nonsense — `[Content_Types].xml=6`,
`document.xml=5`, `meta.xml=1` *within a single producer* — because on parts this
small, levels 4 through 9 frequently produce identical output. The honest unit is an
**equivalence class** per entry, and a producer's setting is pinned only by the
*intersection* across all of its entries. With that correction all three zlib-based
producers collapse to exactly **level 6** and the per-entry noise disappears.

**Level 6 is the crowd again, and it is the second independent time this project has
measured it.** PDF W5 arrived there from the zlib header byte after the fingerprint
guard failed level 9; the ZIP census arrives there from a completely different
technique. W11 takes level 6 because it is where everyone is, not because it is the
default.

**Word's deflate is not zlib at all.** No level, at either plausible `memLevel`,
reproduces any of its eleven entries — and it declares `super-fast` in the general-
purpose flag bits, which nothing else sets. So "match Word's bytes" is not an option
available to us in any language; the reachable crowd is the zlib crowd. Word is also
the only producer writing `version made by` 45 rather than 20, and the only one
carrying the Microsoft `0xA220` OPC growth-hint extra field.

**Word writes the 1980 epoch into its own ZIP timestamps** — confirming from the other
side the constant W11 had chosen on reproducible-builds grounds. LibreOffice and
`textutil` both write **local wall clock**, so before any individual date is read, the
timestamp *scheme* already separates producers into two groups.

#### The decision: we write the ZIP container ourselves

Same shape as the W0 serializer decision, and settled by measurement rather than
taste. Every field above pins through stdlib `zipfile` — `create_system`,
`create_version`, the DOS timestamp, the compression level, the flag bits — **except
one**, and it is the one that matters:

```python
# CPython, ZipFile._open_to_write
if not zinfo.external_attr:
    zinfo.external_attr = 0o600 << 16   # permissions: ?rw-------
```

Word, LibreOffice and `textutil` all write `external_attr = 0`. That single value is
the one `zipfile` refuses to keep, because zero is falsy and trips the default — so a
package written through the stdlib carries **the operator's umask, in the central
directory, in every entry**, and the two Python-written producers in the table are
exactly the two showing `0x01800000`. There is no argument that makes it write zero.

The alternatives were a post-write byte patch (four bytes per entry, which means
already editing the container after the library produced it) or owning the writer. We
own it, for the reason the census exists: **the fields that identify a producer are
the ones no library exposes**, and a determinism-critical path across Python
3.11–3.14 should not depend on stdlib behaviour that has changed as recently as
`_for_archive`. The walker (W10) has to read raw ZIP anyway, so the format knowledge
is not new cost.

The obvious risk of hand-rolling is the classic subtly-corrupt output where the local
header and central directory drift apart, so that is tested from both directions: the
census asserts zero disagreements across every producer, and a test deliberately
patches one copy of a timestamp to prove the check would actually catch it.

Two stdlib traps are recorded as tests rather than as comments, so a CPython change
reports itself instead of silently moving our output:
- **`external_attr` cannot be set to 0** (the decision above).
- **A bare `ZipInfo` carries `compress_type=ZIP_STORED` and silently overrides the
  `ZipFile(..., ZIP_DEFLATED)` argument.** The corpus's first run shipped three
  uncompressed parts while asking for deflate, and the only reason it was noticed is
  that the census counts stored entries.

**Not settled by this corpus, and named rather than assumed:** no sample contains
`__MACOSX/` or `._` AppleDouble entries, ZIP64 structures, data descriptors on a
non-streaming writer, or duplicate entry names. Those are W10's hostile set and must
be constructed, not waited for. LibreOffice does set the data-descriptor flag
(0x0808), so that path at least is exercised by a real producer.

---

### 2.8 M7 as measured — E-DOCX-LOCI, the list F1 gets written against

`tests/scrub/e_docx_loci.py`, `tests/scrub/test_e_docx_loci.py`, and the torture
package in `docx_corpus.torture()`. Run the table with
`python -m tests.scrub.e_docx_loci`.

Phase 1's rule was "prove every duplicate locus is cleared, not just the named tags".
The census is how that rule becomes checkable for a format with more hiding places
than any so far. Every locus carries a **disposition** — drop / keep / recurse /
refuse — and a locus found in a package with no rule comes back `UNCLASSIFIED`, which
a test treats as a failure. **A leak cannot hide in a place nobody wrote a rule for,
because the census names the place.**

Measured across the same five producers (counts are occurrences; `·` means absent):

| locus | cocoa | LO | mat2 | Word | disposition |
|---|:-:|:-:|:-:|:-:|:--|
| `docProps/core.xml` (creator, lastModifiedBy, revision, created/modified, lastPrinted) | 1 | 1 | 1 | 1 | **drop** |
| `docProps/app.xml` (Application, Company, Template, TotalTime, TitlesOfParts) | 1 | 1 | 1 | 1 | **drop** |
| `docProps/meta.xml` — Cocoa-only part | 1 | · | · | · | **drop** |
| `w:rsid*` / `w:rsids` | · | · | **·** | **7 / 1** | **drop** |
| `w14:paraId` / `w14:textId` | · | · | **4** | 4 | **drop** |
| `w15:docId` | · | · | **1** | 1 | **drop** |
| `w:proofErr` / `w:proofState` | · | · | **2 / 1** | 2 / 1 | **drop** |
| `word/fontTable.xml` | · | 1 | 1 | 1 | keep (residual) |
| `word/settings.xml`, `[Content_Types].xml`, `*.rels`, content parts | ✓ | ✓ | ✓ | ✓ | keep |

**The MAT2 column is the finding.** Read across the session-ID rows: MAT2 clears
`w:rsid*` and `w:rsids` outright, and leaves `w14:paraId`, `w14:textId`, `w15:docId`,
`w:proofErr` and `w:proofState` exactly as Word wrote them. That is the §2.6 probe
reproduced through an entirely different mechanism — a rule-driven census rather than
a grep — and it sharpens the phase's target: the leak the literature names is gone,
and what remains is a family nobody names. `w15:docId` is the worst of them, a
persistent per-document GUID; `w14:paraId` is the most interesting, because it travels
with a paragraph pasted into another document and therefore links *files to each
other* rather than files to a producer. That is an attack the A1/A2/A3 ladder does not
model at all. It still is not E-SESSION-ID — one document, one MAT2 version, no
controls — but it is now a test that fails if it stops being true.

**LibreOffice writes its own CPU architecture.** `Application` reads
`LibreOffice/26.2.4.2$MacOSX_AARCH64`, so a single property names the application, its
exact version, the operating system and the instruction set. It is a `drop` like the
rest, and worth quoting as the answer to "surely `app.xml` is harmless boilerplate".

#### The torture package, and why the rule table is tested rather than trusted

No real producer in the corpus writes a tracked change, a comment, a bookmark, a
sensitivity label, an embedded image or an OLE object, so half the rule table would
have been unexercised guesswork. `docx_corpus.torture()` is one synthetic package
containing **one instance of every locus the table names** — the `torture_pdf()`
precedent — and a test asserts that **no rule fails to fire against it**. It is
synthetic and says so: it proves the census can *see* each locus, never that a real
producer writes it that way. It also carries the two structural hazards that are not
metadata at all: an `__MACOSX/._` AppleDouble entry, and a part with no
`[Content_Types].xml` override.

**The census caught a bug in its own rules, which is the point of running it before
writing F1.** `w:name` was in the attribute table as "bookmark names, including
`_GoBack`". The first run reported it **386 times in one document**, because
WordprocessingML reuses `w:name` on `w:font`, `w:style` and `w:compatSetting`. A
context-free rule would have had F1 deleting **font and style names** — content, not
metadata, and a content-preservation failure rather than a leak. Ambiguous attributes
now match only with their parent element (`w:bookmarkStart/@w:name`), and a regression
test asserts the font and style names in the torture package are not counted as
bookmarks.

**Corpus gaps, named rather than assumed away:** no real producer here writes tracked
changes, comments, `people.xml`, embedded media, `customXml/` or a Purview label —
those loci are exercised only synthetically. Word samples with several editing
sessions, a comment and a photo would close the gap, and
`tests/corpus/docx/README.md` says exactly that.

---

### 2.9 M8 as built — the OPC walker, and identification when the magic decides nothing

`src/scrub/formats/ooxml/zipread.py`, `src/scrub/formats/ooxml/opc.py`,
`src/scrub/formats/docx/handler.py`, `tests/scrub/test_ooxml_walker.py`.

The package machinery is in `formats/ooxml/` rather than `formats/docx/` because
DOCX, XLSX and PPTX are the same container with three different main parts — one
shared module, per the rule that a missed copy is a leak.

**Read from the central directory, never by scanning local headers forward.** A ZIP
records each entry's header twice, the copies can disagree, and which one a reader
trusts is itself an attack surface. The walker reads the copy the format designates
as authoritative and *reports* disagreements per field rather than silently picking
one. `zipfile` is not used at any point, for the reason W8 measured: it hands back a
tidy `ZipInfo` with no view of which copy it read, and it normalises away the very
fields that identify a producer.

**Identification is the genuinely new problem.** Every format so far has been decided
by its magic prefix. `PK\x03\x04` decides nothing — XLSX, PPTX, ODT, EPUB, JAR and a
plain archive all begin with it — so `matches()` is a cheap gate and `claims()` opens
the central directory to look for `[Content_Types].xml` plus `word/document.xml`.
This is the same two-stage design the dispatcher already uses for the ID3-prefixed
audio formats, pushed harder: there, the second stage disambiguates two formats;
here it is the *only* stage that identifies anything. A test builds an XLSX-shaped
package and asserts it is not claimed as a DOCX.

`claims()` must also never raise. Dispatch asks every handler in turn, so a handler
that crashes on a malformed file takes down dispatch **for every other format** — a
test feeds it empty input, a bare prefix, and truncated garbage.

#### The refusal list, and what it cost to get right

Refused with a stated reason, on the PDF precedent that a refusal is honest and a
half-scrub is not: ZIP64, duplicate entry names, traversal or absolute entry names,
encrypted entries, trailing bytes after the end-of-central-directory record,
prepended bytes before the first local header, multi-disk archives, a CRC that does
not match the part's own bytes, malformed part XML, an OPC package with no
recognised main part, and — at the package layer — digital signatures, macros and
embedded OLE objects.

**ZIP64 detection was wrong on the first attempt, and the test that caught it was
wrong too.** The check looked only at the two ZIP64 end-of-central-directory
records, and the test tried to force ZIP64 by setting `ZipInfo.file_size` — which
`writestr` overwrites, so it produced an ordinary archive and the test passed
vacuously. ZIP64 more often hides in an entry's **extra field**, where a `0x0001`
record carries 64-bit sizes that *supersede* the 32-bit ones in the header. Ignoring
it does not fail loudly; it silently reads the entry at the wrong length, which for
a scrubber means missing part of a file it claimed to clean. Both were fixed: the
walker refuses on the extra field as well, and the test now forces ZIP64 through
`ZipFile.open(..., force_zip64=True)` and asserts on the message.

**`orphans()` is the check that makes deletion safe.** Dropping a part means dropping
its relationship *and* its content-type override; leave either behind and Word shows
"unreadable content" — the scrub has destroyed the document while reporting success.
So the graph is not a convenience: F1 is not allowed to delete anything until this
check is clean. Measured on a real Word package, dropping `docProps/*` correctly
reports both dangling package-level relationships, and relationship targets resolve
against the *directory of the source part* (`word/document.xml` + `settings.xml` is
`word/settings.xml`, not `settings.xml`) — getting that wrong would make every
internal target look orphaned and the check useless.

AppleDouble entries are **read, not refused**. They are metadata to drop, not a
structure we cannot parse, and they are the Step 2 crash target — so the walker
surviving them is the requirement, and a test asserts it.

**The handler is deliberately not registered in `dispatch.py` yet.** Registration
lands with F1 (M10) so the tool never advertises a format it cannot scrub: today a
DOCX raises `UnsupportedFormatError`, which is true, rather than being admitted by a
handler that declines every fidelity, which reads like a bug. A test asserts the
absence, so registration is a deliberate act rather than something forgotten.

---

### 2.10 M9 as built — the ZIP writer, and the determinism hole finally closed

`src/scrub/formats/ooxml/zipwrite.py`, `tests/scrub/test_ooxml_writer.py`,
`tests/scrub/test_determinism_cross_process.py`.

#### The writer

Every constant is the one §2.7 measured, and each is pinned because a mandatory field
should join the largest crowd that exists — a value nobody else writes is a signature.
`create_system` 0 (FAT) rather than the Unix 3 that names the machine we ran on;
`version made by` and `needed` 20; flag word 0; the `1980-01-01` MS-DOS constant that
**Word itself writes**; deflate at zlib level 6; `external_attr` 0; no extra fields,
no archive comment, no directory entries, no data descriptors.

Entry order is `[Content_Types].xml` first — ECMA-376 Part 2 asks for it so a
streaming consumer has the types before the parts, and it is Word's convention — then
every other part **sorted**. Sorted specifically so the order can never be inherited
from a dict or a set, which is the exact non-determinism the next section is about.
A test asserts that handing the writer the same parts in a different order produces
an identical file, because the scrubber's own bookkeeping must not become a
fingerprint.

The two copies of each entry header are built from one tuple of values in one place,
since the failure hand-rolling risks is precisely a local header and a central
directory that drift apart. It is checked from three directions rather than by
inspection: our own reader reports zero field disagreements, `zipfile` reads every
part back unchanged, and **Info-ZIP `unzip -t`** accepts the archive — an independent
implementation, because `zipfile` agreeing with us proves only that two Python views
of the format agree.

Refused rather than half-supported: a non-ASCII part name (it would need GP bit 11 and
make our flag word non-constant; OPC part names are ASCII by spec) and directory
entries.

**A test that was wrong before the code was.** The level assertion started as
"6 is in the recovered class and 9 is not", measured on one settings part repeated 40
times — and failed, against a writer doing exactly the right thing, because data that
compressible emits **identical bytes at levels 6 through 9**. The level class is a
property of the data, the same lesson §2.7 learned from the other direction. On varied
paragraph text with high-entropy ids the levels separate cleanly and the assertion is
now the strongest available: `levels == {6}` exactly. Level 9 is excluded by name
because it is the mistake this project already made once — PDF F2 shipped at 9 and the
fingerprint guard failed immediately, since no peer producer emits it.

**Portability, stated rather than discovered later:** the compressed bytes are whatever
this machine's zlib emits at level 6. Deterministic per build and across processes,
which is what the tests check; two different zlib builds may differ, so verdicts are
compared across machines, never bytes — the caveat `pdftoppm` already carries for PDF
F3.

#### The determinism hole, closed for every format

§5 recorded it: `floor.py` scrubs one input five times and calls the output
deterministic if the five agree, but all five run in **one interpreter sharing one
hash seed**. A code path iterating a `set` to decide output *order* is invisible to it
and differs on the next CLI invocation. PDF got a subprocess check when it landed; the
plan said the debt was owed to every format, and DOCX — with more string-keyed
iteration than any format so far — is where it gets paid.

`test_determinism_cross_process.py` now runs **every (format, fidelity) the tool
offers** — 15 combinations across JPEG, PNG, MP3, FLAC, M4A and PDF — through the CLI
in separate interpreters under varied `PYTHONHASHSEED`, and asserts byte-identical
output. F3 tiers are included rather than excused: "an external encoder does the work"
is a reason to check the surrounding code, not to skip it. The whole thing costs about
five seconds.

**Result: every existing format already passed.** The hole was latent, not active —
worth saying plainly rather than dressing up a green run as a bug found. What the
milestone buys is that it is now a guard rather than a hope, in place *before* the
format most likely to trip it. The writer is checked the same way, directly rather
than through the CLI, because DOCX is not registered in dispatch yet and a writer must
be proven deterministic before a tier is built on it.

---

### 2.11 M10 as built — DOCX F1, and three leaks a weaker test would have passed

`src/scrub/formats/ooxml/xmlsurgery.py`, `src/scrub/formats/docx/f1.py`,
`src/scrub/formats/docx/handler.py`, `tests/scrub/test_docx.py`.

F1 means what it means for PDF: **we only delete.** Parts go whole, attributes and
elements are excised from the parts that survive, every other byte stays where it
was, and the document's text comes out byte-identical. The container is rewritten by
M9's writer, because container layout is not content.

That rules out the obvious implementation. Parsing a part with `ElementTree` and
re-serialising it reorders and prunes namespace declarations, changes attribute
order, turns `<a/>` into `<a />` and flips the XML declaration's quotes — none of
which changes the document and all of which changes the bytes. That is
canonicalisation, it belongs to F2, and doing it here would mean the tier could no
longer claim the untouched parts are untouched. So `xmlsurgery.py` edits in place.
Regex handles attributes safely (a `"` inside a value must be escaped, so `[^"]*`
cannot run past the closing quote) but **element removal needs a scanner**, because
an element may contain children of the same name and a non-greedy match to the next
closing tag cuts in the wrong place; `_element_span` counts depth, and `_tag_end`
ignores any `>` sitting inside a quoted attribute value.

**Consistency is the hard part, not deletion.** Dropping a part means dropping its
content-type override *and* its relationship, so the last thing `scrub()` does before
returning is re-read its own output and refuse if any relationship dangles. A `.rels`
part left declaring nothing is itself removed — an empty one is a tell that something
was taken out of it.

Measured, all five producers: text byte-identical, every census locus gone, every
output still opens through LibreOffice, and `exiftool` reports no identity field.
Word 13,431 → 10,013 bytes, 11 parts → 9.

#### The three leaks, and the test change that found them

The first version of the session-ID test asserted `b"w:rsid" not in out` against the
**archive bytes**. It passed. It was nearly worthless: the parts are deflated, so a
string is absent from the compressed bytes almost by construction and the assertion
proves nothing about whether anything was removed. Rewriting it to decompress each
part and assert per part turned one green test into three failures:

1. **`w14:docId`.** Word writes the persistent per-document GUID in **both**
   namespaces in the same `settings.xml`. The rule named `w15:docId`, so F1 removed
   one and left the other.
2. **`w:rsid` as a standalone element.** It is not only an attribute and not only
   inside `w:rsids`: Word writes `<w:rsid w:val="…"/>` inside **every `w:style`** in
   `styles.xml`, from the same pool as the document's own. The attribute rule and the
   container rule both missed it.
3. **The generalisation.** Microsoft versions its namespaces — `w14` is Word 2010,
   `w15` is 2012, `w16` is 2021 — and writes the same element in several at once, so
   patching `w14:docId` and moving on would leave the next sibling to be discovered
   the same way. `test_no_identifier_family_has_an_unruled_sibling` now scans every
   identifier-shaped name in the corpus and requires each to be either ruled or
   listed as content with a reason. A `w16:` sibling fails a test instead of shipping.

The torture package gained both loci, so `test_every_rule_in_the_table_actually_fires`
keeps covering them.

A fourth bug was cosmetic but the same species: F1 reported *its own output* as
broken when the **input** already had a relationship pointing at a part the package
did not contain. Inherited damage is now caught in preflight and named as the input's,
so "the scrub left dangling relationships" stays a true statement when it appears.

#### Refused, with a reason

Macros, OLE embeddings, signatures, comments, and **tracked changes** — the last
because accepting all revisions changes what a reader sees with markup on, which is a
content-preservation failure wearing a scrub's clothes. They belong to F2, where the
tier already licenses a rewrite. EMF/WMF media is refused rather than passed through:
it can carry text and a printer name and this project has no handler for it.

#### Named residuals

`_GoBack` is removed (it is where the cursor sat at the last save) while **other
bookmark names survive** — hyperlinks and TOC fields reference them, so removing them
would break the document; they are reported instead. `word/fontTable.xml` stays as a
weak machine-profile hint. External relationship targets stay, because removing one
breaks a link the reader can see. And the **versioned namespace declarations
themselves** (`xmlns:w14`, `w15`, `w16cid`) date the producing Word — removing a
declaration means rewriting the part and `mc:Ignorable` references it, so that is
F2's to take.

The handler is now registered in dispatch, **last**, because `PK\x03\x04` is the
weakest magic of any handler here and every format with a distinctive prefix should
decline first.

---

### 2.12 M11 as measured — E-DOCX, and three features that were measuring the wrong thing

`tests/harness/plugins/docx.py`, `tests/scrub/e_docx.py`,
`tests/scrub/gen_matrix_docx.py`, `tests/scrub/test_matrix_docx.py`.

A DOCX has **two producers in one file** — the packager that laid out the ZIP and the
document model that wrote the WordprocessingML — so the cells name which one leaked
instead of averaging them. `struct:size` is a third channel, named rather than folded
into either, on the M4A precedent.

Peer set: `synth_zipfile`, `libreoffice`, `cocoa_textutil`, `mat2_out`, three repeats
each, all rendering the same source document. **Word is not in it**, and that is a
real limitation rather than an oversight: it has no CLI on any platform, so it cannot
be made to render *our* document, and an unrelated Word file would have the classifier
separating documents while appearing to separate producers. The cell says so in its
own reason — the limit-#12 precedent.

| channel | raw | F1 |
|---|---|---|
| **packager** | `create_system`, `external_attrs`, `flags`, `order_policy`, `timestamp_scheme` | **— nothing separates producers —** |
| **model** | `entry_set`, `has_theme`, `namespaces`, `paragraph_idiom`, `part_types`, `sectpr`, `selfclose_style`, `settings_keys`, `style_ids`, `xml_decl` | *unchanged* |
| **size** | leaks | leaks |

Controls are valid on all three channels: the untouched files give the producer away
every way we know how to ask.

**A2@F1 fails, and the shape of the failure is the result.** The packager channel
closes outright — every field our writer pins stops separating anyone — while the
model channel is untouched, because F1 edits the XML only by deletion. That is the
same shape PDF F1 had (serializer closed, layout open), and it scopes F2 to exactly
one channel from a measurement rather than a guess. `xml_decl` and `selfclose_style`
are pure *spelling* and should collapse under a canonical writer; `entry_set`,
`style_ids` and `paragraph_idiom` are *substance* and are the DOCX candidates for the
residual F2 cannot close — the analogue of PDF's glyph geometry, to be confirmed or
falsified at M13 rather than assumed now.

#### Three features were measuring something other than their names

Caught by the first run, before any cell was published, and each is the same class of
error the project has hit before — a key that leaks for a reason unrelated to the
producer.

1. **`level_class` was reporting part size.** A ZIP stores raw deflate, so the level
   is recovered by recompression, and the recovered *class* narrows as a part grows:
   the synthetic producer's three tiny parts reported `5,6,7,8,9` where LibreOffice's
   eight larger parts reported `6`. Every one of those files was compressed at level
   6. The feature now answers the question we actually claim — `level_pinned`: is this
   consistent with level 6? Membership in the class is size-independent; the class is
   not.
2. **`entry_order` stopped measuring ordering.** Our writer sorts, so after F1 the
   sequence is a pure function of which parts exist — the key was reporting the
   document model through a packager-shaped hole. Replaced with `order_policy`, a
   categorical describing the *scheme* (`content-types-first-then-sorted`,
   `content-types-last`, …), which is what a packager actually chooses. Same
   abstraction as `timestamp_scheme`, for the same reason.
3. **`entry_set` was in the wrong channel.** Which parts a package contains is a
   decision of the program that wrote the document, not of the thing that zipped it:
   LibreOffice emits `styles.xml`, `settings.xml` and `fontTable.xml`, `textutil`
   emits a theme and its own `meta.xml`, Word adds `webSettings.xml`. Filing it under
   the packager would have credited F1 with a leak it cannot close, since preserving
   the part set is what content preservation *means* here.

Without those three fixes the published cell would have said the packager channel
still leaks after F1 — on the strength of a size artifact and a misfiled key.

#### The fingerprint guard failed first, and the corpus was what was wrong

It reported two long runs as our signature: the complete central-directory records of
`[Content_Types].xml` and `_rels/.rels`, **CRCs and sizes included**. Both were real:
`diverse_inputs` was writing the same three-part package every time with a different
paragraph inside, so those two parts were byte-identical across every input. The
guard was right and the corpus was wrong — a guard run over near-identical inputs
finds every shared byte and reports the format itself as a tool fingerprint, which
that function's own docstring had claimed it avoided.

Fixed by varying the **part set**, not just the text: four shapes, each internally
consistent (every extra part gets its content-type override and its relationship, so
the walker's consistency check stays meaningful). The guard then passed on **ten short
declared constants** — the pinned local-header and central-directory heads, the three
mandatory part names, the four OPC namespace URLs — with **no broad skeleton
declaration of the kind PDF needed**. That is worth stating plainly, because PDF had
to declare its whole empty document to get a clean guard and DOCX does not: a ZIP's
structure is a short prologue per entry rather than something interleaved through the
file. A test reproduces the undiverse corpus and asserts it still trips the guard, so
the pass cannot quietly become vacuous.

---

### 2.13 M12 as measured — E-SESSION-ID, and a claim this project had been repeating

`tests/scrub/e_session_id.py`, `tests/scrub/test_e_session_id.py`.

The inherited claim was *"OOXML RSIDs survive every surveyed scrubber including
MAT2"* (Müller). It sat in `CLAUDE.md` as a known conclusion, it was never measured
here, and **it is false against MAT2 0.14.0.** What is true is narrower and sharper.

Measured on two corpora — a synthetic package carrying every member of the family the
way Word writes it, and a real Word document — with the control asserted first, since
a tool cannot be shown to have missed something the input never contained:

| tool | `w:rsid*` attrs | `w:rsids` pool | `<w:rsid>` per style | `paraId` | `textId` | `docId` |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| *(real Word input)* | 7 | 1 | 36 | 2 | 2 | 2 |
| **ours F1** | **0** | **0** | **0** | **0** | **0** | **0** |
| MAT2 0.14.0 | 0 | 0 | 0 | **2** | **2** | **2** |
| ExifTool `-all=` | — | — | — | — | — | — |

**MAT2 clears the RSIDs and leaves everything else.** The leak the literature names
has been fixed upstream; the family nobody names has not. `w14:paraId` and
`w14:textId` are the sharper half, because they are **per-paragraph** and travel with
a paragraph pasted into another document — they link *files to each other*, which is
not an A1, A2 or A3 question at all. `w14:docId`/`w15:docId` is a persistent
per-document GUID, in two namespaces at once.

**ExifTool cannot write DOCX at all** — it answers `Can't write DOCX files` and
produces nothing. Reported as a capability gap rather than a failure, because the two
are not the same: a tool that declines leaves the file untouched, which is honest,
while a tool that writes an output still carrying the ids has told the user their
document is clean when it is not.

The corpus is built two ways deliberately. The synthetic package is what CI can
always build and it measures what a **tool** does; only the real Word document shows
that Word writes these ids in the first place, and that half is reported as measured
locally rather than dropped to keep CI green (limit #12).

`CLAUDE.md` has been corrected. The claim is not simply deleted — the useful version
is that a scrubber can close the named channel and still leave the unnamed one, which
is exactly the failure mode this project's locus census exists to prevent.

#### E-DOCX-THUMB, where the expectation was also wrong

`docProps/thumbnail.jpeg` is a rendered picture of the document's first page **with
its own EXIF**. MAT2 recurses into it and strips the EXIF — and **keeps the picture**.
The test asserting otherwise was written before the measurement and had to be
corrected.

Keeping it is defensible on its face: a thumbnail shows the document's own first
page, so it appears to reveal nothing the document does not already reveal. Two
things make it a leak anyway, and they are why we drop the part outright:

1. **It can be stale.** It is written when a producing application saves, and a later
   edit through a tool that does not refresh it leaves a picture of a page that no
   longer exists — the DOCX relative of PDF's incremental-update history: content the
   document no longer displays, still inside the file.
2. **It is a rendering**, so it carries the producing application's typesetting in
   pixel space — the residual E-PDF-RASTER measured at 100% producer identification.
   Keeping it hands that channel back after the XML has been normalised.

---

### 2.14 M13 as built — DOCX F2, and the honest A2-at-F2 answer

`src/scrub/formats/ooxml/xmlcanon.py`, `src/scrub/formats/docx/f2.py`,
`src/scrub/formats/docx/textextract.py`, `tests/scrub/test_docx_f2.py`.

F1 only deletes, so every part keeps the producer's own *spelling*: its namespace
prefixes, its attribute order, `<a/>` versus `<a />`, whether it escapes `>` in text,
how the XML declaration is punctuated. §2.12 measured all of it still separating
producers, and none of it is content — so F2 re-serialises every part through one
writer. The direct analogue of PDF F2's content-stream canonicaliser, one layer up.

F2 also does what F1 correctly refused: **tracked changes are accepted** (insertions
unwrapped so their text stays exactly where it was, deletions removed as the author
intended) and **comments are dropped with their anchors**. That is a stated cost, not
a free win — the markup view is gone and cannot be recovered — and it is in
`docs/limits.md` before the tier is offered, the way F3's loss of the PDF text layer
was.

#### The result

| channel | raw | F1 | **F2** |
|---|---|---|---|
| **packager** | 5 keys | — closed — | — closed — |
| **model** | 10 keys | 10 keys | **7 keys** |
| **size** | leaks | leaks | leaks |

**A2@F2 fails, and what closed is exactly the half that is spelling.** `xml_decl`,
`selfclose_style` and `namespaces` stop separating producers. What survives is
substance: `entry_set` (which parts a producer emits), `style_ids` (which styles it
defines), `settings_keys`, `paragraph_idiom` (how it represents a paragraph),
`sectpr`, `part_types` and `has_theme`. None of it can change without changing what
the reader sees — the same argument that makes glyph geometry PDF's floor — so it is
named as the residual rather than rounded away to make a cell pass. A test asserts
the F1 leak set is a strict superset of the F2 one, so a tier that bought nothing
would fail rather than look tidy.

It also closes **limit #21**: once F1 has removed the last `w14:` user, F2 drops both
the unused declaration and its `mc:Ignorable` entry, so the versioned-namespace set
that dated the producing Word is gone.

#### Four bugs, each found by a check rather than by reading

1. **The canonicaliser was not idempotent**, and `residuals()` said so on the first
   real file. A document declaring `mc` solely to carry `mc:Ignorable="w14 w15"` had
   both those namespaces pruned as unused, which emptied `mc:Ignorable`, which dropped
   the attribute — leaving `mc` declared and unused on pass one and gone on pass two.
   Canonical output that changes when canonicalised again is not canonical. Fixed by
   deciding attribute survival *before* computing the used-namespace set; a test
   asserts idempotence on every part of every producer.

2. **It broke every real reader.** Rewriting `<Types xmlns="…">` as
   `<ct:Types xmlns:ct="…">` is namespace-equivalent XML, and LibreOffice answered
   `source file could not be loaded` for all five producers. A default namespace must
   stay the default namespace. Whether a part uses one is decided by the part type
   rather than the producer — every producer writes `[Content_Types].xml` and `.rels`
   with a default namespace and `document.xml` with the `w:` prefix — so preserving it
   preserves no fingerprint. Caught only because the acceptance test is *does it still
   open*, not *did we write a file*.

3. **The content oracle was wrong, and it would have failed silently.** Extracting
   text with `<w:t[^>]*>(.*?)</w:t>` is fine until F2 exists: an empty run is written
   `<w:t></w:t>`, canonicalisation makes it `<w:t/>`, and the pattern then matches
   `<w:t/` as an opening tag and runs on to the *next* run's closing tag, swallowing
   the markup between. It reported that F2 had changed the document's text when F2 had
   done nothing of the sort — and the same regex was the harness plugin's
   `canonical_content`, where a false negative is far worse than a false alarm.
   Replaced with a parsing extractor (`textextract.py`) that also knows `w:tab` and
   `w:br` are text and `w:delText` is not.

4. **The fingerprint guard failed the moment F2 landed**, reporting the complete entry
   for `_rels/.rels` — header, name, compressed bytes and CRC — as our signature.
   Unlike §2.12's failure this one is not a corpus artefact: every package's root
   relationships part ends up declaring exactly one relationship once `docProps/*` is
   dropped, and canonicalisation then gives them all identical bytes. That is
   convergence, which is the purpose of the tier. Declared the way PDF declares its
   empty document — **generated, not transcribed**: our own output for a document with
   no content, so any common run that is a substring of it is structure by
   construction. A test asserts that skeleton contains no locus at all, so the broad
   declaration cannot hide a real leak.

#### Content preservation, checked in pixel space

Text equality only proves our extractor agrees with itself. The real gate renders each
document before and after through LibreOffice at 100 DPI and compares the images —
the same argument PDF F2 makes when it renders all five producers rather than trusting
its own invariant. **All four real producers render byte-identically before and after
F2.**

---

### 2.15 M14 as measured — DOCX F3, the tier where re-typesetting actually buys something

`src/scrub/formats/docx/f3.py`, `tests/scrub/test_docx_f3.py`.

F2 left the model's *substance*: which parts a producer emits, which styles it
defines, what it records in `settings.xml`, how it builds a paragraph. None of that
can be normalised by rewriting, because changing it changes the document. It can be
**regenerated** — open the document in one engine, save it again, and every input
comes out with that engine's part set, styles and idiom.

**This is where DOCX F3 differs from PDF's, and the difference is the whole result.**
Rasterising *relocates* the typesetter's geometry into pixels, where E-PDF-RASTER read
the producer off the ink on 100% of pages. A round-trip **rebuilds the symbolic
model** instead of photographing it. W15 predicted this was the one place in the
project where F3 might genuinely win, and the measurement says it very nearly does:

| channel | raw | F1 | F2 | **F3** |
|---|---|---|---|---|
| **packager** | 5 keys | — closed — | — closed — | — closed — |
| **model** | 10 keys | 10 keys | 7 keys | **1 key** |
| **size** | leaks | leaks | leaks | leaks |

**A2@F3 still fails**, and the honest reading is what the remaining key *is* rather
than that there is one. Every producer comes out carrying the engine's own style set;
the single difference is that a document whose *source* already defined a style keeps
it, because the engine preserves the input's styles rather than rebuilding them from
nothing. That is a **primary-production trace** — the same species as M4A's
primary-encoding trace, where a source encoded differently leaves a residue that
survives a full rebuild. It is a residue of how the original was made, not a trait of
the tool that made the file we were handed, and the cell says so instead of reporting
"the model channel still leaks".

`docProps/core.xml`, wall-clock ZIP timestamps and an `Application` string are all
things the round-trip **adds**, so F2 runs over the engine's output. That ordering is
not a detail: the engine regenerates the model, and F2 removes what the engine put in.

#### The cost, and why this corpus cannot measure it

Rendering each document before and after F3 through LibreOffice reports them
**identical** — and publishing that as "F3 is lossless" would be the overclaim this
project exists to avoid. The renderer *is* the engine that did the round-trip, so the
check is the engine grading its own homework. The real cost appears when a
Word-authored document is re-typeset by LibreOffice and then opened **in Word**, and
that is exactly what cannot be scripted on any platform — the same wall the peer set
hits, for the same reason.

So the tier's cost is reported as **not measured** rather than as zero, and a test
asserts the identical hashes so a future reader cannot mistake them for evidence of
losslessness. What can be said without measuring: LibreOffice is not Word, a
round-trip re-typesets the page, and fonts and spacing can move. That goes in
`docs/limits.md` before the tier is offered, the way F3's loss of the PDF text layer
did.

#### A harness bug the tier exposed

Re-measuring the evidence while another LibreOffice process was running produced a
bare `FileNotFoundError` from inside `oracle/floor.py`, naming neither the format, the
tier, nor the reason. The cause was older than this phase: `InProcessScrubber`
deliberately catches exceptions and returns `ScrubResult(ok=False, …)` so a broken
scrub cannot crash an oracle, and **both call sites then opened the output path
regardless**. A scrub that never wrote a file therefore surfaced as a missing file
several frames away from the failure.

F3 is simply what made it happen: it re-typesets through an engine that serialises on
its own profile lock, making it the first tier in this project that can fail for a
purely environmental reason. `floor.py` and `leak.py` now check `ok` and raise an
error naming the input, the tier and the scrubber's own message, with a regression
test. The evidence run itself was never wrong — re-run alone it confirms **63 of 63
published verdicts across all seven formats** — but a confusing failure mode is worth
fixing on the way past.

#### CI now installs LibreOffice

Without it the tier cannot run at all and its published A2 verdict goes
un-re-measured, which is precisely what `_REQUIRED_FOR_PUBLISHED_CLAIMS` exists to
prevent. It is a large install and it is installed anyway — this project already
builds `shineenc` from source rather than let a headline claim go unverified, and the
same reasoning applies here. It also earns its place twice over: it is a DOCX and PDF
peer producer, and the renderer behind the pixel-space content check.

---

## 3. Milestones

| # | Deliverable | Status |
|---|---|---|
| **M0** | W0 spike — measured qpdf's byte layout against our own fingerprint guard; serializer decision recorded | ✅ |
| **M1** | PDF corpus + E-PDF-HISTORY: incremental-update history provably gone | ✅ baseline (§2.1) — attacks + controls land; our own row needs M2 |
| **M2** | PDF walker + serializer + tokenizer; **recursive** F1 + plugin + dispatch; `/Info`, XMP, `/ID[0]`, `/Thumb`, inline images and embedded-JPEG EXIF all cleared | ✅ (§2.2) |
| **M3** | E-PDF at `raw` and `F1` — which channel leaks, measured before F2 is designed | ✅ (§2.3) |
| **M4** | PDF F2 (canonical serialise + content-stream canonicalisation) + matrix — the honest A2-at-F2 answer, whatever it is | ✅ (§2.4) — A2@F2 fails; text-operator spelling closed, glyph geometry is the residual |
| **M5** | PDF F3 + E-PDF-RASTER incl. the DPI knob; redaction detector | ✅ (§2.5) — A2@F3 fails in pixel space at every DPI down to illegibility; the knob is not a trade |
| **M6** | **OPC/ZIP spike (W8)** — the peer byte-level table filled in, compression level *recovered* rather than guessed, writer decision recorded as W0's was | ✅ (§2.7) — level 6 is the crowd (measured twice, two techniques); Word's deflate is not zlib; **we write the container ourselves**, because `zipfile` cannot emit `external_attr = 0` |
| **M7** | **Locus census (W9, E-DOCX-LOCI)** — every metadata-bearing locus in the corpus, each with a disposition | ✅ (§2.8) — 35 loci ruled; a torture package proves every rule fires; MAT2's surviving session-ID family confirmed independently |
| **M8** | **OPC walker + identification (W10)** — parses the corpus and the hostile set without crashing; `claims()` separates DOCX from every other ZIP; refusal list enforced. Dispatch **registration** deliberately deferred to M10, so the tool never advertises a format it cannot scrub | ✅ (§2.9) |
| **M9** | **ZIP writer + cross-process determinism test (W11)** — byte-identical output across several `PYTHONHASHSEED` values in **separate interpreters**, for every format, not only this one | ✅ (§2.10) — writer pinned to the measured crowd incl. `external_attr = 0`; all 15 (format, fidelity) pairs pass, the hole was latent |
| **M10** | **DOCX F1 (W12)** — parts and attributes deleted, rels and content-types kept consistent, media recursed through Phase 1; text byte-identical and **the output still opens** | ✅ (§2.11) — all five producers clean, three leaks found by strengthening one assertion |
| **M11** | **`DocxPlugin` + matrix at `raw`/`F1` (W13)** — packager and document-model channels reported separately, never averaged | ✅ (§2.12) — A1@F1 passes; A2@F1 fails on the model channel with the **packager channel closed outright**; guard passes on ten short constants |
| **M12** | **E-SESSION-ID + E-DOCX-THUMB (W13)** — the benchmark row measured on our own corpus, including whatever it says about the inherited RSID claim | ✅ (§2.13) — the inherited RSID claim is **false** against MAT2 0.14.0; the surviving family is `paraId`/`textId`/`docId`, which we clear |
| **M13** | **DOCX F2 (W14)** — XML canonicalised through one writer, rendering verified in pixel space, and the honest A2-at-F2 answer whatever it is | ✅ (§2.14) — A2@F2 fails; **spelling closed, substance is the residual**; limit #21 closed; renders byte-identically |
| **M14** | **DOCX F3 (W15)** — built, because M13's residual was exactly a model channel a re-typeset regenerates | ✅ (§2.15) — the model channel collapses from 7 keys to **1**, and that one is a *primary-production trace*, not the immediate producer. A2@F3 still fails; the tier's rendering cost is **not measurable** with LibreOffice as both engine and renderer |

M6–M9 are all *before any scrubbing happens*, and that is the same ordering argument
as M1-before-M2 and M3-before-M4: the two things that have actually bitten this project
are normalising by guesswork (PDF's deflate level 9) and a determinism hole the in-process
floor test cannot see. Both are cheap to settle first and expensive to retrofit.

M1 comes before the walker deliberately: E-PDF-HISTORY is binary, provable, the headline
result of the phase, and depends on none of the contested design points. M3 comes before
F2 for the reason recorded in W5 — designing F2 before measuring which features actually
leak is normalising by guesswork.

Phase 3 is **done** when both formats have validated matrices, every residual is in
`docs/limits.md`, the PDF A2-at-F2 frontier is characterised rather than asserted, and
the RSID benchmark row is measured on our own corpus.

**Phase 3 is complete.** Both formats have validated matrices across all three tiers,
every residual named above is in `docs/limits.md` (#13-#27), the PDF A2-at-F2 frontier
is characterised as glyph geometry rather than asserted, and the session-ID benchmark
row is measured on our own corpus — where it **corrected the claim the phase set out
to confirm**.

The closing ledger, in the shape Phase 2's takes:

**Solved.** PDF revision history (rebuilt from the object graph, so history dies by
construction). PDF serializer channel, closed at F1. DOCX packager channel, closed at
F1. DOCX A1 at every tier, with the sentinel in four loci at once. The full DOCX
session-ID family — `w:rsid*`, the pool, the per-style `<w:rsid>`, `w14:paraId`,
`w14:textId`, `w14`/`w15:docId` — where MAT2 clears only the first three and ExifTool
cannot write the format at all. `docProps/thumbnail.jpeg`, dropped rather than
cleaned, because a thumbnail can be stale. XML comments and processing instructions,
found by looking rather than by the corpus. DOCX limit #21, closed at F2.

**Cannot, and why.** PDF glyph geometry: unchangeable without re-typesetting the page,
and F3 relocates it into pixels rather than removing it (100% producer identification
at every DPI down to illegibility). PDF redaction: a different problem, warned about
and never fixed. DOCX model substance at F2: which parts, which styles, which
settings — unchangeable without changing the document. DOCX `style_ids` at F3: a
primary-production trace, the source's own styles surviving the rebuild.

**Named rather than glossed.** Word cannot be scripted, so it is absent from the DOCX
peer set and locally-measured elsewhere. The DOCX F3 rendering cost cannot be measured
with LibreOffice as both engine and renderer, and is reported unmeasured rather than
zero. File size leaks at every tier of both formats. The tracked-changes trade at F2
is a real loss of the review history.

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

DOCX predictions, on the same terms — written down so measurement can falsify them:
- **The document model is the F2 residual**, as glyph geometry is PDF's: which styles a
  producer defines, which compat settings it writes, how it spells a paragraph. Erasing
  it means regenerating the document through one engine, which is F3's job and F3's
  cost.
- **Editing-session identifiers are a wider family than RSIDs** — `w15:docId` and
  `w14:paraId`/`w14:textId` link *documents to each other*, which is a sharper attack
  than naming a producer, and it is not modelled by A1/A2/A3 at all. Closest neighbour
  is the A3 differential tier, but the reference here is another scrubbed file rather
  than the original.
- **Tracked changes and comments are content, not metadata**, so at F1 they are a
  refusal rather than a silent accept-all. The plainest statement of the limit is that
  a tool cannot both preserve what the document displays with markup on and remove the
  author names inside it.
- **A DOCX is a container of whole other files** — OLE embeddings, macros, EMF/WMF
  drawings — and a container we refuse rather than half-scrub, on the PDF-attachment
  precedent.
- **Word is not automatable here.** The producer that writes the RSIDs is macOS/Windows
  desktop software with no CLI, so its rows are measured locally and reported as
  not-automated, exactly as Apple's AAC encoder is (limit #12) — never quietly dropped
  from the peer set to make CI green.

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
