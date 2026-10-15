# Documents made in Word — plan

DOCX F1/F2/F3 were built and measured in Phase 3 (`docs/p3_documents_plan.md`), but
against documents written by LibreOffice, macOS `textutil`, MAT2's rewriter and a
synthetic writer -- Word cannot be driven from a script, so it was never a producer.
One real Word file exists here (`tests/corpus/docx/word_a.docx`, Word 16). Word is
what most people write in, and it writes parts and elements the others never do.
This plan finds what Word writes, closes what F1 misses, and validates on real files.

---

## 1. Where the list of loci comes from

Word cannot run here, so the census is built from four sources, strongest first:

1. **Word's own list of hidden data** -- the categories its Document Inspector
   offers to remove: comments, revisions and annotations; document properties and
   personal information; task pane add-ins; embedded documents; macros, forms and
   ActiveX controls; collapsed headings; custom XML data; headers, footers and
   watermarks; invisible content; hidden text. Microsoft's checklist of where a
   Word document keeps things its reader does not see.
2. **The format's elements that carry a person, a date, a path or an ID** -- ECMA-376
   and Microsoft's Word extensions (the `w14`/`w15`/`w16*` namespaces): every
   revision record (`w:author`, `w:date`), editing permissions (`w:ed`), protection
   hashes, document variables, linked files, embedded-font keys.
3. **Real Word files**: the one here, then a public corpus of real Word-made
   documents (to be downloaded only with permission), then files the user makes.
4. **Probes**: a Word-shaped fragment planted in a document and run through F1 --
   which is how §2's two leaks were confirmed.

## 2. Census: what Word can write, and what F1 does today

| locus | what it reveals | F1 today | decision | milestone |
|---|---|---|---|---|
| `docProps/core`, `app`, `custom`; thumbnail; `customXml/`; `people.xml` | author, last editor, company, template, editing time, IDs | dropped | -- | done |
| `w:rsid*`, `w14/w15 paraId/textId/docId` | editing sessions, paragraph and document IDs | removed | -- | done |
| `w:attachedTemplate`, `w:mailMerge`, `w:documentProtection`, `_GoBack` | paths, password hash, cursor | removed -- **but the template's and data source's paths, held in `settings.xml.rels`, stayed (measured, §5)** | drop relationships nothing refers to; remove the bookmark's end marker too | **W1b** |
| insertions, deletions, moves, comments | other people's text and names | refused (F1), accepted/dropped (F2) | -- | done |
| **tracked formatting changes** (`w:rPrChange`, `w:pPrChange`, `w:sectPrChange`, `w:tblPrChange`, `w:trPrChange`, `w:tcPrChange`, `w:tblGridChange`, `w:numberingChange`) and table-cell revisions (`w:cellIns`, `w:cellDel`, `w:cellMerge`) | **reviewer's name and the date -- measured surviving F1** | **passes through** | refuse at F1 like other revisions; accept at F2 | **W1** |
| **editing permissions** (`w:permStart w:ed`/`w:edGrp`, `w:permEnd`) | **an editor's e-mail address -- measured surviving F1** | **passes through** | remove the range markers (they draw nothing) | **W1** |
| `w:writeProtection` | a password hash and salt | passes through | remove, like `w:documentProtection` | W1 |
| `w:docVars` | template and document-management variables (client, matter, user) | passes through | remove: no field result changes until fields update | W1 |
| task-pane add-ins (`word/webextensions/`) | which add-ins, their store and settings | passes through | drop the parts | W1 |
| ActiveX controls (`word/activeX/`) | controls with their own binary data | passes through | refuse, as macros are | W1 |
| printer settings (`word/printerSettings/`) | the printer's name and driver settings | passes through | drop | W1 |
| pictures' source names (`*:cNvPr name`, `wp:docPr descr/title`, VML `o:title`) | the inserted file's name (a camera counter, a screenshot timestamp), sometimes its full path | kept | blank names and descriptions that are file names or paths; blank VML `o:title` always | **W1b** (measured, §5) |
| linked pictures (`r:link` to `file:///C:/Users/...`) | a path naming the user | kept, advised | where an embedded copy exists, drop the link | **W1b** |
| images other than JPEG/PNG (GIF, TIFF, WebP, SVG) | each image's own metadata | **refused** | clean through `embedded.py` by content | **W2** |
| EMF/WMF pictures (pasted charts, Visio, clip art) | a description string, private comments (paths), a hidden second copy | refused -> **cleaned (§6)** | own handler, `formats/metafile/` | **W2b done** |
| embedded fonts (`word/fonts/*.odttf`, key in `fontTable.xml`) | a GUID key per font -- possibly per save | kept | measure whether the key is random per save; if so, re-key | W3 |
| charts' workbooks, OLE objects (`word/embeddings/`) | a whole Excel file with its own author and company | **refused** | measure; clean embedded OOXML packages' properties, or keep refusing | W4 |
| fields showing metadata (`AUTHOR`, `FILENAME \p`, `USERNAME`, `LASTSAVEDBY`, `DOCPROPERTY`, dates, `INCLUDEPICTURE` paths) | the author's name, a path, shown as text | kept (displayed content) | advise where they are | W5 |
| hidden text (`w:vanish`) | text the reader does not see | kept | advise (content, not metadata) | W5 |
| glossary (building blocks saved in the file) | reusable snippets: signature blocks, addresses | kept | measure; drop if never displayed | W5 |

## 3. Milestones

- **W1 — close the measured leaks and Word's other hidden parts.** Revision records
  beyond insert/delete (refused at F1, accepted at F2), permission markers, the
  write-protection hash, document variables, add-in and printer-settings parts,
  ActiveX refused; then picture names and linked-picture paths once measured.
- **W2 — every picture cleaned by its own rules**: GIF, TIFF, WebP, SVG through the
  shared `embedded.py` (identified by content, not extension); then EMF/WMF.
- **W3 — embedded fonts**: is the font key a per-save ID? If so, re-key.
- **W4 — embedded objects**: what a chart's workbook carries; clean or refuse.
- **W5 — advisories**: metadata shown as text, hidden text, building blocks.
- **W6 — validation**: a Word-shaped corpus with every locus planted (A1 extended);
  LibreOffice's render before and after (content); a public corpus of real Word
  documents and the user's own files, with a second reader.

Each milestone: probes first (a planted value that survives is a failing test
before it is a fix), then the change, then the corpus and docs.

---

## 4. W1 and W2 as built

**W1 (no measurement needed).** F1 now drops `word/webextensions/` (task-pane
add-ins) and `word/printerSettings/` (the printer's DEVMODE) with their
relationships and content types; removes `w:writeProtection` (a password hash),
`w:docVars` and the editing-permission markers `w:permStart`/`w:permEnd` (the text
between stays); refuses tracked formatting changes and table-cell revisions as it
refuses insertions and deletions (`w:rPrChange`, `w:pPrChange`, `w:sectPrChange`,
`w:tblPrChange`, `w:tblPrExChange`, `w:trPrChange`, `w:tcPrChange`,
`w:tblGridChange`, `w:numberingChange`, `w:cellIns`, `w:cellDel`, `w:cellMerge`).
F2 accepts the formatting changes and inserted cells and refuses deleted or merged
cells (accepting one means rebuilding the table). The package layer refuses ActiveX
controls. `tests/scrub/word_corpus.py` builds each locus the way Word wires it; the
12 tests fail without the change. Picture names and linked-picture paths needed
real Word files; they are W1b (§5).

**W2 (pictures).** `word/media/` goes through the shared `embedded.py`: each picture
is identified by its content and cleaned by its own handler -- JPEG and PNG exactly as
before (byte-identical, tested), and now GIF, TIFF, WebP and SVG, which were refused
by extension. Two latent defects went with the old rule: a picture was trusted to be
what its extension said, and an extension not on the list (`.wdp`, a renamed file)
passed through unscrubbed; both now go by content, and content no handler claims is
refused. EMF, WMF and HD Photo remain refused, by name (limit #68).

## 5. Measured on real Word documents (W1b)

**The corpus.** Two public collections of test documents, downloaded to
`~/metadata-research/step2/docx_corpus/` (never into the repo): Apache POI's 130 and
LibreOffice's 835 `.docx`. `docProps/app.xml` names the program that saved each;
**784** were saved by Microsoft Word (774 Word for Windows, 9 Word for Mac, 1 newer
Word), the rest by LibreOffice, Outlook, Apache POI or nothing. They are bug-report
and test files, so they over-represent oddities -- which is what a census of loci
wants. `scripts/docx_census.py` prints aggregates only: no value from any document.

**What F1 did with them, before W1b.** 678 cleaned with `residuals()` silent, 106
refused: 33 embedded OLE objects, 21+5+3 tracked insertions, moves and deletions,
16 comments, 8 tracked formatting changes (W1's refusal, now with real cases), 5
ActiveX, 10 EMF/WMF/HD Photo pictures (limit #68), 1 DOCTYPE. No exception escaped.

**What survived F1 in the 678 it cleaned** -- each a fix in W1b, counted again after:

| what | where | before | after |
|---|---|---|---|
| the template's path and the mail-merge source's | `settings.xml.rels`, after F1 removed the elements that used them | 27 relationships | 0 |
| a picture's file name or path as its name or description | `pic:cNvPr name` (40) and `descr` (38), `wp:docPr descr` (46), `a:cNvPr`, `v:shape alt`; 52 of them with the folder | 128 values | 0 |
| a VML picture's title -- the inserted file's name without its extension | `v:imagedata o:title` (non-empty in 30 of 69 in all 784) | 26 values | 0 |
| any part naming a user's folder (`C:\Users\<name>\`, `/Users/<name>/`) | the three rows above | 16 parts | 0 |
| Word's last-cursor bookmark | `_GoBack` | 498 | 0 |

Two findings went beyond the plan. The template and mail-merge **elements** were
removed in Phase 3, which looked closed; their **relationships** kept the paths, and
the test checked the element. F1 now drops, after every part is cleaned, each
external relationship no element refers to and each `settings.xml` relationship
nothing refers to (`_prune_unreferenced`; F2 calls it too). And the first list of
drawing elements named `pic:cNvPr` and `wp:docPr`; a real file held a full path under
a user's folder in `a:cNvPr`, so the rule now takes any `*:cNvPr`.

What is kept: generic names ("Picture 1", 433 in the corpus) and descriptions a
person wrote (alt text, 57) -- a description is blanked only if it is a file name or a path.
A picture both embedded and linked loses the link (it shows the embedded copy); one
that is only linked keeps it, and `residuals()` advises.

**Content.** 60 of the cleaned documents (10 with pictures, 41 with the `_GoBack`
bookmark), rendered by LibreOffice to PDF before and after: **text identical in 60/60,
pixels identical in 56/60.** The 4 were bisected by putting each kind of removal
back: the `_GoBack` bookmark alone accounts for all 4 -- LibreOffice lays text out in
pieces split at bookmarks, so a justified line holding the collapsed marker is spaced
a fraction of a pixel differently without it. The end marker is now removed with the
start (a dangling `w:bookmarkEnd` is a defect of its own); the 4 still differ. The
cursor position is metadata, so the removal stays: limit #69.

**Not measurable here.** No document in the corpus embeds a font (`word/fonts/`,
0 of 784), so W3 needs Word itself or the user's own files.

Tests: `tests/scrub/test_docx_word.py` (W1b block) plants each locus in
`word_corpus.py` the way the real files wire it; all 6 fail without the change.

## 6. W2b — EMF and WMF pictures

Office stores pasted charts, Visio drawings, clip art and equations as Windows
metafiles: a list of drawing records (EMF: 32-bit records, a header naming its
size; WMF: 16-bit, often behind a "placeable" header). Limit #68 refused them.

**Survey** (`~/metadata-research/step2/emf/`: every distinct metafile inside the
784 real Word documents -- 50 EMF, 361 WMF, all from Word; aggregates only):

| locus | found | what it is | drawn? |
|---|---|---|---|
| EMF header description | 2 of 48 non-empty | the producing application, optionally the picture's name | no |
| EMF private comments (any identifier but `EMF+`/`GDIC`) | 25: **4 paths, 2 file names**, 14 text, 5 binary | whatever the producing program kept | no -- GDI skips comments |
| EMF public comment `WINDOWS_METAFILE` | 19 EMFs, up to 340 KB | **a whole second copy of the picture**, as the WMF it was converted from | no |
| EMF public `BEGINGROUP` | 2 (empty) | a group's description string | no |
| EMF+ records (inside comments) | 21 EMFs | the GDI+ drawing; Office draws these, not the EMF records | **yes** |
| EMF+ image objects | 1 JPEG (APP12 "Ducky", APP14), 1 PNG | a whole embedded picture, with its own metadata | yes |
| EMF+ comment records | 0 | private data | no |
| WMF `MFCOMMENT` escapes | 'WMFC' in 2 files; 'MGXI' 1; binary 3 | 'WMFC' chunks = **the original EMF, embedded** (Windows and LibreOffice draw it instead of the WMF records when present); others private | WMFC: yes |
| bytes after the end record | 3 WMF (1 non-zero), 0 EMF | anything | no |
| reference device (EMF `szlDevice`/`szlMillimeters`, EMF+ DPI, WMF `Inch`) | 29 distinct devices in 48 EMFs; DPI 94-1200; WMF Inch 94/95/129 among 96s | the author's screen or printer | **geometry** -- the picture's coordinates are expressed in it |
| DIB bitmaps (`BITBLT`, `STRETCHDIBITS`, ...) | 32, all 40-byte headers | pixels; a V5 header could link a colour profile by PATH | yes |
| colour-space records (filenames), printer escapes | 0 | a profile path; a driver name | -- |

Header counts: all 48 EMFs' `nBytes` match; 3 carry a wrong `nRecords`. All WMF
sizes, maximum record sizes and placeable checksums are consistent.

**F1 decisions.** Rebuilt record by record (EMF has no absolute offsets: every
offset is relative to its own record); counts and sizes recomputed.

- EMF: header description removed, `sReserved` zeroed; private comments,
  `WINDOWS_METAFILE` copies and string comments dropped; `BEGINGROUP` description
  removed (the group stays: an `ENDGROUP` pairs with it); EMF+ comment records
  dropped, EMF+ image objects cleaned by their own handlers (`embedded.py`; an
  object split across records is reassembled, cleaned and split again); nested
  metafiles recurse. Bytes after `EOF` dropped.
- WMF: private `MFCOMMENT`s dropped; the embedded EMF ('WMFC') cleaned by the EMF
  rules and re-chunked with its checksum recomputed; placeable header's reserved
  fields zeroed, checksum recomputed; header `Size`/`MaxRecord` recomputed; bytes
  after `META_EOF` dropped.
- Refused, by name (none in the survey): `MULTIFORMATS` comments (may hold EPS
  with its own `%%For`), colour-space records (profile paths), printer escapes,
  other WMF escapes, V5 bitmaps linking or embedding a profile, unknown public
  comments or EMF+ record types.
- Kept, documented: the reference device and DPI -- the coordinates are expressed
  in them, so changing them moves the drawing (residual, A2).

**Safety net on every scrub:** every drawing record (EMF, EMF+ and WMF) is in the
output byte for byte, in order; pictures inside are checked by their own handlers.
Content check: LibreOffice renders before and after.

### 6.1 As built, and what it measured

`src/scrub/formats/metafile/` (`emf.py`, `wmf.py`, one handler, matrix id
`metafile`), reached from Word documents -- and SVG, ZIP, EPUB -- through
`embedded.py`. Measured:

- **The 409 real metafiles**: 32 cleaned, 374 had nothing to remove and come back
  byte for byte, 3 differ only in a corrected record count (they left the end
  record out of it -- a writer's off-by-one, so also a fingerprint). A second clean
  finds nothing in any. The two WMFs carrying their source EMF come back exactly as
  Word wrote them, chunk sizes and checksum included -- the checksum rule (the
  metafile's 16-bit words sum to zero; LibreOffice's own writer uses a different
  one) was inferred from them and is now reproduced. LibreOffice draws all 35 that
  changed identically (the rest are unchanged bytes).
- **Word documents**: of the 784, 685 now clean (678 before W2b) and 99 are refused;
  no refusal is for an EMF or WMF, 3 are for HD Photo. Two documents that used to
  stop at their EMF now stop at an older check (relationships pointing at parts the
  file does not contain). The 7 newly cleaned documents holding EMF/WMF render with
  identical text and pixels.
- **Synthetic corpus** (`tests/scrub/metafile_corpus.py`): every survey locus
  planted; A1@F1 passes for EMF and WMF, and a scrubber that removes nothing fails
  the same check (the control is a test). EMF+ pictures are drawn (`DrawImage`), so
  the render check covers a picture split across continuation records.
- **Deep fuzz**: 9,000 damaged EMF/WMF files; every one ends as a refusal, a
  decline or a clean output -- no exception escapes.

**A measurement finding: LibreOffice Writer's page layout is not deterministic.**
The same `.docx`, converted 6 times, gave 2 different renders -- the whole page
shifted by a pixel or two, with or without a fresh profile. A single before/after
page comparison can therefore mistake noise for a change. The metafile render test
converts the picture alone with Draw (6 of 6 identical); page renders of real
documents now render each side several times and compare the sets. Re-checked
this way, the 4 documents of §5 that differ after the `_GoBack` removal render one
way, 4 times in 4, before and never that way after: limit #69 stands.
