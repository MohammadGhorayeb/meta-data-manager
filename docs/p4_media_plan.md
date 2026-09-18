# Phase 4 Plan — Media & Camera Containers: HEIC → MP4 → RAW

## Why HEIC first, against the original order

`docs/implementation_plan.md` sequences this phase **MP4 → HEIC → RAW**, reasoning
that HEIC reuses the MP4 atom parser. That reasoning is now stale: the parser already
exists. `standards/isobmff.py` was built for M4A in Phase 2 and it walks a real
iPhone HEIC unmodified — `ftyp`/`meta`/`mdat`, with `meta`'s children resolving
correctly because `meta` is already in `FULL_CONTAINERS`.

The real ordering constraint turns out to be the opposite of what it looks like:

- **MP4** reuses the offset-patching we already have. Its chunk offsets live in
  `stco`/`co64`, and `isobmff.shift_chunk_offsets()` patches exactly those — measured
  on an ffmpeg-produced MP4: two tables, both covered, zero new machinery.
- **HEIC** does *not*. Its item offsets live in `iloc`, with per-file variable field
  widths, so it needs a sibling of that function.

So MP4 is the cheaper step — but doing it first makes HEIC **no cheaper**, because the
two use different offset tables. The sequencing is therefore free, and it is spent on
the format that matters: HEIC is what every iPhone produces, the corpus is real and in
hand, and the parse side is already proven.

---

## 0. Measured ground truth (the opening spike)

Six real iPhone photos (`metadata-research/step2/`, git-ignored — `*.HEIC` is in
`.gitignore` and none has ever entered history). Measured with the existing
`isobmff` walker plus ~30 lines of `iloc`/`iinf` reading.

### The item table

| file | size | items | composition |
|---|--:|--:|---|
| IMG_0502 | 1.17 MB | 105 | 95 `hvc1`, 3 `grid`, 4 `mime`, 1 `tmap`, 1 `uri `, 1 `Exif` |
| IMG_2427 | 1.79 MB | 101 | 93 `hvc1`, 3 `grid`, 2 `mime`, 1 `tmap`, 1 `uri `, 1 `Exif` |
| IMG_0947 | 0.80 MB | 66 | 61 `hvc1`, 2 `grid`, 1 `mime`, 1 `tmap`, 1 `Exif` |
| IMG_2202 | 0.74 MB | 66 | 61 `hvc1`, 2 `grid`, 1 `mime`, 1 `tmap`, 1 `Exif` |
| IMG_1340 | 0.86 MB | 65 | 61 `hvc1`, 2 `grid`, 1 `mime`, 1 `Exif` |
| IMG_3953 | 0.80 MB | 65 | 61 `hvc1`, 2 `grid`, 1 `mime`, 1 `Exif` |

A HEIC is **not one image in a box**. The photo is a *grid* of 61–95 HEVC tiles, and
alongside it sit auxiliary images, metadata items, and a thumbnail — all addressed by
absolute offset from one `iloc` table.

### The leak surface, per locus

| locus | measured | disposition |
|---|---|---|
| `Exif` item | 2.4–3.0 KB, every file. Parsed by our **existing, unmodified** `standards/tiff_ifd` + `tiff_values`: Make `Apple`, Model `iPhone 16`, Software `18.7.8`, `DateTimeOriginal`, GPS to the second of arc | **drop** |
| `mime` items | 1–4 per file, each an XMP packet (`XMP Core 6.0.0`) | **drop** |
| `uri ` item, name `metadata` | **57–58 KB, 3–5% of the whole file.** An Apple binary plist (`bplist00`) | **drop** — see below |
| `thmb` reference | 1 per file: an embedded thumbnail of the photo | **drop** — the Phase 1 lesson, in a new container |
| `auxl` references | **6 auxiliary images** per file: depth maps and semantic mattes | **decide** — see below |
| `hvc1` tiles + `grid` | the photograph itself | **keep** |

### The `uri ` blob is a description of the photo's subject

The 58 KB plist decodes to Apple's semantic-segmentation output. Among its keys:

```
PeopleRatio           0.571        (IMG_0502)   0.0   (IMG_2427)
SkinRatio             0.00248                   0.0
PersonMasksValidHint  -1.0                      0.0
'1': 51840 bytes      — a segmentation matte (51840 = 216 x 240)
ToneMappedImagePersonSegmentBased / LinearImageSkinBased / ...
```

So the file records **whether it contains people and how much of the frame they
occupy**, plus a downscaled map of where they are. That is a different species from
EXIF: not a fact about the camera, but a machine-derived claim about the *subject*,
computed on-device and shipped inside the picture.

**It is not a secret.** ExifTool surfaces it — 65 tags under a `PLIST` group, plus
`XMP-semanticSegmentationMatte` and a QuickTime
`AuxiliaryImageType: urn:com:apple:photo:2019:aux:semanticskinmatte`. The point is not
that it is hidden but that it is *unlike the rest of the threat model*: A1 asks what a
file says about its origin, and this says something about its content. It goes in
`docs/limits.md` when measured, phrased as what it is.

### The open design question: auxiliary images

Six `auxl` items per file are depth maps and skin/person mattes. They are not the
photograph, and dropping them does not change how the photo looks — but they are
*derived content*, and an app may use them (portrait-mode relighting, background
replacement). Constraint #1 says perceptually identical; these do not affect
perception of the image itself.

Recorded as a decision to make with evidence rather than by taste, and the honest
default is to **drop them at F1 and say so loudly**, because a person-segmentation
matte of the subject is exactly the kind of thing a privacy tool should not ship
silently. To be settled in W3.

---

## 1. Work items

### W1 — HEIC walker: the item model (read-only)
`formats/heic/walker.py` over `standards/isobmff.py`. Parse `meta`'s children into an
item model: `pitm` (primary), `iinf`/`infe` (id → type, name), `iloc` (id → extents),
`iref` (`dimg`/`auxl`/`cdsc`/`thmb` links), `iprp`/`ipma` (properties per item).

Refuse rather than guess: `iloc` construction methods other than file-offset (item
and `idat` offsets exist and mean the bytes are somewhere else), ZIP64-style
oversized fields, `meta` versions we do not model, and any item whose extents run
past EOF.

`claims()` must separate HEIC from the other `ftyp` formats already handled — M4A is
`ftyp M4A `, HEIC is `heic`/`heix`/`mif1`, and MP4 is `isom`. Today an `isom` MP4 is
correctly unclaimed, and that must stay true.

### W2 — `iloc` rewriting, the trap this phase turns on
Removing an item shifts every item stored after it. `iloc` holds **absolute** offsets
with per-file field widths (measured: version 1, `offset_size=4`, `length_size=4`,
`base_offset_size=0`). This is the `stco`/`co64` trap from M4A in a second
guise — same failure mode, and the M4A experience says it decodes to garbage while
still parsing, so the test must decode, not just parse.

`shift_chunk_offsets()` does not generalise (it keys on `stco`/`co64`), so this is a
sibling function. Both belong in `standards/isobmff.py`, since MP4 and HEIC will each
want one.

### W3 — HEIC F1: drop the metadata items, rebuild the tables
Drop `Exif`, `mime` (XMP) and the `uri ` plist; drop the `thmb` thumbnail; decide on
`auxl`. Then rebuild `iinf`, `iloc`, `iref` and `ipma` consistently and repack `mdat`.
The acceptance test is not "it parses" but **"it still decodes to the same pixels"** —
the M4A lesson, where a wrong offset table left a file that parsed perfectly and
decoded to noise.

### W4 — `HeicPlugin` + the A2 channel
Structural features: item count and order, `iloc` field widths, brand and compatible
brands, `hvc1` configuration, tile grid geometry, which optional boxes exist. The
producer axis is the *device and iOS version*, which is what a peer corpus of iPhone
photos varies.

### W5 — the corpus problem, and it is a real one
Only `sips` can produce HEIC on this machine, and it is macOS-only. No `heif-enc`, no
`pillow-heif`, and ffmpeg has libx265 but no HEIF muxer. CI is Ubuntu.

Options, to be decided in W5 rather than assumed: install `libheif-examples` (gives
`heif-enc`) on the runner, add `pillow-heif` to the harness requirements, or accept
macOS-only generation and report HEIC as locally-measured — the limit-#12 precedent
that already covers Apple's AAC encoder and Microsoft Word. The scrubber itself must
depend on none of them: we parse ISOBMFF ourselves, and a HEIC decoder is needed only
to *verify* pixels, which is exactly where an independent implementation belongs.

---

## 2. Milestones

| # | Deliverable | Status |
|---|---|---|
| **M0** | Opening spike — the item census above, measured on six real files | ✅ (§0) |
| **M1** | Corpus decision (W5) | ✅ — `pillow-heif`, pip-installable so **CI can build a HEIC**, and an independent **decoder** besides. Real camera photos stay a local-only second corpus |
| **M2** | Walker + `claims()` + refusal list (W1) | ✅ (§3) — brand-based identification; M4A and MP4 correctly declined |
| **M3** | `iloc` rewriting (W2), with a **decode** test, not a parse test | ✅ (§3) |
| **M4** | HEIC F1 (W3) + the auxiliary-image decision | ✅ (§3) — all six real photos scrub pixel-identically; auxiliary images dropped by default with the reasoning recorded |
| **M5** | A hand-built HEIC with a grid, an auxiliary image and a thumbnail (so CI covers the hard paths), then `HeicPlugin` + matrix + the A2 channel (W4) | ✅ (§4) — and building it found three defects M0–M4 could not see |
| **M6** | `limits.md` rows | ✅ — #30 (the segmentation blob), #31 (the auxiliary-image trade), #32 (the HDR gain map kept, and our oracle blind to it), #33 (the container fingerprint A2@F1 leaves), plus the residuals block |

**HEIC is closed at F1.** F2/F3 are not built and their matrix cells say
`not_tested` rather than `fail`: a tier that was never run has no verdict. What an
F2 would have to do is no longer a guess — it is the list of features A2@F1 names.

---

## 3. M1–M4 as built

**Identification.** `....ftyp` is shared by every ISOBMFF file this project handles,
so the brand decides: M4A is `M4A `, an MP4 is `isom`, a HEIC is `heic`/`heix`/`mif1`.
The HEIC handler registers after M4A so the narrower audio claim runs first, and a
test asserts an M4A is still routed to the audio handler.

**Two bugs, both found by decoding rather than parsing.** The first version of the
walker **refused construction method 1** and therefore rejected all six corpus files:
Apple stores the `grid` item — including the *primary* item — with its bytes inside
`idat` rather than at a file offset. Those extents carry no file position, so the
model records them as such and anything that moves bytes skips them.

The second was worse and is the reason this tier's acceptance test decodes. Rebuilding
`iprp` from `Box.children` emitted an **empty `iprp`**, because `iprp` is not in the
shared `CONTAINERS` set and its children live in its payload. The output walked
perfectly and libheif answered `Invalid input: No 'ipco' box`. Fixed inside the HEIC
module rather than by widening the shared set, which is load-bearing for M4A.

**A third was found by the report's own cross-check**, on a real photo: keeping `ipco`
whole left the file still advertising `urn:com:apple:photo:2019:aux:semanticskinmatte`
after the matte was gone. Properties are shared *by index*, so pruning them means
renumbering every surviving association — now done, and the advertisement is gone.

**Measured, six real iPhone photos:** every one scrubs to **pixel-identical** output
through an independent decoder. ExifTool sees **428 metadata tags before, 147 after**
on the richest file; `Make`, `Model`, `Software`, `DateTimeOriginal`, GPS latitude,
longitude, altitude and timestamp are all gone, as are the four XMP packets, the
thumbnail, the six auxiliary images and the 58 KB segmentation plist.

> **Corrected in M5 — read §4 before believing this paragraph.** "The six auxiliary
> images" was **false**: the tiled ones left every tile behind, up to 707 KB of a
> 1.78 MB photo. The count after M5's fix is **428 tags before, 109 after**, one
> auxiliary image is now kept on purpose, and the rest are gone along with their
> tiles. The paragraph is left standing rather than edited because the whole point
> of §4 is that this claim read as a measurement and was not one.

What remains is container structure and the colour profiles, whose headers are
sanitised and whose tag data is not — limit #14, in a third format.

### What CI actually verifies, and what it does not

`pillow-heif` installs on the Linux runner, so the synthetic half runs everywhere:
identification against M4A and MP4, metadata-item removal, pixel survival through an
independent decoder, truncation refusal, idempotence, and the report. **13 tests skip
on CI**, and they are exactly the ones that need a real camera photo — which is to
say they are the ones covering the findings that made this milestone interesting:

- the 61-to-95-tile **grid** and `idat`-relative primary item,
- the six **auxiliary images** and the `thmb` thumbnail,
- Apple's **segmentation blob**,
- the **`ipco` orphan-property pruning** that stopped a scrubbed photo advertising
  `semanticskinmatte`.

A synthetic file built by `pillow-heif` has none of those: one tile, no grid, no
auxiliary images, no thumbnail. So the honest statement is that HEIC's *core* is
regression-tested everywhere and its *hardest paths* are regression-tested only where
real photos exist. That is the limit-#12 situation again, and it is a gap rather than
a decision.

Closing it means a hand-built HEIC carrying a grid, an auxiliary image, a thumbnail
and a `uri ` item — the `pdf_corpus.py` pattern, where the corpus is written byte by
byte and deliberately **shares no code with the scrubber**, so a shared
misunderstanding of `iloc` cannot cancel out. That is M5's first task, before the
plugin, because a matrix computed on a corpus that cannot express the format's hard
cases would be measuring the easy half.

Phase 4 continues to MP4 (cheap, reuses `stco` patching as-is) and then RAW, whose
embedded full-size previews are the same "item with its own EXIF" shape HEIC
establishes here.

---

## 4. M5 as built — and the three defects the corpus found

M5 was planned as tooling: build a HEIC that CI can use, then plug the format into
the harness. It did not stay tooling. The rule the plan opened with — *a matrix
computed on a corpus that cannot express the format's hard cases would be measuring
the easy half* — turned out to apply to the tier itself, not just to the matrix.
Auditing the six real photos against what F1 actually emitted, before writing any of
the new code, found three defects. Each one had six green pixel-identity tests
standing over it.

### 4.1 A tiled auxiliary image is not the eight bytes that describe it

M4 reported that the auxiliary images were dropped. They were not. On an iPhone an
auxiliary image is usually a **grid**, exactly like the photograph: `iloc` points at
an 8-byte grid descriptor and the picture lives in twelve or thirty separate `hvc1`
items. Dropping the item named in `iinf` removed the *description* and left every
tile in `mdat`:

| file | in | out (as M4 built it) | orphaned tiles |
|---|--:|--:|--:|
| IMG_0502 | 1 166 577 | 997 481 | **381 209 B** (45 items) |
| IMG_2427 | 1 785 048 | 1 708 590 | **707 399 B** (45 items) |
| IMG_0947 | 803 712 | 792 217 | 239 188 B (14 items) |

Every one of the six leaked; the worst case was **41% of the output**. The tiles were
individually decodable HEVC, and no check caught it — ours because it looked for
named items, ExifTool's because there was nothing left to name. The file said the
mattes were gone and carried them anyway.

The fix is not a special case for grids. Removal is now computed on the `dimg`
graph: **an item survives only if a surviving root still composes it**, roots being
the items nothing derives from, minus whatever policy removes. Removed items are
**barriers**, so nothing is reached through them while a tile some other surviving
image also uses is still reached by that path.

The residual check that catches it is the more interesting half. It has to be
**connectivity, not naming** — and *undirected*, because deleting a tiled image also
deletes the `dimg` reference that made its tiles tiles, and a directed walk then
reads the abandoned tiles as top-level items and calls the file clean. That is not
hypothetical: the first version of the check did exactly that and reported zero
orphans on the broken output.

### 4.2 `iloc` wrote offset 0 for every item stored in `idat`

The walker recorded `idat`-relative extents as offset `-1`, deliberately, so that
anything moving bytes would skip them. The writer then clamped the `-1` back to `0`.
So every `idat`-stored item was located at the start of `idat`.

The primary grid survived it, because it is written first and 0 is its real offset —
which is precisely why six pixel-identical tests passed. Everything beside it did
not: a `tmap` at offset 24 read the grid descriptors instead of its own tone-mapping
data. This is the `stco`/`co64` trap one level below `mdat`, and the channel it
corrupts is one the pixel oracle **cannot see**.

`Extent` now carries `idat_offset` alongside the `-1`, and `idat` is re-packed like
`mdat` — which also stops the descriptions of removed auxiliary grids being copied
into the output, where anyone reading `iloc` rather than `iinf` would still find
them.

### 4.3 `grpl` named items nobody rebuilt

`grpl` holds entity groups — Apple writes an `altr` group meaning "these are
alternatives, show one" — and they name items by id. It was copied through verbatim,
so a group could outlive the item it names: the same dangling reference `iref`
already guards against, in the box nobody looks at.

### 4.4 The auxiliary-image decision, revisited

M4 dropped every auxiliary image. Measuring what they actually are made that wrong in
one case:

| `auxC` type | what it is | verdict |
|---|---|---|
| `hdrgainmap` | half-resolution luminance of the same scene | **kept** |
| `semanticskinmatte` / `semanticskymatte` / `portraiteffectsmatte` | machine-made claims about the subject | dropped |
| `styledeltamap` | the photographic edit that was applied | dropped |
| `linearthumbnail` | a second picture of the same scene | dropped |
| anything unrecognised | — | dropped |

The gain map is how the photograph **renders** on an HDR display, and removing it is
a content change under hard constraint #1. The uncomfortable part is that our own
oracle cannot see it: libheif decodes the primary item and ignores the gain map
entirely, so the version of the tool that threw it away passed every pixel test we
had. A decision that a test cannot make is recorded in `limits.md` (#32) instead, so
a future change to it has to argue with something.

Keeping it means keeping its tiles and its `tmap`, which §4.1's graph rule does
without a special case. The allowlist is matched on the **trailing component** of the
URN, because Apple has rewritten the prefix twice (`…2019:aux:` → `…2020:aux:` →
`tag:apple.com,2023:photo:aux:`) while keeping the name at the end; the allowlist is
the small side, so an aux type we have never seen fails toward removal.

### 4.5 The corpus

`heic_corpus.handbuilt()` writes the container **byte by byte** and imports nothing
from `src.scrub` — a test asserts that by walking the module's import statements, not
by grepping for a string. `pillow-heif` is still used, but only as an HEVC *encoder*:
the tiles are real, which is what lets the acceptance test **decode** the result. It
does: libheif composes our hand-built 2×2 grid into a 128×128 image.

What it carries, and what each one is for:

| structure | the path it covers |
|---|---|
| 2×2 `grid` primary, bytes in `idat` | construction method 1 — the shape of every iPhone photo |
| 2×1 gain-map grid (`auxl`) | a **tiled** auxiliary image that is kept |
| 1×2 matte grid (`auxl`) | a tiled auxiliary image that is **dropped**, with its tiles |
| single-tile matte (`auxl`) | the untiled case, so the graph rule is not only right for grids |
| `thmb` thumbnail | Phase 1's lesson in a container |
| `Exif` / `mime` / `uri ` | the metadata items, including a subject-describing plist |
| `altr` entity group | §4.3 |
| shared `ipco` properties | pruning forces the renumbering |

The three grids are deliberately **different shapes**. They were the same shape
first, and that made the file useless for catching §4.2: every grid descriptor was
byte-identical, so writing them all at offset 0 still decoded correctly. A corpus
that cannot tell a wrong answer from a lucky one is not a corpus.

Both defects were then re-run against the old code paths to confirm the new tests
*fail* on them — a regression test that passes on the broken version is decoration.

### 4.6 The matrix, and what A2 says

`HeicPlugin` exposes the **muxer** channel only: brand, compatible brands, top-level
and `meta` table order, `iloc` field widths, primary kind, tile count, item and
reference inventories, property inventory, and the `hvcC` decoder-configuration
digest. The coded tile digest is exposed separately and kept **out** of it, for the
reason written up in `m4a.py` — compressed content in a categorical A2 channel makes
one format judged against its own content while another's channel is headers-only.

The peer set is three container-writer profiles wrapping **byte-identical tiles**, so
anything separating them is the writing software and not the picture. A test checks
that claim rather than asserting it in prose.

| | F1 | F2 | F3 |
|---|---|---|---|
| **A1** | ✅ pass | not_tested | not_tested |
| **A2** | ❌ fail — `brand`, `compatible_brands`, `meta_table_order`, `iloc_widths`, `size` | not_tested | not_tested |

The A2 failure is the expected result and the reason for running the cell. F1
relocates items and rebuilds the tables and deliberately changes nothing else, so
every writer choice passes through. Published as a failure with each feature named,
because that list **is** the specification for an F2: one canonical brand, one table
order, one set of `iloc` widths, canonical item numbering, tiles untouched. Same
method Phase 3 used on PDF — measure which producer channel leaks before designing
the fix.

Phase 4 continues to MP4 (cheap: `stco`/`co64` patching already exists and is proven)
and then RAW, whose embedded full-size previews are the same "item with its own EXIF"
shape HEIC establishes here.
