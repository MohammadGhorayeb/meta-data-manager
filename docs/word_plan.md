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
| `w:attachedTemplate`, `w:mailMerge`, `w:documentProtection`, `_GoBack` | paths, password hash, cursor | removed | -- | done |
| insertions, deletions, moves, comments | other people's text and names | refused (F1), accepted/dropped (F2) | -- | done |
| **tracked formatting changes** (`w:rPrChange`, `w:pPrChange`, `w:sectPrChange`, `w:tblPrChange`, `w:trPrChange`, `w:tcPrChange`, `w:tblGridChange`, `w:numberingChange`) and table-cell revisions (`w:cellIns`, `w:cellDel`, `w:cellMerge`) | **reviewer's name and the date -- measured surviving F1** | **passes through** | refuse at F1 like other revisions; accept at F2 | **W1** |
| **editing permissions** (`w:permStart w:ed`/`w:edGrp`, `w:permEnd`) | **an editor's e-mail address -- measured surviving F1** | **passes through** | remove the range markers (they draw nothing) | **W1** |
| `w:writeProtection` | a password hash and salt | passes through | remove, like `w:documentProtection` | W1 |
| `w:docVars` | template and document-management variables (client, matter, user) | passes through | remove: no field result changes until fields update | W1 |
| task-pane add-ins (`word/webextensions/`) | which add-ins, their store and settings | passes through | drop the parts | W1 |
| ActiveX controls (`word/activeX/`) | controls with their own binary data | passes through | refuse, as macros are | W1 |
| printer settings (`word/printerSettings/`) | the printer's name and driver settings | passes through | drop | W1 |
| pictures' source names (`pic:cNvPr name`, `wp:docPr descr/title`) | the inserted file's name (a camera counter, a screenshot timestamp) | kept | measure what Word writes, then blank names that are file names | W1 (after measuring) |
| linked pictures (`r:link` to `file:///C:/Users/...`) | a path naming the user | kept, advised | where an embedded copy exists, drop the link | W1 (after measuring) |
| images other than JPEG/PNG (GIF, TIFF, WebP, SVG) | each image's own metadata | **refused** | clean through `embedded.py` by content | **W2** |
| EMF/WMF pictures (pasted charts, Visio, clip art) | a description string (application, file name) | **refused** | survey, then a handler or a documented refusal | W2 |
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
12 tests fail without the change. Still to measure (needs real Word files with
pictures): source file names in `pic:cNvPr`/`wp:docPr`, and linked-picture paths.

**W2 (pictures).** `word/media/` goes through the shared `embedded.py`: each picture
is identified by its content and cleaned by its own handler -- JPEG and PNG exactly as
before (byte-identical, tested), and now GIF, TIFF, WebP and SVG, which were refused
by extension. Two latent defects went with the old rule: a picture was trusted to be
what its extension said, and an extension not on the list (`.wdp`, a renamed file)
passed through unscrubbed; both now go by content, and content no handler claims is
refused. EMF, WMF and HD Photo remain refused, by name (limit #68).
