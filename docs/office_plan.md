# Excel and PowerPoint (XLSX, PPTX) — plan

Excel and PowerPoint files are refused today: the ZIP handler stops at any Office
package (limit #59) and the DOCX handler declines anything that is not Word. They
are the same container as Word — a ZIP of XML parts joined by relationships — so the
container layer (`ooxml/zipread`, `opc`, `xmlsurgery`, `zipwrite`) is already built
and measured. What differs is where each program keeps its people, paths and IDs,
and what counts as content. This plan measures that first, then builds both at F1
on the shared layer, Excel first.

---

## 1. Where the list of loci comes from

1. **The format** — ECMA-376 SpreadsheetML and PresentationML, and Microsoft's
   extensions (`x14`/`x15`/`xr*` for Excel, `p14`/`p15`/`p188`/`p1510`/`a16` for
   PowerPoint): every element that carries a person, a date, a path or an ID.
2. **Probes** (§2): a package of each kind with a `PLANT_` value in every locus in
   §3, built by `scratchpad/office_probe/build.py` (to become
   `tests/scrub/office_corpus.py`). Both open in LibreOffice (rendered to PDF).
3. **Real files.** One vendor XLSX ships on this Mac (Keynote's `FontTemplate.xlsx`,
   written by Mac Excel 2011). The real Word corpus (`docs/word_plan.md` §5) holds 28
   embedded Excel workbooks. **Excel, PowerPoint and Word 16.114 are installed on
   this Mac**, so Office itself can now be a producer — the gap `docs/word_plan.md`
   §1 worked around. A public corpus (Apache POI and LibreOffice test files, as for
   Word) is the next source, downloaded only with permission.

## 2. Measured: the benchmark tools (O0, 2026-10-29)

**ExifTool 13.55 cannot write either format** (`Writing of XLSX files is not yet
supported`, the same for PPTX), as with DOCX.

**MAT2 0.14.0 refuses a file with any part it has no rule for.** Probes split into
one feature each, so each verdict is one feature's:

| probe (one feature added to a minimal package) | MAT2 0.14.0 |
|---|---|
| XLSX: properties, printer settings, picture, hyperlink | cleaned — leaves 12 of 19 planted values (below) |
| XLSX: + legacy comments / + threaded comments and persons | cleaned; comments **deleted** (content) |
| XLSX: + external workbook link | **refused** |
| XLSX: + pivot cache | **refused** |
| XLSX: + data connection | **refused** |
| PPTX: properties, thumbnail, printer, tags, notes, hidden slide, sections | cleaned — leaves 7 of 17 |
| PPTX: + legacy comments / + modern comments / + `revisionInfo.xml` / + `changesInfos/` | **refused** (each) |
| PPTX: + a chart whose workbook is `Microsoft_Excel_Worksheet1.xlsx` | cleaned — **the workbook passes through untouched**: 31 planted values inside it survive |

What MAT2 leaves in a file it calls clean:

- **XLSX**: the folder the workbook was saved in (`x15ac:absPath`, a user name in
  `C:\Users\…`), the per-document GUID (`xr:revisionPtr documentId`) and the
  last-save and view GUIDs (`xr10:uidLastSave`, `xr2:uid`), per-sheet GUIDs
  (`xr:uid`), the workbook password hash, a hidden defined name, a picture's
  description holding its file path, an external hyperlink's UNC path, a string no
  cell uses (`sharedStrings.xml`), plus content: the header text and a hidden
  sheet's text.
- **PPTX**: the per-shape GUID `a16:creationId` (MAT2 randomizes `p14:creationId`
  only — the older of the two), section names and their GUIDs, a picture's file
  path, an external hyperlink's UNC path, plus content: speaker notes and a hidden
  slide.
- **The chart workbook.** MAT2's allowlist keeps `…/embeddings/Microsoft_Excel_Worksheet[0-9]+\.xlsx`
  *as is*; only a name without a digit is cleaned recursively. **Measured on real
  files**: in the 784 Word documents, 17 of 28 embedded workbooks are named with a
  digit; MAT2 cleaned 8 of the 11 documents holding one (refused 3), and in its
  outputs **10 of 17 embedded workbooks still name their author**
  (`docProps/core.xml`). The same rule applies to PowerPoint and Excel.

## 3. Census: what each program can write, and the decision

Status: **probe** = planted and measured above; **spec** = from the format, to be
confirmed on real files (O0b) before it is claimed.

### Shared by all three (the package)

| locus | reveals | decision |
|---|---|---|
| `docProps/core`, `app`, `custom` (sensitivity labels: tenant ID), thumbnail, `customXml/` | author, editor, company, manager, dates, tenant | drop (as DOCX) — probe |
| `*/printerSettings/*.bin` | printer name (a DEVMODE) | drop, and the `r:id` that points at it — probe |
| `*/media/*` | the picture's own metadata | each by its own handler (`embedded.py`) — probe |
| picture `cNvPr name/descr` holding a file name or path | the inserted file's path | blank (DOCX rule, shared) — probe |
| external hyperlinks | a path or URL | kept (visible link), reported — probe |
| `*/embeddings/*.xlsx`, `.docx`, `.pptx`, `.sldx` | a whole document of its own | **cleaned by its own handler** (recursion) — probe |
| `*/embeddings/*.bin`, `.xls`, `.doc` (OLE compound files) | a legacy document | refused (no CFB reader) — census |
| macros, ActiveX, signatures, encryption, DOCTYPE | — | refused (`opc.REFUSE_PARTS`, limit #75) |

### Excel

| locus | reveals | decision |
|---|---|---|
| `x15ac:absPath` (in `mc:AlternateContent`) | the save folder, often `C:\Users\<name>\` | remove — probe |
| `xr:revisionPtr`, `xr*:uid`, `xr10:uidLastSave` | per-document, per-sheet, per-object GUIDs (co-authoring) | remove — probe |
| `workbookProtection` / `sheetProtection` hash and salt | a credential | remove the hash, keep the lock flags — probe |
| `fileSharing userName`, reservation hash | who reserved the file; a credential | remove — spec |
| `workbookView` window position and size | the screen it was last open on | remove — spec |
| `sheetView` selection / active cell | where the cursor was (Word's `_GoBack`) | remove — spec |
| `fileVersion` build, `calcPr calcId`, `x14ac`… namespaces | Excel's version | kept at F1, reported; F2 — spec |
| shared strings no cell uses | deleted text | blanked in place (indexes kept) — probe |
| legacy and threaded comments, `persons/` | names, e-mail addresses, text | refused at F1 (content, as Word) — probe |
| `xl/revisions/` (shared-workbook change log) | every editor and old value | refused (tracked changes) — spec |
| external links (`externalLinks/`) | another workbook's path **and a cached copy of its cells** | refused at F1 — probe |
| pivot cache records | a full copy of the source data, even from a deleted sheet | refused at F1 — probe |
| data connections, Power Query (`customXml` DataMashup) | server names, user names, queries | refused at F1 — probe / spec |
| hidden and very-hidden sheets, hidden rows and columns, hidden names | content the reader does not see | kept, reported — probe |
| headers and footers | text, sometimes a name | kept (content), reported — probe |

### PowerPoint

| locus | reveals | decision |
|---|---|---|
| `a16:creationId` (per shape), `p14:creationId` (per slide) | per-object IDs; by the format's design they stay with a copied shape (copying not measured) | remove the extension holding them — probe |
| `revisionInfo.xml` | the client GUIDs of every app that saved it | drop — probe |
| `changesInfos/` | co-authors' names and user IDs | drop — probe |
| `presProps.xml` recent colours (`clrMru`) | the author's recent picks | remove — spec |
| `viewProps.xml` | the last view | drop — spec |
| `tags/` | add-in data (some add-ins keep chart data here) | measure on real files before deciding — probe |
| legacy comments + `commentAuthors.xml`; modern comments + `authors.xml` | names, e-mail addresses, text | refused at F1 (content) — probe |
| section names and GUIDs | how the deck is organised; IDs | names kept (shown in the sorter), GUIDs to F2 — probe |
| speaker notes, hidden slides | content the audience does not see | kept, reported — probe |
| a cropped picture (`a:srcRect`) | **the whole uncropped original** | kept, reported (cropping is a pixel edit) — probe |
| artistic effects / background removal (`.wdp`, `a14:imgLayer`) | the unedited original picture | refused (no HD Photo handler, #68) — spec |
| embedded fonts | the font's own names; a key | measure (W3) — spec |
| embedded video and audio | the clip's own metadata | each by its own handler — spec |

## 4. Measured on real workbooks (O2, 2026-10-29)

27 real XLSX files: Keynote's `FontTemplate.xlsx` (Mac Excel 2011) and the 26
embedded workbooks of the 784 real Word documents that open as XLSX (21 written by
Excel, 4 by ONLYOFFICE, 1 by LibreOffice). All 27 cleaned at F1 (none holds a
refused part); none has a residual; every output is a fixed point; every
`<sheetData>` is byte-identical; no `dc:creator` and no `absPath` survives; and
LibreOffice's CSV of each is identical before and after. Aggregates only, from a
one-off script; it becomes `scripts/office_census.py` with O0b.

**Fuzzed**: 3,000 damaged workbooks (archive bytes flipped, or XML parts edited and
re-zipped): 2,222 refused, 778 cleaned with no residual, nothing else. The first run
found an XML declaration naming an unknown encoding (`encoding="K8"`) escaping as a
`LookupError` from BOTH shared parsers (`opc`, `xmlspans`) -- so a damaged `.rels`
crashed Word's F1 too. Mapped to a refusal at both; tested. Embedded workbooks are still refused *inside* a
Word file until O4 wires the recursion.

**PowerPoint (O3, 2026-10-30).** No real PowerPoint-written deck exists on this
Mac yet (O0b). Measured instead: `tests/scrub/pptx_corpus.py` with every §3 locus
planted -- two decks differing only in metadata come out byte-identical, every
slide's text, positions, sizes and crops identical, LibreOffice Impress's page
renders pixel-identical (and identical across two renders of the same file, so the
comparison means something); a deck and a workbook re-saved by LibreOffice 26.2
(a real producer) clean with no residual. **Fuzzed** 3,000 damaged decks: the first
run cleaned 389 whose kept XML no parser could read -- the PowerPoint rules mostly
edit bytes in place, so a broken part passed through them. Excel and PowerPoint F1
now refuse a kept part that is not well-formed (`package_f1.malformed_parts`);
re-run: 2,231 refused, 769 cleaned, every output parses, nothing escapes. Excel's
corpus outputs and its 27 real workbooks are unchanged by the rule.

## 5. Content: how a clean is checked

- **Excel**: every cell's value, type and formula identical before and after, read
  by our own parser; then LibreOffice Calc's rendering and Quick Look.
- **PowerPoint**: every slide's text and shape geometry identical; LibreOffice
  Impress's rendering of every slide (render each side several times — Writer was
  nondeterministic, Impress unmeasured); Quick Look.
- **Office itself**: a cleaned file opened in Excel or PowerPoint on this Mac must
  open without a repair prompt. Unscriptable without granting automation, so it is a
  short manual check per milestone, recorded here.

## 6. Milestones

- **O0 — survey** (done: §2 probes, MAT2 and ExifTool benchmarks, the real Word
  corpus's embedded workbooks). **O0b**: a real-file census once a corpus exists
  (public test files on permission, and files saved from Office on this Mac).
- **O1 — the shared package layer** (**done**, `ooxml/package_f1.py`; DOCX F1 and F2
  output and refusals byte-identical on all 965 corpus files), lifted out of DOCX F1: parts dropped, content types and relationships pruned, external
  and settings relationships pruned, empty `.rels` removed, picture names, linked
  paths, media through `embedded.py`, the orphan check.
- **O2 — Excel F1** (**done**, `formats/xlsx/`; `tests/scrub/xlsx_corpus.py` plants
  every locus in §3; two workbooks differing only in metadata come out byte-identical;
  every `<sheetData>` byte-identical; LibreOffice's CSV and page render identical;
  limits #76–#77). Matrix `xlsx` with O5.
- **O3 — PowerPoint F1** (**done**, `formats/pptx/`; limits #78–#79). Matrix `pptx`
  with O5. Open: Word F1 does not yet refuse a kept part that is not well-formed
  (5 of 946 real Word files hold one, mostly empty parts) -- adopting the rule
  changes Word's outcomes, so it is measured on its own before it is taken.
- **O4 — Office packages inside Office packages**: embedded workbooks, documents and
  slides cleaned by their own handler, in all three formats. Closes the 28
  embedded-workbook refusals among Word's 33 (limit #75).
- **O5 — results**: A1 matrices, a live MAT2/ExifTool section in `scripts/benchmark.py`, A2 measured on real producers (Excel, LibreOffice,
  Numbers/Keynote export, Google), limits, README.
- **Later (F2)**: comments and tracked changes resolved; pivot caches without
  records; external links broken to values; canonical XML through `xmlcanon.py`.
