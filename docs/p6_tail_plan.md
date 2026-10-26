# Phase 6 Plan — The Long Tail and Folder Mode

TIFF, WebP, GIF, SVG, ZIP and EPUB, then cleaning a whole folder in one run with a
single results sheet. Most of this phase reuses what exists — the TIFF/EXIF writer
(Phase 4), XMP/IPTC/ICC, the ZIP reader and writer and the XML surgery (Phase 3),
and every image handler for what archives and SVGs embed. Built leaves first:
images, then the containers that hold them, then the folder that holds everything.

Same method as every phase: measure first. §0–§3 are the opening survey (M0,
2026-10-07); §4 the decisions it forces; §5 the milestones.

---

## 0. The corpus

One test picture (320×240, a gradient with noise) and its animation, with identity
planted by ExifTool in every place each format allows: artist, copyright, camera
make/model/serial, GPS, capture date, XMP creator and IPTC by-line. Then saved by
every producer available here:

| Format | Producers | Notes |
|---|---|---|
| TIFF | macOS `sips`, Pillow (plain, LZW, multi-page, tagged), libtiff `tiffcp`, ExifTool-tagged | `DocumentName`/`HostComputer` planted with a user path |
| WebP | `cwebp -metadata all` (lossy and lossless), `webpmux`, Pillow, `gif2webp` (animated), ExifTool-tagged | |
| GIF | Pillow (with a comment), `sips`, ExifTool-tagged (comment + XMP), animated | |
| SVG | LibreOffice Draw export; **4,255 SVGs shipped inside installed apps** (public files, scanned in place, copies only for benchmarking) | `rsvg-convert` is the independent renderer |
| ZIP | Info-ZIP `zip`, macOS `ditto` (what Finder's *Compress* runs), Python `zipfile` | a photo carrying the extended attributes a browser download leaves |
| EPUB | LibreOffice export | |

Research artifacts: `~/metadata-research/step2/p6/`. No personal library (Photos,
Books, Downloads) was read; user names in on-disk files are shown as `<user>`.

---

## 1. The leak surface, per format

- **TIFF.** Everything RAW's TIFF walker already knows — IFD0 artist, copyright,
  make/model, dates, software, host computer, ExifIFD serials, the GPS IFD, XMP
  (0x02BC), IPTC (0x83BB), Photoshop resources (0x8649) — plus two tags a plain TIFF
  uses and a camera raw does not: **`DocumentName`** (0x010D) and **`PageName`**
  (0x011D), which tools fill with the **file's original path**. Measured:
  **`sips` converting a JPEG to TIFF copies the EXIF identity across and *invents* an
  IPTC block from it** (By-line from Artist, CopyrightNotice, DateCreated) — a format
  conversion that duplicates metadata into a second locus. Pillow and `tiffcp` write
  nothing of their own; `sips` embeds a standard sRGB profile (content, not identity).
- **WebP.** A RIFF of chunks: `EXIF`, `XMP `, `ICCP` beside `VP8 `/`VP8L`/`ANMF`, with
  presence flags in `VP8X`. `cwebp -metadata all` carries EXIF (GPS included) and XMP
  from its source and drops IPTC; Pillow carries EXIF only. Chunks hold no absolute
  offsets, so removal is chunk surgery plus the flags and the RIFF size.
- **GIF.** Comment extensions (`0xFE`) and application extensions — XMP
  (`XMP DataXMP`), ICC (`ICCRGBG1012`) — between the frames. `NETSCAPE2.0` is the loop
  count: content.
- **SVG** (4,255 app-shipped files): **214 carry Inkscape's editor attributes** — the
  document's file name (`sodipodi:docname`), the exact Inkscape version and build
  date; **3 carry an absolute path naming a user's home folder**
  (`inkscape:export-filename="/home/<user>/Desktop/…png"`); Illustrator and Sketch
  leave generator comments (`Generator: Adobe Illustrator 21.1.0`, `Sketch 47.1
  (45422)`); 218 carry `<metadata>` blocks; **12 embed PNG or JPEG pictures as base64**,
  each with its own metadata.
- **ZIP.** Per entry: a DOS time (local time of the machine — a time zone), Info-ZIP's
  extended timestamps and **the creating account's numeric Unix user and group ID on
  every entry** (`ux`: uid 502, gid 20 here), file modes, an archive comment. Finder's
  *Compress* adds an **`__MACOSX/._<name>` sidecar per file holding its extended
  attributes** — measured: **the URL the file was downloaded from**
  (`kMDItemWhereFroms`), **the browser that downloaded it and the quarantine event's
  ID** (`com.apple.quarantine`), and macOS's provenance record — compressed, so
  nothing shows to a byte search or a listing. And every member is a file of its own
  with its own metadata (the photo's GPS).
- **EPUB** (LibreOffice export): a **random per-export ID** (`dc:identifier`, a UUID
  that links copies of one book to one export), the export time (`dcterms:modified`),
  and a generator line naming **the exact LibreOffice build, the platform
  (`MacOSX_AARCH64`) and the source commit**; the ZIP layer's entry times (written in
  UTC); real books add creator, contributor, publisher, rights, dates, and
  e-book-tool fields (calibre's). Mandatory by the spec: title, language, identifier
  and (EPUB 3) the modified time.

---

## 2. What the standard tools do (MAT2 0.14.0, ExifTool 13)

| Input | MAT2 | `exiftool -all=` |
|---|---|---|
| TIFF (tagged) | cleaned, pixels identical | cleaned **except `DocumentName`: the original absolute path stays**, on both samples |
| WebP (lossy, animated) | **no output: "something went wrong", exit status 0** (this Homebrew install's image backend has no WebP loader) | cleaned, pixels and frames identical |
| GIF (still, animated) | cleaned, frames identical | cleaned, frames identical |
| SVG from LibreOffice, embedding a photo | **an empty picture**: 207 bytes, renders 0 visible pixels (original: 34,992), page size changed | does not write SVG; and **reports no GPS**, because it does not read the embedded photo |
| 14 app-shipped SVGs (Inkscape, Sketch) | **renders differently in 14 of 14**; page size changed in 10; 1 renders empty | — |
| ZIP from Info-ZIP / Python | cleaned well: times set to 1980-01-01, extras and comment gone, the member photo's EXIF removed | does not write ZIP |
| ZIP from Finder's *Compress*, holding a JPEG | **crashes**: parses the `._photo.jpg` sidecar as a JPEG by its name, exits 1, and **leaves an empty 22-byte `.cleaned.zip`** — with `--unknown-members omit` too | — |

The useful contrast with Phase 5: MAT2 claims all six formats, so this phase has a
real benchmark — and in four of the six, a measured way it fails.

---

## 3. Findings worth stating on their own

1. **An SVG exported from LibreOffice carries the original photo byte for byte** —
   GPS, camera serial, artist, XMP and IPTC — while ExifTool, reading the SVG,
   reports no GPS at all. The stale-copy lesson of video (M7) in a vector file.
2. **MAT2 does not preserve SVG content**: it re-renders through its graphics stack,
   which changed the rendering of every app-shipped SVG tried and emptied two.
3. **A ZIP made with Finder's *Compress* says where its files were downloaded from**,
   in sidecars nobody sees — and MAT2 cannot clean one that holds a JPEG.
4. **`exiftool -all=` leaves a TIFF's original file path** (`DocumentName`).
5. **Converting a JPEG to TIFF with `sips` duplicates its identity into a new IPTC
   block.**
6. **Info-ZIP stamps the creating account's Unix user ID on every entry.**
7. **LibreOffice's EPUB names its own build, platform and source commit, and gives
   every export a fresh random ID.**

---

## 4. Design decisions the survey forces

- **D1. TIFF is in place, like RAW.** Strip and tile offsets are absolute; the RAW
  machinery already blanks and removes in place without moving a byte. The
  generic part of `formats/raw/f1.py` (`clean_tiff` and its tag tables) moves to a
  shared module both handlers call — written once, as the plan requires — and plain
  TIFF adds what a raw does not need kept: Make/Model (no decoder needs them in a
  plain TIFF), `DocumentName`, `PageName`. The ICC profile stays (colour) -- unlike
  JPEG F1, which drops it on an sRGB assumption a TIFF often breaks; a published
  colour space's profile stays byte for byte, any other is sanitized (§6.1).
- **D2. WebP and GIF are rebuilt by block.** Drop `EXIF`/`XMP ` (WebP) and comment and
  XMP extensions (GIF); keep image data, animation control, the loop count and ICC;
  fix WebP's flags and RIFF size. Nothing in either holds an absolute offset.
- **D3. SVG is XML surgery** (the DOCX machinery): remove `<metadata>`, comments,
  editor namespaces (`inkscape:`, `sodipodi:`, `sketch:`, Illustrator's `i:`/`x:`) and
  their elements; **recurse into every base64 image** through the JPEG/PNG/GIF/WebP
  handlers; report external `file:` references (content the renderer reads). Content
  preservation is a render comparison through `rsvg-convert` — the check MAT2 fails.
- **D4. ZIP is rebuilt through our own writer**: one canonical timestamp, no extra
  fields, no comment; **`__MACOSX/` and `._*` sidecars and `.DS_Store`/`Thumbs.db`
  dropped** (they hold Mac/Windows file attributes and folder views, never the files);
  each member scrubbed by its own handler. A member no handler claims is **refused**
  by default, naming it (fail closed, as everywhere), with a flag to keep such members
  byte for byte — the choice MAT2 offers, made explicit.
- **D5. EPUB is ZIP plus the OCF rules** (`mimetype` first and stored) and OPF
  surgery: the derived identifier (a UUID from a hash of the content, D2 of Phase 5
  again), a canonical modified time, the generator removed; creator, contributor,
  publisher, rights and dates removed — a book's visible author belongs on its title
  page, which is content; title and language stay (required, and they are the book).
- **D6. Folder mode** walks a directory, writes each cleaned file to a mirrored path,
  never stops on one bad file, and writes **one results sheet** — one row per file:
  cleaned / refused (with the reason) / not supported, what was removed, what stayed
  and why.

---

## 5. Milestones

| Milestone | Scope |
|---|---|
| **M1** — TIFF F1 | shared TIFF cleaning (from RAW), `DocumentName`/`PageName`, multi-page, tiles; matrix `tiff` |
| **M2** — WebP F1 | chunk rebuild, lossy/lossless/animated; matrix `webp` |
| **M3** — GIF F1 | extension surgery, animation kept; matrix `gif` |
| **M4** — SVG F1 | XML surgery, embedded-image recursion, render check; matrix `svg` |
| **M5** — ZIP F1 | rebuild, sidecars dropped, member recursion, unknown members; matrix `zip` |
| **M6** — EPUB F1 | OCF + OPF; matrix (as `zip`'s second family) |
| **M7** — folder mode | directory in, directory + results sheet out |

Each format publishes its matrix as it lands (A1 across planted-value variants, A2
across the producers in §0), so the evidence gate covers it from the first push.

---

## 6. M1 as built — plain TIFF

`formats/tiff/` — `clean.py` (shared with RAW: the tag tables, embedded-JPEG
cleaning, GPS dropping, the image-region check, moved out of `raw/f1.py`; RAW keeps
aliases and all 114 RAW tests pass unchanged), `f1.py`, `handler.py` (registered
after RAW, which claims only files that say they are raws).

Blanked in every IFD: the shared dates/software/host/description/comment set plus
Make, Model, DocumentName, PageName, Artist, Copyright, the XP fields and the EXIF
owner/serial/lens/editor fields; removed in place: XMP, IPTC, Photoshop, the maker
note; the GPS IFD dropped; the EXIF thumbnail cleaned within its extent; every page
of a multi-page file walked; image data checked byte-identical on every scrub.

### 6.1 The colour profile, decided by measurement rather than by analogy

The plan said "keep ICC, as in JPEG". JPEG F1 actually **drops** ICC (an sRGB
assumption, `formats/jpeg/f1.py`), and a TIFF is often the file that breaks that
assumption. So the profile stays — but the first run sanitized the header of
`sips`'s plain sRGB profile, which would have turned the commonest bytes in imaging
into a mark of this tool on every file. `icc.description()` and `icc.is_standard()`
now tell a published colour space (matched anchored at the start of the profile's
description: all 14 macOS system profiles recognized) from anything else; a standard
profile stays byte for byte, any other (a display calibrated at home) is sanitized —
header provenance zeroed, ID recomputed, colour tables untouched.

### 6.2 Found by the corpus

A truncated TIFF was **accepted**: image data past the end of the file compared two
equally short slices and passed. Plain TIFF now refuses it. RAW keeps the tolerant
reading — a real Panasonic RW2 declares more sensor data than it holds, and LibRaw
decodes it — so the bounds check is an option, on for TIFF only.

**Measured:** the 8 survey TIFFs (sips, Pillow, libtiff, ExifTool-tagged, multi-page)
come out with every page pixel-identical, no residuals, every planted value gone;
plain `sips` output is untouched (nothing in it was personal). **Matrix:** A1@F1
pass in both byte orders; A2@F1 fail across Pillow, Pillow-LZW, `tiffcp` and `sips`
on byte order, compression, IFD chain, tag sets, strip layout, ICC and size (on
Linux, without `sips`: compression, tag sets, size — same verdict, measured); the
fingerprint guard passes with one declared mark, the zero fill. Limit #56 (BigTIFF
refused).

---

## 7. M2 as built — WebP

`formats/webp/` — the RIFF rebuilt from an ALLOWLIST of image chunks (`VP8X`, `ICCP`,
`ANIM`, `ANMF`, `ALPH`, `VP8 `, `VP8L`), applied inside every animation frame too; the
EXIF/XMP flags cleared; the RIFF size recomputed; bytes after the RIFF's end dropped;
ICC by TIFF's rule. Checked on every scrub: the coded image — every frame and alpha
plane — is in the output byte for byte, in order. `RIFF` is shared with WAV and AVI,
so the handler claims only the `WEBP` form.

**Measured:** the 7 survey files (cwebp lossy/lossless/plain, webpmux, Pillow,
gif2webp animated, ExifTool-tagged) come out frame-identical with no residuals;
libwebp's own `dwebp` decodes the result and `webpmux` still reads three frames and
the loop. **Matrix:** A1@F1 pass, still and animated; A2@F1 fails **only on file
size** across Pillow at effort 0 and 6 and `cwebp -lossless` (pixels asserted
identical): the container is the same, and the encoder shows only in the bitstream
F1 copies. Guard passes with nothing declared.

---

## 8. M3 as built — GIF

`formats/gif/` — the block stream rebuilt from an allowlist: images, graphic control
extensions, plain-text extensions (rendered text: content) and the loop extensions
kept; an ICC extension kept and sanitized by the shared rule, rewritten around the
same sub-block sizes; comments, XMP (with its 258-byte "magic trailer"), every other
application or unknown extension, and bytes after the trailer dropped. The header's
version is left as it is. Every scrub checks the images, timing and text blocks are
in the output byte for byte, in order.

**Measured:** the 6 survey files (Pillow with a comment, `sips`, ExifTool-tagged,
animated) come out frame-identical with no residuals; the loop and frame timing
survive. **Matrix:** A1@F1 pass, still and animated; A2@F1 fails across Pillow,
Pillow-optimised and `sips` (and `gifsicle` on CI) on the block layout, each image's
encoding settings, the screen descriptor and size, with decoded colours asserted
identical — on Linux (Pillow ×2 + `gifsicle`, no `sips`) on the screen descriptor's
flags alone; same verdict, measured in an x86-64 container. Guard passes with
nothing declared.

---

## 9. Found during M4

### 9.1 A rotated photo came out sideways — in all three JPEG tiers (fixed)

Checking how an SVG's embedded photo would look after cleaning raised the question
nothing in the project had asked: what happens to EXIF **Orientation**? A phone held
upright stores its picture sideways and writes Orientation = 6; every browser and
photo viewer rotates it for display. Measured on a 64×32 test photo tagged 6
(displayed 32×64):

| | displayed as |
|---|---|
| original | 32×64 (upright) |
| our JPEG F1, F2, F3 | **64×32 — sideways** |
| `exiftool -all=` | 64×32 — sideways |
| MAT2 0.14.0 | 32×64 (it re-encodes with the rotation applied) |

All three tiers dropped the whole EXIF block, Orientation with it, breaking hard
constraint 1 ("perceptually identical") for most portrait phone photos. The tests had
not seen it because they compare decoded pixel arrays, and Pillow decodes the stored
pixels without applying the tag.

Fixed (`formats/jpeg/orientation.py`): F1 and F2, which may not touch the pixels,
keep the orientation as **one canonical EXIF segment holding nothing else** — the
same bytes for every producer, so it says how to display the picture and nothing
about who made it — inserted only when the value is not 1, and accepted by their
residual checks only in that exact form. F3 applies the rotation to the pixels and
keeps no tag, as MAT2 does, and its perceptual check now compares against the
picture as displayed. `test_jpeg_orientation.py` checks all eight orientations at
all three tiers by what a viewer shows (`ImageOps.exif_transpose`), and that two
rotated photos differing only in metadata still come out identical. The segments are
declared to the fingerprint guard (generated from the code).

### 9.2 M4 as built — SVG

`formats/svg/` and the shared `embedded.py` (scrub a contained file by its own
format's handler, identified by content, refused if no handler claims it — written
once for SVG, ZIP and EPUB). F1 only deletes, through the DOCX surgery: comments;
processing instructions except the XML declaration and `xml-stylesheet`;
`<metadata>`; every element, attribute and declaration in an editor's namespace —
resolved from the declarations (expat), not trusted from the prefix, because
Illustrator binds its prefixes through DOCTYPE entities, which go too once unused;
a `<title>`/`<desc>` that only names the program. It rewrites one thing: each
`data:` picture is decoded, scrubbed by its own handler (nested SVGs recursively, up
to three deep) and re-encoded. LibreOffice's `ooo:` namespace is never touched (its
presentation script reads it). Entities only as plain strings; external, parameter
and nested entities refused. `.svgz` is rewritten without the gzip header's original
file name and time. The prolog's empty lines are tidied (a parser reports no text
there); inside the root, whitespace is reported text and stays.

**The safety net**, on every scrub: input and output, read by a real XML parser,
must give the same stream of elements, attributes and text once the rules' removals
and the embedded payloads are set aside — so nothing that draws can change unnoticed.

**Measured on the 4,255 SVGs shipped inside installed apps** (read in place, scrubbed
in memory): 4,253 scrubbed, **every one librsvg can render (4,252) renders
pixel-identically**, no residuals, 3,976 needed no change at all; 2 refused as
malformed XML. The first run refused two more because the default namespace was
re-bound (XHTML inside `<foreignObject>`) — a check too strict, relaxed to refuse
only a named prefix bound both to an editor's namespace and to another. The
LibreOffice export's embedded photo comes out without its EXIF and still renders.

**Matrix:** A1@F1 pass in four producer shapes, embedded pictures included; A2@F1
fails on namespaces, vocabulary, root attributes, the XML declaration and size
(peer: cairo, real, and three corpus models — said so in the cell). The
fingerprint guard needed two changes: the corpus got a seed (identical drawings and
photos were common to every output), and plugins can now give the guard a view of
their own layer (`guard_view`) — base64 re-aligns every byte after a removed segment
into different text, so SVG blanks its payloads there; the pictures are guarded in
their own matrices. Declared: the newline a removal leaves inside the root.

---

## 10. M5 as built — ZIP

`formats/zip/` (F1, `appledouble.py` to read sidecars for the report) and two
extensions of the Phase 3 ZIP layer, both written so DOCX output stays byte for byte
what it was (checked on the synthetic and the real Word samples before and after):
the reader keeps each name's stored bytes and bounds inflation by the declared size
(a member that inflates past it is refused after one byte too many, not after the
memory is gone); the writer gains `write_archive`, the same serialiser with
directory entries, bit 11 and four modes. Registered after DOCX: Word packages go
there, every other `PK` file here.

### 10.1 The canonical archive, decided by measurement

Five producers measured on one folder (an executable script, an empty folder, an
empty file, a non-ASCII name); Python is `zipfile` and `shutil.make_archive`:

| field | Info-ZIP 3.0 | `ditto` | libarchive | Python | MAT2 | **ours** |
|---|---|---|---|---|---|---|
| create system / made by | 3 / 30 | 3 / 21 | 3 / 20 (10 stored) | 3 / 20 | 3 / 20 | **3 / 20** |
| version needed | 10 stored, 20 deflate | same | same | 20 | 20 | **20** |
| extra fields | `UT` + `ux` (uid, gid) | `UX` (times; uid, gid in the local copy) | `ux` + `UT` | none | none | **none** |
| file modes | from disk | from disk + `0x4000` | from disk | from disk | **0600, no type bits** | **0644 / 0755** |
| folder entries | every folder | every folder | every folder | every folder | **none** | **empty folders only** |
| empty / incompressible | stored | stored | stored | deflated (2 bytes) | as input | **stored** |
| non-ASCII name | UTF-8, **no bit 11** | UTF-8, no bit 11 | UTF-8, no bit 11 | bit 11 | bit 11 | **bit 11** |
| internal attr | 1 on text | 0 | 0 | 0 | 0 | **0** |

The Unix crowd is unanimous on create system 3, so modes are written and mean
something; every mode is one of four (file, executable, folder, link), so the
operator's umask is gone and a script still runs. The UTF-8 flag splits the crowd
three to two; the spec and Python (and so MAT2) set it, and with it every reader
decodes the name the same way. One side effect, measured: libarchive on macOS
extracts a flagged name decomposed (NFD) where it kept an unflagged one composed —
the same visible name, and what it already does with every archive Python writes.
Members are sorted by name; a folder its files imply gets no entry.

### 10.2 Members

Sidecars are decided by their bytes, not their names: AppleDouble (`._*`, files
under `__MACOSX/`), `.DS_Store` (`Bud1`), `Thumbs.db` (OLE). A file named `._notes`
that is not AppleDouble is a file someone named so, and stays. Every other member
is resolved by content through the dispatcher and scrubbed by its handler's F1
(`embedded.py`); a nested archive by this module again, to a depth of four. Plain
text (UTF-8, no control characters but tab, newlines, form feed) is kept: it has
no container to separate metadata from — limit #58. An XMP packet saved as a file
is the exception, metadata by definition, and counts as unknown. Unknown members
are all named in one refusal, or kept byte for byte with `--keep-unknown-members`
(`kept()` lists them). Symbolic links stay links; an absolute target is advised.
Packages that are ZIPs — OOXML other than Word, OpenDocument/EPUB (`mimetype`
first), Java, Android — are refused by name.

**Memory**: decompressing is where an archive can outgrow the machine, so before any
member is inflated the declared sizes are checked against free memory
(`MEMORY_FACTOR` = 3 × (archive + declared), measured 2.5 on macOS and held by a
test); `--skip-memory-check` reaches it through the dispatcher.

### 10.3 Measured

- **E-ZIP (A2)**: one folder through Info-ZIP `zip -r`, `shutil.make_archive`,
  libarchive `bsdtar` and `ditto`, content identical (asserted). Raw, 11 container
  keys separate them (versions, flags, extras, attributes, order, folder entries,
  methods, sidecars, size, ExifTool's required version); after F1, none — the four
  outputs are **one file, byte for byte** (a test holds it). Linux runs the same
  without `ditto`.
- **A1** pass in four producer shapes (Info-ZIP, `ditto`, Python, Windows Explorer):
  comments, local times, Unix and NTFS times, uid/gid, umask modes, the sidecars'
  download URL, referrer and quarantine record, `.DS_Store`, `Thumbs.db`, and every
  member's own metadata down to a nested archive's.
- **The survey archives**: Info-ZIP's, Python's and Finder's all scrub clean; from
  Finder's, the download URL and quarantine record go with the sidecars (MAT2 crashed
  on it and left an empty archive, §2).
- **MAT2 0.14.0 on the probe folder**: refuses an archive holding a shell script
  (`application/x-sh` "isn't supported", exit 255, nothing written). With
  `--unknown-members keep`, the script loses its executable bit, the empty folder
  is gone, and every file is written `0600` with no file-type bits.
- **Guard**: the corpus seed now changes every member's size and the top folder's
  name length (equal sizes made equal offsets, which the guard rightly could not
  tell from a mark); the view blanks member data (guarded in their own matrices);
  the header constants are declared. A test plants a comment on every output and
  sees the guard fail.

**Matrix:** A1@F1 pass, A2@F1 pass, fingerprint guard pass; F2/F3 not built (an
archive's F2/F3 would be its members' own, applied inside it).

---

## 11. M6 as built — EPUB

`formats/epub/` (`ocf.py`: container, font obfuscation, the derived identifier;
`f1.py`) on top of ZIP M5, and a shared span reader, `ooxml/xmlspans.py`: expat
says which element is which by **resolved namespace** (one producer writes
`<dc:creator>`, another declares Dublin Core as the default namespace), and gives
each element's byte span, so every edit is still a cut or an in-place value. (Expat
reports an empty element's end *after* its tag but a normal element's at its `</`;
the self-closing check is made on the start tag's bytes.) Registered after DOCX and
before ZIP; ZIP now refuses any OCF package (a `mimetype` anywhere, or a
`META-INF/container.xml`), so an EPUB never falls through to a plain-archive clean.

### 11.1 Survey (one document, LibreOffice 26.2 and pandoc 3.12)

- Both producers pack the source photo **byte for byte, GPS included**. ExifTool
  reads the package document's fields but reports nothing from the photo, and
  cannot write EPUB.
- pandoc prints the author, publisher, date and rights on a generated title page,
  as well as in the package document.
- **MAT2 0.14.0 produces invalid books.** By the W3C's epubcheck 5.4: pandoc's book
  is valid (0 errors) and MAT2's copy has 13. It empties `dc:title` and
  `dc:language`, removes the `dcterms:modified` EPUB 3 requires, gives the
  identifier a new `id` the package's `unique-identifier` no longer points to,
  empties every chapter's `<title>` and the NCX's `docTitle`, and leaves NCX
  `<meta/>` elements with no attributes. LibreOffice's book goes from 1 error
  (its own CSS) to 7. MAT2's identifier is random per run.

### 11.2 Rules

OCF: `mimetype` first and stored (wherever it was); the rest sorted; `META-INF`
keeps `container.xml`, `encryption.xml` and Apple's display options, drops anything
else (calibre's bookmarks); a file the manifest does not list is dropped (no reading
system shows it); rights, signatures, DRM and multiple renditions refused. OPF: in
every `metadata` block (OPF 2.0's `dc-metadata`/`x-metadata` wrappers too), keep the
title, the language and the unique identifier; keep `dcterms:modified` (set to the
ZIP epoch, 1980-01-01T00:00:00Z), rendition, media-overlay, accessibility and
Apple's specified-fonts properties, the EPUB 2 `cover` pointer, and a title's
type/display order; drop everything else (people, dates, tools, calibre's columns,
other identifiers, `link`s), and `opf:scheme` from the kept identifier. The
identifier becomes a UUID (v8) from SHA-256 over the cleaned members, computed with
a placeholder in OPF and NCX and then written in; the NCX's `dtb:uid` follows.
NCX: `docAuthor` and head metas other than the four readers use go. XHTML: `<meta>`
with a name or property (except `viewport`) and author/schema `link`s go, inline
`data:` pictures are cleaned (SVG's cleaner, now shared), inline SVG `metadata`
goes; whitespace is tidied only in the head, never in mixed content. Comments and
PIs (except the declaration and `xml-stylesheet`) go everywhere.

**Font obfuscation** is keyed by the identifier (IDPF: SHA-1 of it over the first
1040 bytes; Adobe: its UUID over the first 1024), so every obfuscated font is
unscrambled with the old identifier, checked to be a font, and scrambled again with
the new one; tests unscramble the output with the output's identifier and get the
original font back.

**The residual check is the clean itself**: a cleaned book must be a fixed point —
the OPF, NCX, every chapter and the kept `META-INF` files come back unchanged from a
second clean — plus the ZIP checks and `mimetype` first and stored.

### 11.3 Measured

- **Validity**: epubcheck finds no error in our output that the input did not have,
  on both survey books and all five corpus shapes (tests run it when installed;
  CI installs the release, checksum-pinned).
- **A1** pass in five shapes (LibreOffice, pandoc, Calibre EPUB 2, Sigil with IDPF
  font obfuscation, InDesign with Adobe's).
- **E-EPUB (A2)**: one Word document (title and language in its properties, so every
  producer reads the same metadata) through LibreOffice and pandoc (EPUB 3, EPUB 2).
  Raw, all three channels separate them. After F1 the **packager channel is
  closed**; the **package metadata** channel keeps its layout — element order and
  set, the identifier's `id` name, the NCX head, the package's attributes (F2's
  spec); the **content model** (file layout, chapter heads, class names, stylesheet,
  size; the derived identifier, a hash of it) is untouched by design. Two packager
  keys were first measuring the wrong thing — `entry_order` and `methods` list
  member names, so after a sorting writer they report the content layout; replaced
  by policies (is the order sorted; is a member stored exactly when deflate would
  not shrink it), the DOCX M11 lesson again.

**Matrix:** published as its own (`epub`), not as ZIP's second family as §5
planned: its A2 channels are its own. A1@F1 pass, A2@F1 fail (channels named),
guard pass; F2/F3 not built.

### 11.4 Found by CI: the ZIP guard on Linux

The push of M5 failed one test on both Linux legs: the ZIP fingerprint guard,
which passes on the Mac. Reproduced in an x86-64 container: the signature was
`ff000000 7b010000` -- a compressed size of 255 and a size of 379, the nested
archive's, in all three "diverse" outputs. The corpus varied its seeds with
repeated characters (`"." * n`) and solid-colour pictures, which deflate shrinks to
nearly the same size whatever `n`; the Mac's zlib happened to leave one of the three
different, Linux's did not. Equal sizes in diverse inputs are equal header bytes in
their outputs, which the guard rightly cannot tell from a mark. Both corpora now
vary seeds with incompressible hex of seed-dependent length (`zip_corpus.filler`)
and seed-dependent pixel noise. That exposed a second edge: a run that ends
partway into a declared header (the next member stored in one output and deflated
in another) is cut only at whole constants, so the header fields are now declared
in their pieces as well as whole -- the same fields, never more. The non-vacuity
tests (a planted comment, a planted generator line) still fail the guard.

---

## 12. M7 as built — folder mode

`src/scrub/folder.py`; the CLI switches to it when the input is a directory. A folder
is treated as the ZIP handler treats an archive -- it is the same thing on a disk --
but each file goes through the full single-file path (dispatch, scrub, residual
check, report, the ExifTool cross-check), so nothing folder mode does is a second
implementation of a format.

**Outcomes**, one row each in `scrub-results.csv` (UTF-8, written as files finish,
so an interrupted run still leaves a sheet): cleaned; kept as text (ZIP's rule,
limit #58); kept unscrubbed (only with `--keep-unknown-members`); not supported;
refused, with the handler's reason; dropped (sidecars, by their bytes:
`.DS_Store`, AppleDouble `._*`, `Thumbs.db`); skipped (links, never followed;
special files); error (anything unexpected -- the run goes on). Only the first three
are written. If ExifTool still reads a value the report says is gone, the file is
deleted and the row says so: the independent reader outranks ours. Exit 0 when
everything but sidecars was written out, 8 when the run finished but not
everything was, 2 for a refused layout.

**Decisions:**

- **The sheet names fields, never values.** The single-file report echoes the
  values it removed (a terminal display); a sheet saved beside cleaned files and
  shared with them would return the author and the GPS position. A test plants
  values and finds none in the sheet or on the terminal.
- **Layouts that could overwrite or recurse are refused before anything is
  written**: an output folder that exists and is not empty, or that is the input,
  inside it, or around it. A top-level input file called `scrub-results.csv` is
  never overwritten (the sheet takes the next free name).
- **Permissions** (`cli.output_mode`, now for single files too): the user's default
  for a new file, plus the executable bits when the input had one. Until now every
  output was written `0600` by `mkstemp`, so a cleaned program did not start until
  it was `chmod`ed -- the executable tests did that by hand. Found while designing
  this milestone.
- **Files are written new.** Measured on macOS: a photo carrying a browser's
  download address (`kMDItemWhereFroms`) and quarantine record comes out with
  neither (tested). It does carry `com.apple.provenance`, which macOS writes on
  every new file of the session -- the same 11 bytes on files made by `touch` and
  `printf`, and not removable with `xattr -d` (limit #64).

**Run on a mixed folder** (photos with EXIF and download attributes, a truncated
JPEG, a PNG, Finder sidecars, an EPUB, a Finder ZIP, a Word file, text, a script, a
program, a binary blob, an empty file, a link, a name with an accent and a space):
7 cleaned, 2 kept as text, 2 dropped, 1 not supported, 1 refused (the truncated
JPEG, with the reason), 1 skipped; the script and the program keep their
executable bit.

---

## 13. Validation: video telemetry

The iPhone keeps position in metadata boxes (Phase 4 M7). Action cameras, drones and
360 cameras keep it as **media** -- samples of a track, timed like frames -- and
Android phones in boxes of their own. None of these were in the MP4 corpus, so the
question was whether F1 leaks them.

**Models** (`tests/scrub/telemetry_corpus.py`), each from the format's public
description and each read back by ExifTool's embedded-data mode (`-ee`), so the
model is real enough to test against: GoPro (a GPMF `gpmd` track with `GPS5`, the
`GoPro SOS` track, a timecode track, `udta/GPMF` with the camera serial and
`udta/FIRM`); a drone's subtitle track (`sbtl`/`tx3g`, in Autel's text format, the
one ExifTool's source documents); Google's camera-motion track (`camm`, GPS
records); Android (`udta/©xyz`, `mdta` model keys, a Samsung `sefd` trailer).

**Measured before any change: no leak, and three refusals.** Android cleaned with
everything gone. GoPro, the drone and the 360 camera were refused whole, because
their timecode, subtitle and camm handlers were not modelled -- the keep-list did
its job, and made those cameras unusable.

**Decisions:**

- `tmcd` and `camm` tracks are **dropped** (with `meta`): a timecode track's one
  sample is the time of day recording started; camm holds orientation, motion and
  GPS; no player displays either. Their `tref` links from the video are pruned.
- Subtitle and caption tracks (`sbtl`, `text`, `subt`, `clcp`) stay **refused** by
  default -- a film's text and a drone's telemetry look the same to a container --
  with a reason naming both, and `--drop-subtitles` drops them (CLI, folder mode).
- The report names vendor `udta` boxes (`FIRM`, `GPMF`) and labels dropped tracks
  by kind (timecode, camera-motion, subtitle, timed metadata).

**After:** every shape cleaned (the drone with the flag); no planted value in any
output; ExifTool `-ee` finds no position; variants collapse to one file. The MP4
matrix's A1 cell now includes the GoPro, camm and Android shapes.

**Real GoPro files** (added after the models): the ten clips GoPro publishes in its
`gpmf-parser` repository -- HERO5, 6 (three variants), 7, 8, the Karma drone, Fusion,
MAX in 360 and hero mode -- downloaded to the research folder (sizes verified against
GitHub's listing, checksums recorded), never into the repo. Every one has exactly
the modelled layout: video, audio, a timecode track, GPMF telemetry and the SOS
track, and a `udta` holding firmware, the lens serial (`LENS`), a camera ID (`CAME`),
a media unique ID (`MUID`), highlight marks (`HMMT`) and a 25 KB GPMF block with the
camera serial (`CASN`). Before §13 all ten would have been refused. Now all ten
clean: no GPMF structure left (`DEVC`, `GPS5`, `GPSU`, `CASN`, `MUID` -- thousands of
GPS records before, none after), only picture and sound tracks (MediaInfo), none of
ExifTool's identity values anywhere in the bytes, ExifTool's cross-check clean, and
the decoded audio and video identical (ffmpeg's decoded-stream MD5).

**Found by them:** one string survived -- `GoPro AVC encoder`, the video sample
entry's 32-byte `compressorname`, a label no decoder reads. It names the camera's
maker, which F1 removes everywhere else (EXIF `Make`), so it is now blanked in place
in every kept video track, checked by the residual pass and listed in the report.
The GoPro model gained the field; a local test runs the ten clips when they are
present (11 tests fail without the change).

**Still not measured:** DJI and Android clips (they need devices), and other GoPro
firmware than these ten.

---

## 14. Validation pass: deep fuzzing, second readers, real files, a real renderer

The corpora plant values where we already know to look, the checks mostly use one
independent reader (ExifTool, which has been measured missing things), and the pixel
tests use a decoder that ignores how a picture is displayed. This pass attacked each.

### 14.1 Deep fuzzing

HEIC, ZIP and EPUB joined the suite's fuzz corpus, and a one-off campaign ran 3 seeds
x 1,000 mutations on ZIP (two shapes), EPUB (two), HEIC and the telemetry MP4s:
**22 escapes in ~21,000 files** -- raw `struct.error`/`IndexError` from `zipread.read`
on a cut end record (reached while *identifying* the file) and from HEIC records cut
short. Fail-closed held (nothing is written on any exception) but the exit code was
"unexpected" instead of "parse error". Converted at the boundary, as RAW and TIFF
already did; three replayed mutations and three cut end records are regression tests
(they fail without the fix). Re-run: zero escapes. Eight damaged HEICs whose metadata
item lost its type marker come out of F1 with an orphan item the residual check
catches -- the CLI refuses them (exit 6): defence in depth working.

### 14.2 Second readers

`tests/scrub/test_second_reader.py`: exiv2 (C++, no code shared with ExifTool) for
JPEG, TIFF, WebP, HEIC and two raws; MediaInfo for the telemetry shapes; qpdf
rewriting the PDF with every stream decompressed (`--qdf`) and checking its structure.
Every case asserts the reader **sees** the planted values first, then sees none after
F1. All pass. CI installs the three (Ubuntu's exiv2 0.27 lacks BMFF: its HEIC and CR3
cases skip there, a missing reader rather than a pass).

### 14.3 Real files (`scripts/real_file_sweep.py`)

Up to 150 files per type from `/System/Library` and `/Applications` bundles --
vendor assets, never a user's files -- cleaned in memory; content judged by the
DISPLAYED pixels (EXIF rotation applied), page count and text for PDFs; ExifTool's
identity tags compared by VALUE (TIFF blanks fields in place and MP4 zeroes dates,
so counting tag presence reported 135 TIFFs and 148 M4As as leaking when none was).
Aggregates only. Final run:

| type | sampled | cleaned | content |
|---|---|---|---|
| JPEG | 150 | 150 | 150 display-identical |
| PNG | 150 | 150 | 150 display-identical |
| TIFF | 150 | 150 | 150 display-identical |
| GIF | 113 | 108 | 108 display-identical (1 truncated, refused; 4 are not GIFs) |
| WebP | 3 | 3 | 3 display-identical |
| HEIC | 150 | 149 | 149 display-identical (1 refused by the residual check: macOS's `Sonoma.heic` dynamic wallpaper, 37 extra pictures -- limit #67; 1,740 of all 1,741 vendor HEICs clean) |
| PDF | 150 | 147 | 147 same pages and text (3 hybrid-xref, refused) |
| MP3 / M4A / video | 119 / 150 / 150 | 119 / 149 / 143 | no identity value left; 7 videos refused for caption tracks |
| ZIP | 41 | 30 | 11 refused for members no handler reads |

Re-run 2026-10-26 after the BMP handler ([`bmp_plan.md`](bmp_plan.md)): ZIP 41 → **32
cleaned, 9 refused**, every one for a `.metallib` member (GPU code, out of scope); the
two that held Office's 55 pattern-fill BMPs clean. The sweep now samples BMP too (1
found, display identical).

**Found, and fixed:**

1. **HEIC F1 dropped transparency.** The alpha plane is an auxiliary image
   (`urn:mpeg:hevc:2015:auxid:1`) and F1 matched aux types by the URN's last
   component -- `1` -- so it went with the depth maps: 16 of 150 vendor HEICs came out
   opaque. Alpha is now kept by its full standard URNs (HEVC, AVC, MPEG-B); the
   depth plane (`auxid:2`) still goes. Limit #32 updated.
2. **The ISOBMFF pointer check refused a correct M4A.** It compared 64 bytes at each
   chunk offset before and after; a last chunk of 30 bytes in a file laid out `mdat`
   first read on into `moov`, which F1 changes. Clamped to the media's extent: all
   1,414 vendor M4As now clean except 6 that are not audio files with media.
3. **Display profiles kept their identity.** TIFFs and PDFs embed the profile of the
   display they were made on -- "Apple Cinema Display", a Mac's built-in "Display" --
   with Apple's MakeAndModel tag (`mmod`: maker, model, and a per-unit number that
   differs between two displays of one model); a Mac tags screenshots with it.
   `icc.sanitize` zeroed only the header. Every profile that is not a published
   colour space now loses `desc`, `dscm`, `cprt`, `dmnd`, `dmdd`, `mmod`, `calt`,
   `targ`, `pseq` and `psid` (blanked in place, counts and sizes kept), and a profile
   holding a device tag is never "standard" whatever it is called. Colour is
   unchanged by two renderers on eight real display profiles: LittleCMS converting
   through either profile, and Quick Look drawing the cleaned TIFFs. Limit #14
   narrowed. One function, so PDF, TIFF, GIF, WebP and HEIC all get it.

### 14.4 A real renderer

`tests/scrub/test_quicklook.py` renders pictures with Quick Look -- what Finder and
Preview draw with, rotation, colour profile and transparency applied -- before and
after F1 and requires the same pixels, with a non-vacuity case (the portrait photo
without its rotation tag renders sideways). It runs in the macOS CI job. EPUB and
Word documents have no scriptable reader on this machine, so they remain checked by
validity (epubcheck) and by LibreOffice's render.

### 14.5 Still open

Real files from DJI drones and Android phones (GoPro's ten published clips are done,
§13), Word-made documents, store-bought books, and a **blind test**: someone else plants metadata in files we never see, and judges our output.
Each removes a bias these checks share -- that we chose what to look for.
