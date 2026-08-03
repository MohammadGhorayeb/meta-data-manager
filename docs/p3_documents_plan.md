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
and exiv2). No `qpdf`, `gs` or `pdftk` binary is installed, so the PDF path must be
library-only or declare the dependency the way `shineenc` was. DOCX is a ZIP of XML,
so the standard library covers it; `olefile`/`oletools` only if legacy `.doc` enters
scope.

---

## 1. Work items (dependency order)

### W1 — PDF structure walker
`src/scrub/formats/pdf/`. Enough of the file structure to account for every byte and
fail closed on anything unexplained, mirroring the MP3/FLAC/ISOBMFF walkers:
- header, cross-reference table or stream, trailer, and **every `startxref` chain**;
- object inventory with generation numbers;
- **incremental-update sections** — the critical one, see W3;
- object streams (`ObjStm`) and cross-reference streams, which hide objects from a
  naive scan;
- linearisation ("fast web view") artefacts, which are themselves a producer tell.

### W2 — PDF F1 (metadata strip, structure preserved)
- `/Info` dictionary — Title, Author, Subject, Keywords, Creator, **Producer**,
  CreationDate, ModDate.
- **XMP metadata** — reuse `src/scrub/standards/xmp.py` from Phase 1 rather than
  writing a second parser. PDFs routinely carry BOTH `/Info` and an XMP packet with
  the same values, so clearing one is the classic half-scrub.
- Document ID (`/ID` array) — two hashes that link revisions of the same document.
- Embedded file attachments (`/EmbeddedFiles`), which can be *anything*.
- Annotations carrying author names, and form-field values.

### W3 — Incremental-update history (the leak that defines this phase)
A PDF is edited by **appending** a new revision, leaving the old objects in the file.
Text "deleted" three revisions ago is still there in full, and this is the single most
famous PDF disclosure mode — redactions published with the original text one layer
down. Consequences for us:
- the scrub must **rewrite** the document, never append — an appended cleanup leaves
  the dirty original directly above it in the same file;
- the residual check must confirm no earlier revision survives, by walking the
  `startxref` chain and asserting exactly one;
- **glyph-position redaction leak** (Bland): even in a rewritten file, text covered by
  a black rectangle is still in the content stream. Out of scope for a metadata
  scrubber, but it must be *said*, because users assume otherwise.

### W4 — Embedded image streams (recursion into Phase 1)
`DCTDecode` streams are JPEG files carrying their own EXIF/GPS; `FlateDecode` image
XObjects can carry ICC profiles. Feed them through the existing JPEG/PNG handlers
rather than deleting them — the image is content, its metadata is not. This is the
container-recursion failure mode the whole build order exists to handle.

### W5 — `PdfPlugin` + A2 channel
`structural_features` for the producer fingerprint: object ordering, xref style
(table vs stream), compression choices, `/Producer` (before stripping), font-subset
naming (the `ABCDEF+Font` six-letter tag is generator-dependent), linearisation, and
the ID scheme. This is the DQT-equivalent and the input to E-PDF.

### W6 — PDF F2, and the honest A2 answer
Rewrite through one canonical writer (pikepdf/qpdf with fixed settings) so object
order, compression and xref style come from one producer. Then **measure** whether
that is enough for A2 — the literature says no tool has achieved it, so the likely
honest outcome is a fail with named residuals (font subsets, content-stream operator
style, image encoder fingerprints). Characterising *why* precisely is the deliverable
here; a pass is not assumed.

### W7 — OOXML/DOCX walker + F1
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

### W8 — `DocxPlugin` + E-RSID
`structural_features`: RSID inventory, ZIP entry order, compression levels per entry,
timestamp scheme. E-RSID then measures the benchmark claim directly: do RSIDs survive
MAT2 (expected: yes) and our F1 (target: no)?

---

## 2. Experiments

| # | Question | Status |
|---|---|---|
| **E-PDF-HISTORY** | Does an earlier revision survive our scrub? Build a PDF with N incremental updates, scrub, and carve for text that was "deleted" in revision 1. | 🔜 |
| **E-PDF** | Do object order, xref style and font-subset tags identify the producing application, and does the canonical rewrite (F2) erase them? | 🔜 |
| **E-RSID** | Do RSIDs survive MAT2 and ExifTool, and does our F1 clear them? The published claim, measured on our own corpus. | 🔜 |
| **E-DOCX-THUMB** | Is `docProps/thumbnail.jpeg` cleared, and is its own EXIF cleared with it? | 🔜 |

Phase 2's design rules carry over and are not negotiable: **assert the controls** (if
the attack cannot identify the producer of an unscrubbed file, its failure on a
scrubbed one proves nothing); **verdicts are significance tests, not thresholds** on a
statistic with a wide standard error; and **content that carries the signal** —
a one-line PDF has no font-subset variety to fingerprint.

---

## 3. Milestones

| # | Deliverable | Status |
|---|---|---|
| **M1** | PDF walker + F1 + plugin + dispatch; `/Info` and XMP both cleared | 🔜 |
| **M2** | E-PDF-HISTORY: incremental-update history provably gone | 🔜 |
| **M3** | Embedded image streams recursed through the Phase 1 handlers | 🔜 |
| **M4** | PDF F2 + E-PDF + matrix — the honest A2-at-F2 answer, whatever it is | 🔜 |
| **M5** | DOCX walker + F1 + plugin + matrix, ZIP timestamps consistent | 🔜 |
| **M6** | E-RSID: RSIDs cleared where MAT2 leaves them; benchmark row written | 🔜 |

Phase 3 is **done** when both formats have validated matrices, every residual is in
`docs/limits.md`, the PDF A2-at-F2 frontier is characterised rather than asserted, and
the RSID benchmark row is measured on our own corpus.

---

## 4. Known limits expected to come out of this phase

Recorded here as predictions to be tested, not conclusions — they go to
`docs/limits.md` only once measured:
- **Redaction is not metadata scrubbing.** Text hidden under a black box remains in
  the content stream. We will not fix that, and users must be told plainly.
- **Font-subset fingerprints** are likely to survive F2 and may be irreducible without
  re-embedding fonts, which changes rendering.
- **Scanned PDFs are images**, so PRNU applies exactly as it does in Phase 1 — a
  structural impossibility inherited, not a new one.
- **Digital signatures** are invalidated by any rewrite. That is unavoidable and worth
  stating up front: a signed PDF cannot be scrubbed and stay signed.
