# BMP — the last picture type the real-file sweep found refused

The 2026-10-26 re-sweep of vendor archives (`scripts/real_file_sweep.py`, §14.3 of
[`p6_tail_plan.md`](p6_tail_plan.md)) left two kinds of refusal: `.metallib` GPU
code (out of scope) and **BMP**. Office's pattern fills ship as 55 BMPs in
`OAPatterns.zip` — twice on this Mac (Keynote and the system's OfficeImport
framework) — and a ZIP refuses any member no handler reads. A Word document holding
a BMP was refused by name too (limit #68). There was no BMP handler and no plan
mentioned one.

Same method as every phase: measure first (§0–§3), decide from the measurements
(§4), then build (§5) and measure the result (§6).

---

## 0. The corpus

| Source | Files | What they are |
|---|---|---|
| Office (`OAPatterns.zip`, Keynote + OfficeImport) | 2 × 55, byte-identical sets | 8×8 1-bit pattern fills, 40-byte header; one is `patternHeader.bmp` |
| Pillow 12.2.0 | 7 | modes 1, L, P (16 and 256 colours), RGB, RGBA, RGB at 300 dpi |
| ffmpeg 8.1.1 | 8 | `bgr24`, `bgra`, `pal8`, `gray`, `monow`, `rgb555le`, `rgb565le`, `rgb444le` |
| macOS `sips` (26.6.2) | 5 | from a planted JPEG, a PNG, a Display P3 JPEG, grey and RGBA PNGs |
| ImageMagick 6.9.12-98 (Ubuntu 24.04, x86-64 container) | 11 | default, `BMP2:`, `BMP3:`, `bmp4`, 16 colours, RLE, 1-bit, RGBA, from a JPEG, from a Display P3 JPEG |
| LibreOffice Draw 26.2 | 1 | a PNG exported as BMP (it renders the page) |
| A vendor test image (`windows95.bmp`, inside an app's bundled node modules) | 1 | 8-bit 320×400 |
| Planted (`tests/scrub/bmp_corpus.py`) | every layout below | every locus, values derived from a variant at a fixed length |

The sources are the survey picture of [`p6_tail_plan.md`](p6_tail_plan.md) §0 (320×240,
identity planted in its EXIF, XMP and IPTC). Research artifacts:
`~/metadata-research/step2/bmp/` (`vendor_*`, `survey`, `survey_linux`, `planted`,
`bench`). No personal file was read.

---

## 1. What a BMP can carry, measured

A BMP is a 14-byte file header (`BM`, file size, **two reserved 16-bit fields**, the
pixel offset), an info header whose size names its version — 12 (OS/2 1.x / Windows
2 "core"), 40 (Windows 3), 52/56 (Adobe's V2/V3), 64 (OS/2 2.x), 108 (V4), 124 (V5)
— optional colour masks, a palette, the pixel rows, and in V5 a colour profile. It
has no tags. What it has instead is places to put bytes no reader shows:

- **The reserved fields.** Zero in every file measured. Nothing stops a writer.
- **The resolution** (pixels per metre). A setting, not the picture: Pillow writes
  96 dpi (3780) unless told otherwise (300 dpi → 11811), ffmpeg 0, Office 75 dpi
  (2953, 49 files) or 72 dpi (2835, 6 files), LibreOffice 96 dpi (3781). **`sips` and
  ImageMagick copy it from their source**: the planted JPEG (1 dpi) came out at 39 —
  a scanner's or a camera's setting carried into the BMP.
- **The colour counts.** "Colours used" is 0 for a full palette by the format's rule;
  Pillow and Office write the count anyway, and "important colours" (read by
  nothing modern) as the same number. A writer's habit — an A2 key.
- **Gaps.** Bytes between the palette and the pixel offset, and after the pixels.
  None in the files measured; anything could live there.
- **Row padding and spare bits.** Each row is padded to 4 bytes, and a 1- or 4-bit
  row's last byte can have spare bits. Zero in every file measured — a writer that
  leaves uninitialised memory there would leak it.
- **The palette's fourth byte** ("reserved"): zero everywhere measured.
- **A palette in a picture of more than 8 bits** ("for optimisation", which no
  decoder draws with): none measured; allowed by the format.
- **V4/V5 colour space.** `sRGB`, `Win ` (Windows default), calibrated (36 bytes of
  endpoints and 12 of gamma: colour data), **`MBED`** (an ICC profile, after the
  pixels in ImageMagick's files) or **`LINK`** — a NUL-terminated file name, which on
  Windows is `C:\Users\<name>\…`: **a path is metadata**. ImageMagick writes V4/V5
  headers by default, **embeds the source's ICC profile** (Display P3, 536 bytes) and,
  in its 1-bit V4 files, fills the endpoint and gamma fields **while saying `sRGB`**,
  which makes them unused. `sips` drops a source's P3 profile and writes `sRGB`.
- **V5's reserved field**: zero everywhere measured.
- **RLE.** 8-bit (and 4-bit) run-length coding: literal runs are padded to 16 bits
  with a byte no decoder reads, and the image size field can cover bytes after the
  end-of-bitmap code. ImageMagick's RLE8 files are real samples; nothing here writes
  RLE4 (ImageMagick asked for 16-colour RLE wrote RLE8), so RLE4 is measured on
  planted files only.
- **The 32-bit "unused" byte.** The format calls each 32-bit pixel's fourth byte
  unused; Pillow (RGBA) and ffmpeg (`bgra`) store transparency there. Pixel data.

Also found while measuring:

- **`patternHeader.bmp`** is 62 bytes: a file header, an info header and a 2-colour
  palette, **no pixels** — the header Office stamps onto each 32-byte pattern. Its
  sizes describe the bitmap it heads (94 bytes).
- **LibreOffice's file-size field is wrong**: 3,566,702 for a 2,677,286-byte file —
  it counts 32-bit pixels for a 24-bit picture.
- **`sips` writes a headerless file for a palette PNG**: 230,400 bytes of pixels and
  nothing else. Not a BMP; noted, not ours to fix.
- **Pillow 12.2** reads the palette straight after the headers whatever the pixel
  offset says, ignores the colour space and any profile, cannot read 4-4-4 bitfields,
  and **reads an odd-length RLE4 literal one byte short** (`n // 2` bytes), then takes
  its last byte and pad as the next code.
- **macOS ImageIO** (`sips`, Preview, Quick Look) **cannot open any V4/V5 BMP whose
  colour space is `MBED`, `LINK` or `Win `** — with real system profiles too — nor
  a V4 16-bit bitfields one. It opens `sRGB` and calibrated ones.

---

## 2. What the standard tools do (MAT2 0.14.0, ExifTool 13.55)

| Input | MAT2 | `exiftool -all=` |
|---|---|---|
| any BMP (7 tried: Office, Pillow, `sips`, planted V5 with a personal profile, a linked path, gaps and trailing bytes) | **copied byte for byte**, exit 0, `mat2 -s` says "No metadata found": `libmat2/harmless.py` lists `image/bmp` with `text/plain` as types that "can not contain metadata" | **"Can't write BMP files"** |

ExifTool *reads* a BMP fully — every header field, the embedded profile's header and
description, and **`LinkedProfileName: C:\Users\sentinel01\AppData\Local\display.icm`**.
It files that under its `File` group, which this project's report cross-check
treated as derived (for every other BMP field, rightly): the report on a linked-path
BMP said "0 metadata tags before". That one tag is now counted and searched like any
other (`crosscheck.STORED_IN_DERIVED`): "1 metadata tag before → 0 after".

---

## 3. Findings worth stating on their own

1. **MAT2 declares BMP harmless and copies it**, so a BMP naming its owner's profile
   path comes out of MAT2 with the path — reported as clean.
2. **ExifTool can read that path and cannot remove it.**
3. **Format converters carry the source's resolution into the BMP** (`sips`,
   ImageMagick), and ImageMagick carries the source's colour profile.
4. **macOS cannot open a BMP that links or embeds a colour profile.**

---

## 4. Decisions

- **D1. Rebuilt from parts, not edited in place.** Only the pixel offset and V5's
  profile offset point anywhere, so the file is written again: file header, info
  header, masks, palette, pixels, then an embedded profile. Every byte outside those
  parts goes; that is the whole of "gaps and trailing bytes".
- **D2. Inside the parts, every field no decoder draws with is canonical**: reserved
  fields, resolution, important colours and V5's reserved field zero; colours-used 0
  for a full palette; the palette's fourth byte, row padding, spare bits and an RLE
  literal's pad byte zero; V4/V5 endpoints and gamma zero unless the picture says it
  is calibrated (then they are colour data). Sizes and offsets recomputed.
  Resolution goes for the reason PNG F1 drops `pHYs` and JPEG F1 writes 1:1: it is the
  source's setting.
- **D3. What draws the picture is byte for byte**: dimensions, row order, depth,
  compression, masks, colour space, intent, palette colours, every pixel. The header
  version and the row order stay — changing either is an F2 (one layout).
- **D4. Colour profiles by the shared rule** (`icc.is_standard`): a published colour
  space byte for byte (ImageMagick's Display P3 stays identical), anything else
  sanitized — as TIFF, GIF and WebP do.
- **D5. A linked profile becomes `sRGB`**, its path removed. The file it names exists
  only on the machine that saved the picture, so every other machine already showed
  it as sRGB (limit #74). `sRGB` rather than `Win `: ImageIO opens one and not the
  other.
- **D6. Kept because some program reads it** (limit #73): the 32-bit fourth byte, an
  uncompressed V4/V5 header's masks, palette colours no pixel uses, and an odd RLE4
  literal **exactly as written** — zeroing its spare nibble or pad would change what
  Pillow draws, since Pillow reads both as the next code.
- **D7. A header with no pixels is a template, and cleaned**: every byte is
  accounted for (Office's `patternHeader.bmp`). Any other truncation is refused.
- **D8. Fail closed on what was not measured** (limit #72): OS/2 2.x headers, JPEG/PNG/
  CMYK/alpha-bitfield compression, 2- and 64-bit depths, 24-bit bitfields, top-down
  RLE, RLE that draws outside the picture or has no end, a profile overlapping the
  headers or pixels, a `MBED` that is not ICC, a V4 naming a profile, an unknown
  colour space — and **pixels using a colour the palette does not have**, because
  decoders disagree there and some read on into what F1 drops.

---

## 5. As built

`src/scrub/formats/bmp/` — `f1.py` (parse, rebuild, findings, residuals, describe) and
`handler.py` (format id `bmp`, magic `BM`, claimed only when a known header size
follows, so a text file beginning "BM" is not a bitmap). Registered after GIF; ZIP
members and Word pictures reach it through `embedded.py` by content.
`docx/f1.py`'s "no handler" hint no longer names BMP.

Every scrub re-parses its output and checks that what the picture draws is the same
(§4 D3, with spare bits masked) and that the profile is the one the rule chose.
`residuals()` lists every non-canonical field in plain words and, if none, checks the
file is a fixed point of the rebuild.

---

## 6. Measured

- **Real and survey files:** 110 Office pattern fills, 32 producer files (Pillow,
  ffmpeg, `sips`, ImageMagick, LibreOffice), the vendor test image, 23 planted layouts:
  no residual, nothing planted left, the same pixels wherever Pillow can draw the
  input (all but the template and ffmpeg's 4-4-4 bitfields). ImageMagick's Display P3
  profile is byte-identical after. macOS ImageIO draws the same picture before and
  after in every planted layout it can open, and refuses after everything it refused
  before — except a linked-profile BMP, which it opens only once cleaned. Quick Look
  draws the same picture before and after for 10 of 12 planted layouts tried (the
  other two: an embedded profile, which it never draws, and a linked one, which it
  draws only after); three are in `test_quicklook.py`. Quick Look **hangs** on a BMP it
  cannot open, so the test only includes ones it can.
- **Matrix `bmp`:** **A1@F1 pass** in seven layouts (V5 with a personal profile, V5
  linked, V5 with a published profile before the pixels, V4 calibrated, 8-bit with 16
  colours, RLE8, core header) over every locus of §1; a do-nothing scrubber fails the
  same check in every layout, asserted per layout. **A2@F1 fail** (E-BMP, one opaque
  24-bit picture, decoded colours asserted identical, three runs each): on this Mac
  (Pillow, ffmpeg, `sips`) on `top_down`; in an x86-64 Ubuntu 24.04 container (Pillow,
  ImageMagick) on `header`, `offset`, `size`. Raw, the resolution and image-size
  fields separate writers too; F1 closes both. **Pillow and ffmpeg write the same
  bytes once cleaned.** Same verdict on both platforms, different keys; CI installs
  ImageMagick so the Linux peer set separates (without it, Pillow and ffmpeg would
  re-measure the cell as a pass). Fingerprint guard: pass, nothing declared.
- **Fuzz:** 33,000 mutations of 11 files (planted layouts, Office files, ImageMagick's
  P3 and RLE8): nothing but `ScrubError` escapes; every one cleaned (15,019) is a
  fixed point with no residual, and Pillow draws the same pixels for every one it
  could draw before (14,028). Two BMPs are in `test_fuzz.py`'s corpus.
- **Vendor ZIP sweep** (41 archives under `/Applications` and `/System/Library`):
  before, 30 cleaned and 11 refused (9 for one `.metallib` member each, 2 for 55
  BMPs); **after, 32 cleaned and 9 refused — all 9 `.metallib`**. The sweep now
  samples BMP files too: 1 found, cleaned, display identical.
- **Word corpus census** (784 Word-made documents): **0 hold a BMP** — every picture
  member was checked by content — so the census is unchanged, 685 cleaned and 99
  refused, before and after. BMP in a Word document is measured on built documents
  (`word_corpus.pictures` now carries one, with a linked profile).

---

## 7. Open

- **F2: one layout** — one header version and rows bottom-up — closes A2's keys
  (limit #71). Lossless for an `sRGB` or calibrated picture; not built.
- **Real RLE4 and V4/V5-with-profile files from other writers** (GIMP, Photoshop):
  none available here; the rules are measured on ImageMagick's files and planted ones.
- **Other stored values ExifTool files under `File`** — a JPEG's comment is one —
  are still treated as derived by the report's cross-check; only the BMP path was
  moved (its handler's residual check reads the bytes either way).
