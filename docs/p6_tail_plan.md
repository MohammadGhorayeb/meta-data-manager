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
