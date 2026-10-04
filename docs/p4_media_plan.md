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

---

## 5. MP4 and MOV — the opening spike (M7)

The earlier sections call MP4 "cheap", and that word was an **inference, not a
measurement**: it came from one ffmpeg-produced file whose two `stco` tables
`shift_chunk_offsets()` already covered. ffmpeg is not what makes the videos people
actually carry. Measured on real ones, the offset patching is reused as promised,
and it is also the smallest part of the job. The spike found four things none of the
earlier formats had.

### 5.1 The corpus

Thirteen real files from this machine, measured in place (a sixth iPhone clip, `IMG_0545`, was added after the first pass and confirmed its prediction; §5.4). They are personal videos:
none is copied into the repository, and this document gives no coordinates, dates or
identifiers from them, only whether each is present.

| producer | files | container | brand | size |
|---|--:|---|---|---|
| iPhone 16 camera, iOS 18.6.2 and 18.7.8 (`IMG_9117`–`9121`, `IMG_0545`) | 6 | QuickTime | `qt  ` | 32 MB – 3.37 GB |
| macOS capture on a MacBook Pro (AVFoundation; screen `pixeldensity` key) | 3 | QuickTime | `qt  ` | 56 – 160 MB |
| macOS export from an editing timeline | 1 | QuickTime | `qt  ` | 251 MB |
| WhatsApp's transcoder (x264 + libavcodec) | 2 | MP4 | `mp42` + `isom` | 6 – 25 MB |
| Apple Core Media export, ISO flavour | 1 | MP4 | `mp42`, minor 1 | 221 MB |

**The iPhone does not record MP4. It records QuickTime.** A handler scoped to the
`isom`/`mp42` brands would leave out every video a phone camera made, which is the
file this phase exists for. So the handler is **MP4 and MOV together**: the same box
grammar, with the one dialect difference in §5.2.

No file in the corpus is fragmented (`moof`/`mvex`/`sidx`), none has more than one
`mdat`, and every data reference is self-contained (`alis` or `url ` with flag 1).

### 5.2 The shared walker cannot parse an iPhone video

`isobmff.parse()` fails on all eight QuickTime files, and fails closed, which is the
correct behaviour:

```
ISOBMFF: box b'\x00\x00\x00\x00' at 8857 declares size 1751411826, which overruns its container
```

`1751411826` is `0x68646c72`, ASCII `hdlr`. In ISO 14496-12 `meta` is a **full box**,
with 4 bytes of version and flags before its children, and `FULL_CONTAINERS` encodes
exactly that. In QuickTime, `moov/meta` and `trak/meta` are **plain containers**. The
walker skips 4 bytes into the first child's header and reads its type as a size.

The fix belongs in `standards/isobmff.py`, and it must be decided **per box, from the
bytes**, not per brand, because nothing forbids either layout in either brand. A
QuickTime `meta` has a child box type at payload offset 4 (`hdlr`); an ISO `meta`
has zero version and flags there. The serializer has to write back the dialect it
read. M4A and HEIC depend on the ISO reading, so their suites are the regression
test for the change.

**Fixed (M8, first half).** `_quicktime_meta()` reads the first word of the body:
zero means ISO; a non-zero size that fits, followed by a printable four-character
type, means QuickTime; anything else takes the ISO path and fails closed there. A
QuickTime `meta` is recorded with an empty payload, so `serialize()` writes back the
dialect it read with no change to its own code. Measured on the corpus: **all twelve
`moov` boxes parse and re-serialise byte-identically**, and every `meta` (two to
three per Apple file, all QuickTime) resolves to `hdlr`/`keys`/`ilst`. Before the
fix, eight of the twelve could not be parsed at all. CI covers it with hand-built
boxes in `tests/scrub/test_isobmff_dialects.py`, including both dialects in one
file. Five of its seven tests fail on the old walker. The ISO round-trip passes on
both, which is the point. The full suite is unchanged: 617 passed.

### 5.3 The leak surface, per locus

| locus | measured | disposition |
|---|---|---|
| `moov/meta` (`mdta` keys) | iPhone: **GPS (ISO 6709) plus horizontal accuracy**, make, model, iOS version, and creation date **with its UTC offset**. Mac: make, model `MacBookPro18,1`, macOS build `14.6.1 (23G93)`, creation date | **drop** |
| `trak/meta` | Video track: lens model (`iPhone 16 back camera 2.22mm f/2.2`), 35 mm focal length, the screen's `pixeldensity`. Audio track: the **microphone** (`MacBook Pro Microphone (Digital Mic)`, `Apple Inc.`) | **drop** |
| timed-metadata tracks (`hdlr` `meta`, sample entry `mebx`) | **Six per iPhone clip**, with their samples in `mdat` next to the video: 149 KB to 13.5 MB per file. **Per-frame detected faces** (bounds, face-id, roll, yaw; 243 to 6,949 samples), live-photo info, scene illuminance, cinematic audio, video orientation, and `segment-identifier`, which is **a per-recording UUID in plain ASCII, different in every clip** | **drop** the track, its samples, and every `tref` that names it |
| unreferenced bytes in `mdat`, and a trailing top-level `free` | §5.4. **The exact GPS coordinates, in every iPhone video: 6 of 6** | **excise** |
| `mvhd`/`tkhd`/`mdhd` times | The recording time, to the second, on every track. WhatsApp alone zeroes them | **zero**, the M4A rule |
| `hdlr` names | `Core Media Video`/`Audio`/`Metadata`/`Data Handler`: a Pascal string in QuickTime, a C string in the ISO export, empty from WhatsApp | muxer channel; decided in M10 |
| top-level `wide`, `beam` | QuickTime's placeholder, and WhatsApp's own 16-byte box | **drop**. The top level is a keep-allowlist: `ftyp`, `moov`, `mdat` |
| x264 SEI, inside the coded video | WhatsApp: `x264 - core 155 r2917 0a84d98 … options: cabac=1 ref=2 …` plus `Lavc58.54.100`, 21 copies in a 25 MB file | encoder channel; F2 candidate (§5.6) |
| sample-entry children: `hvcC`/`avcC`, `dvvC`, `amve`, `colr`, `fiel`, `chrm` | Decoder configuration, Dolby Vision, HDR viewing environment, colour | **keep** (content) |
| `apac` second audio track, linked by `tref/fall` | iPhone 16 spatial audio. **ffmpeg cannot decode it** | **keep** (content), verified by sample-byte identity because it cannot be decoded |

### 5.4 The file keeps a second copy of its metadata where no table points

This is the headline, and nothing in the M4A or HEIC work predicted it. The spike
resolves every sample of every track to a byte range, from `stsc`, `stsz` and
`stco`/`co64`, and then asks which bytes of `mdat` **no sample covers**:

| file | unreferenced bytes | gaps | non-zero | what is in them |
|---|--:|--:|--:|---|
| IMG_9117 | 33,518 | 2 | 15,453 | **exact GPS**, make/model/software/creation-date keys, lens model, face-detection key table, a `bplist00`, stale `moof`/`traf` |
| IMG_9118 | 49,024 | 2 | 23,509 | the same, **exact GPS** |
| IMG_9119 (4.9 s) | 0 | 0 | — | none in `mdat`; the copy is in a trailing `free` box instead (below) |
| IMG_0545 (5.9 s) | 0 | 0 | — | the same |
| IMG_9120 | 1,198,025 | 50 | 574,075 | **exact GPS** |
| IMG_9121 | 887,841 | 39 | 426,061 | **exact GPS** |
| Mac capture 1 | 1,496,557 | 177 | 38,642 | model, macOS build, creation date with UTC offset |
| Mac capture 2 | 7,817,961 | 956 | 128,408 | the same, plus the microphone name |
| Mac capture 3 | 1,520,402 | 175 | 48,182 | the same |
| timeline export, WhatsApp ×2, ISO export | 0 | 0 | — | — |

"Exact" means the ISO 6709 string in `moov/meta`, byte for byte, found in the
unreferenced region.

**The cadence is fixed: one stale copy per 10.0 seconds of recording.** Mapping each
gap back to the video sample in front of it puts them at t = 10.0, 20.0, 30.0 s … in
every iPhone clip (15.8 s gives 2, 492 s gives 50). IMG_9119 is 4.9 s long, so it never
reached the first one.

**A short clip does not escape; the copy just moves.** Both clips under ten seconds
end with a 2,352-byte top-level `free` box after `moov`, and it holds the same key
table with **the exact GPS, the model, the iOS version and the creation date**. No
clip longer than ten seconds has that box. So the count is **every iPhone video, 6
of 6**: long clips carry the copy in `mdat` once every ten seconds, short ones in the
trailing `free`. `IMG_0545` was added after the cadence was measured, and it is the
confirmation: 5.9 s, no `mdat` gaps as predicted, GPS in the `free` box. On it,
`exiftool -all=` again leaves the coordinates and then lists zero GPS tags, and MAT2
again crashes with return code 234 and writes a 0-byte file.

This is why the top level is a keep-allowlist (`ftyp`, `moov`, `mdat`) and not a
drop-list. A drop-list only works if you already know every box you need to drop. The stale `moof`/`traf` headers are consistent with a camera
that records as a fragmented movie for crash safety, then writes one final `moov` and
leaves the fragment scaffolding where it was. That is an interpretation. The bytes
are the measurement.

This is PDF's incremental-update history in a new container: **data the file no
longer points at, which every reader ignores and every byte-level reader finds.** It
is invisible to any tool that works on the box tree.

### 5.5 What the benchmark tools do with it

MAT2 0.14.0 and ExifTool 13.55, on copies:

| | iPhone `.mov` | Mac `.mov` | WhatsApp `.mp4` |
|---|---|---|---|
| **ExifTool `-all=`** | Succeeds. **The exact GPS is still in the file**, with model, lens, the face-detection keys and the creation date | Model and creation date survive | x264 string survives, all 21 copies |
| **MAT2** | **Refused** (`video/quicktime` not supported). Renamed to `.mp4`, it **crashes (ffmpeg return code 234) and leaves a 0-byte output file** | Refused. Renamed, it cleans (an ffmpeg remux, which drops the slack) | x264 string survives. Output is 267 bytes **larger** |

Two consequences, both of which change how this format is built:

- **ExifTool cannot be the oracle for this locus.** Read back with `-ee`, ExifTool's
  own output shows no GPS at all, while the coordinates are still in the bytes. It
  still shows the recording time on every track and **every per-frame face bounding
  box**, because it removes no tracks. The A1 check for MP4 has to work on bytes, and
  "no unreferenced bytes in `mdat`" has to be a residual check of its own.
- **ffmpeg cannot be the F1 engine.** MAT2's crash is ffmpeg failing to stream-copy
  `apac`, a codec it does not know. Every iPhone 16 video has that track, so a
  remux-based F1 fails on the most important input. F1 is our own box surgery, as it
  was for M4A and HEIC.

### 5.6 Design decisions the spike forces

1. **F1 emits only what the kept sample tables reach.** `mdat` is rebuilt chunk by
   chunk, in the original file order so the interleave is untouched, from the tables
   of the tracks we keep. Every chunk offset is then **rewritten individually**, not
   shifted by one delta. Slack and dropped tracks' samples leave by construction:
   there is no deletion pass to get wrong, the principle PDF's M2 serializer was
   built on. Zero-filling was the alternative. It keeps the offsets but also keeps
   the gap layout (every Mac file opens `mdat` with the same all-zero 16,348-byte
   gap, a producer tell) and the inflated size. So the new shared primitive is a
   **sample-table resolver and a per-chunk remap**, next to `shift_chunk_offsets()`
   in `standards/isobmff.py`.
2. **Two independent content checks.** (a) Per-track sample identity: every kept
   sample's bytes, in order, equal the source's. (b) A decode through ffmpeg
   (`framemd5` of video and `mp4a` audio). A shared misreading of `stsc` could
   cancel out in (a) alone, and ffmpeg is the independent implementation. `apac`
   gets (a) only, and that is stated wherever the check is described.
3. **Memory is a real constraint here, not a hardening detail.** The handler
   interface is bytes in, bytes out, and the largest file in the corpus is 3.37 GB,
   so about 7 GB would be resident. **Open, to be decided before M10:** refuse above
   a size cap now and stream in the November hardening, or stream from the start.
4. **x264's settings string is the LAME tag's analogue, and it may not need a
   re-encode.** It lives in a user-data-unregistered SEI (payload type 5), a NAL unit
   of its own and non-normative, so dropping it should leave the decoded frames
   identical. That is a **hypothesis to measure, not a claim**. It changes sample
   sizes, so it needs the per-chunk remap from (1), which is one more reason to build
   the remap rather than zero-fill.
5. **Refused, fail closed:** fragmented files, external data references, more than
   one `mdat`, samples outside `mdat`, and overlapping sample ranges. None occurs in
   the corpus, so each refusal gets a hand-built test rather than a real one.

### 5.7 Milestones

| # | Deliverable | Status |
|---|---|---|
| **M7** | Opening spike: this section | ✅ |
| **M8** | Walker: the QuickTime `meta` dialect in `standards/isobmff.py`, read and written back as found; `claims()` for video (a `vide` handler and not a HEIC brand, the mirror of M4A's rule); the refusal list. M4A and HEIC routing unchanged | ✅ (§6) |
| **M9** | Sample-table resolver and per-chunk remap (shared), with the resolver checked against ffmpeg's own packet positions on the real corpus | ✅ (§6): every sample of every track matches ffprobe's positions |
| **M10** | MP4/MOV F1: drop the metadata boxes and tracks, rebuild `mdat` from the kept tables, zero the times, decide the handler names. Acceptance: sample identity **and** decode identity on all thirteen real files, zero unreferenced bytes, and GPS absent from the bytes, not only from ExifTool's view | ✅ (§6): real clips decode identically, GPS gone from the bytes |
| **M11** | Hand-built corpus, byte by byte with no `src` imports: QuickTime `meta`, a `mebx` track with a `tref`, a stale `moov` fragment in the slack carrying a planted ISO 6709 string, `co64`, and interleaved chunks, so CI covers what real files do | ✅ (§6) |
| **M12** | `Mp4Plugin`, matrix, and the A2 cell: the muxer channel (brand, top-level order, handler names, box inventory), with the encoder channel reported separately, as M4A does | ✅ (§9) — ported from the parallel branch and re-measured on this F1 |
| **M13** | F2 candidate: SEI user-data removal, measured for decode identity | |
| **M14** | `limits.md` rows, the benchmark row, README | ✅ limits #34–#39 and #42–#44, README, benchmark rows and Evidence 8 (§9) |
| **M15** | Camera RAW opening survey: eight makers, before any code | ✅ (§7, with the parallel survey in §8.9) |

---

## 6. M8–M11 as built

**The handler is MP4 and MOV at F1**, `formats/mp4/`, registered after M4A and HEIC.
`claims()` needs a known video brand **and** a `vide` track. The brand list is a
keep-list, because Canon's CR3 is ISOBMFF with `vide` tracks too, and a camera raw
scrubbed with video-shaped assumptions is the mistake M4A's handler exists to avoid.
A test sets the brand to `crx ` and to an unknown code and requires both to be
declined. M4A's `claims()` now parses `moov` alone instead of the whole file. It was
copying a multi-gigabyte `mdat` just to learn the file was a video.

**The shared primitives** are in `standards/isobmff.py`: `scan()` (top-level headers
with no payload copy), `chunks()` (a sample table resolved into chunks and sample
sizes, refusing `stz2` and any table that does not account for every sample), and
`set_chunk_offsets()` (a per-chunk rewrite, the sibling of `shift_chunk_offsets()`).
The resolver was checked before anything was built on it. **Every sample of every
track in five real files matches ffprobe's own position and size**: video, both audio
tracks including `apac`, and all six timed-metadata tracks, over 30,000 samples. The
first comparison disagreed on two or three audio samples per clip and on one screen
recording. Every difference was ffmpeg applying the **edit list** (hiding the AAC
priming frames, repeating frames); with `-ignore_editlist 1` they match exactly. The
lesson for anyone repeating it: compare against the demuxer with edit lists off, or
the independent check disagrees for a reason that has nothing to do with the tables.

**F1** is §5.6(1) as designed. It emits `ftyp`, `moov` and `mdat` in their original
order, rebuilds `mdat` from the chunks of the kept tracks in file order, and writes
each offset individually. Before returning, it re-resolves the output with the same
primitives and requires every kept track's samples to hash identically. Three things
were decided here rather than in the plan:

- **Handler names and the `hdlr` reserved words are blanked.** QuickTime's `hdlr`
  keeps the component manufacturer (`appl`) in words that ISO calls reserved and
  QuickTime documents as "set to 0". The name is free text naming Apple's framework.
  No decoder reads either. The component type (`mhlr`/`dhlr`) is kept: it is the
  QuickTime dialect marker, not a name.
- **Track IDs are not renumbered.** Dropping tracks 4–9 leaves IDs 1–3 and a
  `next_track_ID` of 10. That is a structural trace of *how many* tracks there were.
  It goes on the A2 list for M12 rather than being patched blind.
- **`tref` is pruned per entry, not dropped.** The `apac` track's `fall` reference to
  the AAC track is content (it is how a player picks a fallback), so it stays; any
  entry naming a dropped track goes.

**Measured on the real clips** (five readable; the WhatsApp files became unreadable to
this process mid-session, a macOS permission on files downloaded by WhatsApp, and
were not forced):

| | before → after | decoded frames | residuals | stale bytes | ExifTool tags |
|---|---|---|---|---|---|
| IMG_9117 (15.8 s) | 54.2 → 53.9 MB | 473 video + 740 audio, **identical** | none | 33,518 → **0** | 7,405 → 118 (with `-ee`) |
| IMG_0545 (5.9 s) | 42.3 → 42.3 MB | 355 + 278, **identical** | none | trailing `free` → **gone** | 301 → 103 |
| IMG_9119 (4.9 s) | 32.2 → 32.0 MB | 293 + 229, **identical** | none | trailing `free` → **gone** | — |
| Mac capture | 55.7 → 54.2 MB | 10,313, **identical** | none | 1,520,402 → **0** | — |
| WhatsApp (x264) | 25.1 → 25.1 MB | 6,045 + 9,450, **identical** | none | 0 | encoder string **kept, reported** |

The GPS string is absent from every output's bytes, not only from ExifTool's view.
What ExifTool still lists on a scrubbed iPhone clip is structure: dimensions,
durations, codec names, the brand, and dates of `0000:00:00`.

**The report's cross-check raised one false alarm, and it was ExifTool's wording, not
the file.** "Apple QuickTime (.MOV/QT)" is ExifTool's name for the brand code
`qt  `. The file holds only those four bytes, so the removed `Make: Apple` appeared to
survive. It is exempted as a single tag, `MajorBrand`, with the reason in the handler,
the same way HEIC exempts `AuxiliaryImageType`.

**The hand-built corpus** (`tests/scrub/mp4_corpus.py`, table in its docstring)
carries every locus in §5.3, in four layouts (`moov` before or after `mdat`, `stco`
or `co64`), with ffmpeg used only as an encoder. A libx264 variant covers the kept
encoder string in CI, now that the real WhatsApp files cannot be read here. MP4 joins
the fuzz, idempotence and cross-process determinism suites. **Seven refusals are
tested**: fragmented, two `mdat`s, external data, a subtitle-type track, `stz2`, a
sample outside `mdat`, and a sample count the file cannot hold. The last one is a bug
found by reading the resolver, not by a test. A fixed-size `stsz` states its count
as a bare 32-bit number, and `chunks()` built a list that long before checking it,
so a damaged count of about four billion was an allocation, not a refusal. The count
is now checked against the file's length first, and every MP4 caller passes it.

One older test changed meaning rather than breaking.
`test_m4a.py::test_dispatch_refuses_mp4_video` required dispatch to find **no**
handler for an MP4 video, which was correct only while Phase 4 had none. Its real
point, that the *audio* handler declines video, is kept. It now also requires that
the video lands on this handler.

**Speed:** 0.2 to 0.3 s per real clip. **Memory:** 203 MB peak for the 54 MB clip,
about 3.7 times the file (limit #38). §5.6(3) is answered for now by stating it
rather than capping it: nothing is refused for size, the cost is written down, and
streaming is November's hardening work.

**Suite after M8–M11: 703 passed, 1 skipped** (the long-standing M4A engine skip). Five
of those tests need the real clips and skip on CI.

**Still open for video:** M12 (plugin, matrix, the A2 cell, and the benchmark row)
and M13 (the SEI experiment). F1 is the only tier offered, and the A2 cell is
`not_tested` until M12 runs; limit #37 says so in plain words.

---

## 7. Camera RAW — the opening survey (M15)

The schedule's rule for RAW was a survey of what the makers actually put in their
files **before any code**. This is that survey. No RAW code exists yet.

### 7.1 The corpus

Eight files from eight makers, from raw.pixls.us (a public sample archive), kept
outside the repository in `~/metadata-research/raw/` with a manifest of source URLs
and SHA-256 hashes. All eight re-verified against the manifest before measuring.

| file | container | our `tiff_ifd` today | `rawpy` (LibRaw) decodes |
|---|---|---|---|
| Apple iPhone 12 Pro `.DNG` | TIFF | parses | yes |
| Canon EOS 80D `.CR2` | TIFF | parses | yes |
| Nikon D750 `.NEF` | TIFF | parses | yes |
| Sony A7 III `.ARW` | TIFF | parses | yes |
| Olympus E-M10 IV `.ORF` | TIFF with magic `IIRO` | refused (magic) | yes |
| Panasonic G9 `.RW2` | TIFF with magic `IIU` | refused (magic) | yes |
| Fujifilm X-T4 `.RAF` | Fuji header, then JPEG and TIFF | refused | yes |
| Canon EOS R6 III `.CR3` | ISOBMFF, brand `crx ` | n/a (the MP4 handler declines it by brand, tested) | yes |

Four of eight are plain TIFF that the Phase 1 reader already walks. Two more are
TIFF with a different magic number. RAF needs its own header parser. CR3 is the
ISOBMFF walker again, with Canon's `uuid` boxes.

### 7.2 What identifies the camera, and the person

Measured with ExifTool as the measuring stick. The samples are public, but this
document still does not repeat the names and email address found in them.

| | body serial | internal serial | lens serial | owner / artist | usage counter | other |
|---|---|---|---|---|---|---|
| iPhone DNG | — | — | — | — | — | **GPS**, iOS version, `UniqueCameraModel` |
| Canon CR2 | ✔ | ✔ | ✔ | **owner name + artist** | — | copyright line |
| Canon CR3 | ✔ | ✔ | ✔ | (empty fields) | **image count 4,243** | **`ImageUniqueID`** |
| Fuji RAF | ✔ | ✔ | ✔ | (empty) | image count | firmware |
| Nikon NEF | ✔ | — | — | **an email address in `Artist`** | **shutter count** | firmware |
| Olympus ORF | ✔ | ✔ | ✔ | (empty) | — | firmware |
| Panasonic RW2 | — | ✔ | ✔ | — | — | firmware |
| Sony ARW | — | ✔ (binary) | — | — | shutter count | firmware |

A serial number is a stronger identifier than anything in the photo formats so far,
because it is stable across every picture the camera ever takes. The usage counters
are weaker per file but **order** a set of photos from one camera in time. Both live
mostly inside the **maker note**, which is where the design problem is (§7.4).

### 7.3 Every file carries a second picture, and some carry a second copy of the data

Every file embeds full-size or near-full-size JPEG previews beside the raw data,
from 1616×1080 up to 6016×4016, plus a thumbnail. Three of them carry **their own,
independent copy of the metadata** inside the preview:

| file | embedded preview | what the preview carries by itself |
|---|---|---|
| iPhone DNG | 4032×3024 JPEG, 5.4 MB | **GPS**, make, model, software, lens, host computer, date |
| Fuji RAF | 4416×2944 JPEG, 4.2 MB | **body, internal and lens serials**, image count, make, model, date |
| Panasonic RW2 | 1920×1440 JPEG, 0.7 MB | **internal and lens serials**, firmware, make, model, date |

This is the "item with its own EXIF" shape HEIC established, and the Phase 1 thumbnail
lesson at full size. A scrubber that cleans the RAW's tags and copies the preview
leaves GPS in a DNG and every serial number in a RAF. Measured: `exiftool -all=` does
exactly that to the DNG (§7.5). The other five previews carry no
metadata of their own, and each is still a finished rendering of the photo, which the
RAW decoder does not need.

### 7.4 The maker note is not metadata you can drop

The obvious F1 is to delete the maker note: it holds most of the serials. The test was
to zero each maker note **in place** (same size, so no offset moves) and decode with
LibRaw, both the raw sensor data and a rendering with the camera's white balance:

| file | maker note | sensor data | rendered with camera white balance |
|---|--:|---|---|
| Canon CR2 | 41 KB | identical | **different**: white balance lost (`1771/1024/1827` → `0/1/0`) |
| **Nikon NEF** | 148 KB | **does not decode** ("data corrupted") | — |
| Sony ARW | 37 KB | identical | identical |
| iPhone DNG | 1 KB | identical | identical |

So "strip the maker note" **destroys a Nikon photo and discolours a Canon one**. The
serial numbers share a block with the decompression curve and the colour data the
decoder needs. **RAW F1 therefore edits inside the maker note, maker by maker**,
blanking the identity fields in place without moving a byte. Every other maker-note
tag is kept, and the per-maker list of what is blanked is the thing to build and test.
Two decode oracles are required, sensor data and camera-white-balance rendering,
because the Canon result shows one alone passes a file with its colours gone.

### 7.5 The benchmark tools

**MAT2 0.14.0 supports none of the eight formats** ("format (None) is not
supported"). **`exiftool -all=`** runs on all eight, and:

- **leaves text-stored serial numbers in the bytes in five of eight**: Canon CR2 (body,
  internal and lens serials, **and the owner's name and the artist**), Canon CR3 (body,
  internal, lens), Fuji RAF (body, lens), Nikon NEF (body serial, **and the email
  address**), Olympus ORF (body, internal, lens). It cannot delete maker notes, and
  it warns that it cannot delete IFD0 from a CR2, NEF, ORF, RW2, ARW or DNG;
- **left the GPS in the DNG.** ExifTool's own listing of its output shows no GPS,
  but the 4032×3024 preview inside the file still carries its own **exact position
  and the phone model**. ExifTool does not read inside that preview, so it cannot
  report what it left there. This is the video finding again (§5.5): the tool reads
  its output as clean while a copy of the location remains. It *did* clean the
  previews inside the Panasonic and Fuji files, and it removed Panasonic's text
  serials;
- **produced a Fuji RAF that LibRaw cannot open** (I/O error), which ExifTool cannot
  read back to the end either ("Unexpected end of file"), 78 KB shorter. That is one
  sample, and is stated as one sample.

Values stored as binary (shutter and image counts, Canon's `ImageUniqueID`, Sony's
internal serial) cannot be found by a byte search for their displayed text. So
whether ExifTool removed them is **not measured here**, and the list above does not
claim it.

### 7.6 What this sets up

| # | Next | Why |
|---|---|---|
| **M16** | Extend `tiff_ifd` to accept the `IIRO`/`IIU` magics, and model a maker note as a located, **size-preserving** region with per-maker tag tables | four TIFF files are walkable now, six with the magic fix; nothing may move |
| **M17** | RAW F1 for the TIFF family: blank the identity tags in IFD0/ExifIFD and inside the maker note, and drop or clean each embedded preview; acceptance = LibRaw sensor data **and** camera-WB rendering identical, every identity value absent from the bytes | the two oracles from §7.4 |
| **M18** | RAF (own header) and CR3 (ISOBMFF, Canon `uuid` boxes) | the two non-TIFF containers |

**Open decision for M17: drop the previews, or keep a cleaned copy?** Dropping them is
safest, but some viewers and OS thumbnails show the preview instead of rendering the
raw. Stripping a preview's own EXIF keeps it, but it is still a finished rendering
made by the camera's processing, which is its own fingerprint. To be settled with
the viewer behaviour measured, not by taste, as HEIC's auxiliary images were.
---

## 8. The parallel line, 19–25 September: ISO MP4 and a second RAW survey

MP4 was built twice. A branch opened from the HEIC close (`39f323a`) and built an
ISO-dialect MP4 with its own read-only walker, a strip that copied `mdat` whole, a
harness plugin, the A2 cell, and a second camera-RAW survey. It was never merged, and
§5–§7 were then written on `main` without it. The two met on 2026-10-04 (§9).

**Read this section as history; its measurements stand, three of its decisions do
not.** It was written under a scope decision this file's §5 reversed: real phone video
was out of scope, so QuickTime was refused by name and `mebx` tracks were untested
scope (its §8.4, §8.8). `main`'s F1 scrubs QuickTime, including the stale copy in
`mdat` that a copy-`mdat` strip keeps. Its `walker.py` did not survive the merge;
its refusals did (§9). Its own section numbers are kept below for the record: its §5
to §15 are §8.1 to §8.11 here, and limit numbers in it are as they were then (its
#35, #36, #38 and #39 are #41, #42, #43 and #44 now; its #40, QuickTime refused, is
gone).

### 8.1 M0 — measured ground truth (the opening spike)

Six producers, one 2-second 160×120 test clip, measured with the existing
`standards/isobmff.py` walker and `exiftool` as the measuring stick. No real
phone video is in this corpus yet — see M1 below, and read every number here as
being about *muxers we can run*, not about what an iPhone writes.

| producer | how | brand / compatible | top-level order |
|---|---|---|---|
| `ffmpeg_x264` | ffmpeg 8.1.1, libx264 | `isom` / isom,iso2,avc1,mp41 | `ftyp free mdat moov` |
| `ffmpeg_x264_faststart` | same, `-movflags +faststart` | `isom` / same | `ftyp moov free mdat` |
| `ffmpeg_videotoolbox` | ffmpeg, `h264_videotoolbox` | `isom` / same | `ftyp free mdat moov` |
| `avfoundation` | Swift `AVAssetExportSession`, passthrough | **`mp42`** / isom,mp41,mp42 | `ftyp mdat moov` |
| `mat2_out` | mat2 0.14.0 over `ffmpeg_x264` | `isom` / same | `ftyp free mdat moov` |
| `exiftool_out` | `exiftool -all=` over the tagged file | `isom` / same | **`ftyp free moov mdat`** |

The AVFoundation producer is a pass-through re-mux (the coded video and audio are
copied, only the container is rewritten), which is deliberate: it gives a second
*muxer* without introducing a second *encoder*, so the two producer channels M4A
taught us to separate stay separated from the first measurement.

Reproducing the corpus, so the numbers above can be re-derived rather than
taken on trust (the AVFoundation producer needs macOS; everything else is portable):

```
ffmpeg -f lavfi -i testsrc2=size=160x120:rate=15:duration=2 \
       -f lavfi -i sine=frequency=440:duration=2 \
       -c:v libx264 -preset ultrafast -pix_fmt yuv420p -c:a aac -shortest out.mp4
#  + `-movflags +faststart`                    -> the moov-first layout
#  + `-c:v h264_videotoolbox`                  -> a second video encoder, same muxer
#  + `-metadata location="+40.7128-074.0060/"` -> udta/loci
#  + `-movflags use_metadata_tags`             -> the mdta Keys namespace as well
```

AVFoundation's file is an `AVAssetExportSession` pass-through re-mux
(`AVAssetExportPresetPassthrough`, `outputFileType = .mp4`) of the first file —
about 20 lines of Swift, to be folded into the corpus module at M1.

#### The leak surface, per locus — and the same coordinate written three times

| locus | measured | disposition |
|---|---|---|
| `moov/udta/meta/ilst` (`mdir`/`appl`) | iTunes-style tags: `©too` = `Lavf62.12.101` in **every** ffmpeg file, `©nam`, `©cmt` | **drop** |
| `moov/udta/loci` | GPS, binary, `Lat=40.71280 Lon=-74.00600 Body=earth`. Written **whenever** `-metadata location` is set, independent of muxer flags | **drop** |
| `moov/udta/meta/keys` + `ilst` (`mdta`) | the Apple Keys namespace: `location`, `location-eng`, `com.apple.quicktime.location.ISO6709`, `make`, `model`, `encoder` | **drop** — see the trap below |
| `mvhd` / `tkhd` create+modify | ffmpeg writes **0**; AVFoundation writes **local wall clock** (`3872688647` = 2026-09-19 18:50:47) | **zero** |
| `hdlr` name field | ffmpeg writes the generic `VideoHandler` / `SoundHandler`; **AVFoundation writes `Core Media Video` / `Core Media Audio`** — the framework names itself in a field nothing needs | **normalize** |
| `udta/meta/hdlr` vendor | `appl` — in files ffmpeg wrote, on a machine with no Apple software involved | **drop with the box** |
| top-level `free` | 8 bytes, before `mdat`, in every ffmpeg file; absent in AVFoundation's | **drop** — and see the offset trap |
| `mdat` sample data | the video and audio themselves | **keep** |

The `hdlr` row is worth stating plainly: the one producer here that is not ffmpeg
puts its own framework's name inside every track, in a field no player needs, in a
file whose tag boxes are otherwise empty. `hdlr` is not a metadata box, so no
tag-oriented scrubber touches it — see the benchmark below, where that turns out to
matter more than the tags do.

**One coordinate, three boxes.** Asking ffmpeg for a location once writes it into
`udta/loci` *and*, with `-movflags use_metadata_tags`, three more times inside the
Keys `ilst` (`location`, `location-eng`, and the `com.apple.quicktime…` spelling).
This is the EXIF+XMP+IPTC problem of Phase 1 in a new container, and it means a
handler written against `ilst` alone leaves the GPS sitting in `loci`.

#### The trap: a Keys `ilst` entry has no name

In the `mdir` namespace an `ilst` child is a fourcc — `©nam`, `©too` — and a
scrubber can match on it. In the `mdta` (Keys) namespace it is **not**: the child's
four "type" bytes are a big-endian **1-based index into the `keys` box**, which
holds the names in a parallel list. Measured on `ffmpeg_gps.mp4`:

```
keys[1] = location        ilst child type b'\x00\x00\x00\x01'  value '+40.7128-074.0060/'
keys[4] = make            ilst child type b'\x00\x00\x00\x04'  value 'TestCorp'
keys[6] = encoder         ilst child type b'\x00\x00\x00\x06'  value 'Lavf62.12.101'
```

Three consequences, and the third is the one that is not merely a leak:

1. A handler that matches fourcc names finds **nothing here** — the GPS is invisible
   to it, not merely missed.
2. Dropping `keys` and keeping `ilst` leaves every **value** in the file and removes
   only its label. The coordinate is still there in plain ASCII.
3. Dropping *one* entry from `keys` **renumbers every entry after it**, so the
   surviving `ilst` values silently acquire the wrong names. That is not a leak, it
   is a corruption that still parses and still plays — the same species as the
   `stco` failure M4A taught, in the metadata table rather than the sample table.
   So `keys`/`ilst` are dropped or kept as a **pair**, never edited apart.

#### The offset trap fires, but not for the reason the plan assumed

§"Why HEIC first" says MP4 "reuses the offset-patching we already have … two tables,
both covered, zero new machinery." Both halves are true and the conclusion needs
narrowing, so the claim is left standing and corrected here rather than edited away.

`stco` holds **absolute** file offsets, so it needs patching only when bytes are
removed *before* `mdat`. Measured: `moov` sits **after** `mdat` in four of the six
producers, so removing metadata from `moov` moves nothing and the patch is a no-op.
But **every ffmpeg file carries an 8-byte `free` box before `mdat`**, and a `free`
box is padding — FLAC's lesson was to omit a tool constant rather than normalize it,
so we will want it gone, and the moment it goes `mdat` moves and every `stco` entry
is wrong. The trap is therefore conditional on *what we remove*, not on the format,
and the dangerous reading is "moov is at the end, so offsets are safe."

The `+faststart` layout puts `moov` before `mdat` outright, where any shrink moves
`mdat`. And **exiftool's own output is faststart** even though its input was not, so
a tool's layout choice is itself a producer channel, not a neutral detail.

#### Benchmark: what the standard tools do, measured rather than assumed

**On the ffmpeg files, both tools do the job.** `mat2 0.14.0` and `exiftool -all=`
each remove the GPS from all three loci and every identity tag with it. The
inherited expectation that a video scrubber misses the QuickTime location box did
not reproduce, and is not published as a result.

**On the AVFoundation file, `exiftool -all=` removes nothing at all.** Not "less
than we hoped" — it reports `1 image files unchanged` and the output is
**byte-identical** to the input, while the file still carries:

- `mvhd` and `tkhd` creation and modification times of **`2026-09-19 18:50:47`**, the
  local wall-clock second the file was written, and
- `Core Media Video` / `Core Media Audio` in the track handlers.

Both are plainly identifying, and exiftool lists 71 tags for the file, so it is not
that it cannot see them. They are simply not in a box it will write. A user who runs
the measuring stick over an Apple-muxed video and gets "unchanged" has been told the
file was already clean.

**`mat2` does remove them — by re-muxing.** Its output is 76 577 bytes against the
original's 76 224, the timestamps are zeroed, and the handler names come out
`VideoHandler` / `SoundHandler`: ffmpeg's. That is a real clean and it is also a
producer substitution, an F2-tier act rather than deletion, and it leaves
`udta/meta/hdlr` (`mdir`/`appl`) with an **empty `ilst`** — scaffolding a
never-tagged file does not have, since AVFoundation's original has no `udta` at all.
So mat2's cleaned file is distinguishable from one that was never tagged. That is an
A2 observation, not an A1 failure, and it is recorded as one.

This is the clearest statement of the gap MP4 F1 exists to fill: the two loci that
survive the measuring stick entirely are both ones we already planned to handle
(`mvhd`/`tkhd` times, `hdlr` names), and neither is a tag.

*(A first pass at the ffmpeg half of this counted `[System] FileName` as a surviving
GPS tag, because the test files are named `*_gps_*`. It is recorded because it is
the third time in this project a feature has measured something other than its name.
Chasing that artifact is what led to running the tools against AVFoundation's file
at all.)*

#### `claims()` today

All six files are **correctly declined** by the current dispatcher
(`UnsupportedFormatError: no handler for magic … ftyp`). The M4A handler's brand
check (`M4A `) and HEIC's (`heic`/`heix`/`mif1`) both pass on `isom` and `mp42`, so
the fail-closed property holds before MP4 exists and a test must keep it holding
after.

---

### 8.2 MP4 work items

#### W8 — the MP4 walker and `claims()`
Brand-based identification against the two ISOBMFF handlers already registered.
`isom` and `mp42` are MP4; `M4A ` and `heic`/`heix`/`mif1` are not. Register **after**
M4A and HEIC, as HEIC was, so the narrower claims run first.

Refuse rather than guess: fragmented MP4 (`moof`/`mfra` — the sample tables are not
in `moov` and the offset model is different), `co64` where we have never measured
one, DRM boxes, and any file whose `stco` entries do not land inside `mdat`.

#### W9 — MP4 F1: drop the metadata boxes, patch what moved
Drop `udta` whole (`loci`, `meta`/`keys`/`ilst` and all), zero `mvhd`/`tkhd`
timestamps, normalize `hdlr` names, drop top-level `free`. Then patch `stco` by the
delta of everything removed **before** `mdat` — which is zero in the common layout
and non-zero as soon as `free` goes.

The acceptance test is the M4A/HEIC lesson a third time: **decode**, do not parse.
A wrong `stco` yields a file that walks perfectly, reports the right duration, and
plays noise.

#### W10 — `Mp4Plugin` + the A2 channel, split two ways
M4A's precedent applies directly: an MP4 has a **muxer** (brand, compatible-brand
list, box order, `free` slack, timestamp scheme, `hdlr` vendor) and a **video
encoder** (the coded H.264 itself). Report them as separate channels rather than
averaging them, and keep the coded-video digest out of the categorical A2 channel
for the reason M4A did.

#### W11 — the corpus problem, again and differently
The six producers above are all software muxers on this machine. What is missing is
a **real phone video** — the `.MOV` an iPhone writes, which carries `mebx` timed
metadata tracks that none of these files contain (what they actually carry was
measured later, §12 — it is not motion data).
That is a leak surface this spike has not touched and must not be claimed as absent.

### 8.3 MP4 milestones

| # | Deliverable | Status |
|---|---|---|
| **M0** | Opening spike — the census above, six producers | ✅ (§5) |
| **M1** | Corpus decision (W11) | ✅ — **real phone video is out of scope** (§8). ffmpeg on the CI runner; AVFoundation stays macOS-only under the limit-#12 precedent |
| **M2** | Walker + `claims()` + refusal list (W8) | ✅ (§9) — and the fixture was wrong before the walker was |
| **M3** | MP4 F1 (W9) with a **decode** test | ✅ (§10) — and the decode test found a bug in *M4A*, shipped since Phase 2 |
| **M4** | `Mp4Plugin` + matrix + the two A2 channels (W10) | ✅ (§11) — five of nine channels close at F1; the four that remain are the F2 spec |
| **M5** | `limits.md` rows and the `FORMAT:mp4` block in `docs/formats.md` | ✅ — #37, #38, four residual notes, and the block the report guard demanded the moment the matrix landed |

### 8.4 M1 — the corpus decision, and what it costs us to say no

**Decided: no real phone video.** The corpus is the six software muxers of §5 —
ffmpeg (installable on the CI runner, so CI can build an MP4 from scratch) plus
the AVFoundation re-mux, which stays macOS-only under the same limit-#12
precedent that already covers Apple's AAC encoder and Microsoft Word.

**What that costs, stated rather than absorbed.** An iPhone `.MOV` carries
`mebx` timed-metadata tracks — sampled *per frame*, alongside the picture — and
nothing in this corpus has one. (This paragraph originally said they carry device
motion. That was an assumption; §12 measured five real files and found face
detection, scene illuminance, Live Photo info and a UUID instead.) So `mebx` is recorded as
**untested scope**, not as measured-absent. The distinction is the same one
HEIC's A3 cells make: a surface nobody ran has no verdict, and writing `fail`
or `pass` for it would be inventing a measurement either way.

The practical consequence for the build: MP4 F1 must **refuse** a file carrying
a track handler type it does not model rather than scrub around it, so a real
phone video meets a stated refusal instead of a silent partial clean. That is
the `UNCLASSIFIED` rule of the DOCX locus census in a different container, and
it is what keeps "out of scope" from quietly becoming "leaks".

---

### 8.5 M2 as built — the track model, and a fixture that proved nothing

`formats/mp4/walker.py` turns `moov` into the track table: per-track id, handler
kind, handler *name*, creation and modification times, and the chunk-offset table.
`formats/mp4/handler.py` identifies MP4s. Neither is in `default_dispatcher()`,
which is deliberate and is asserted by a test — DOCX's M8 precedent: a registered
handler is the tool advertising a format, and nothing scrubs an MP4 until M3.

#### Identification is harder than HEIC's, and the fix is a property rather than an order

HEIC could decide on the brand alone. MP4 cannot: `isom` and `mp42` are declared by
audio-only M4A files, which the M4A handler has claimed since Phase 2. Registration
order would "solve" it the way a coin toss solves a tie.

The real separation is in the predicates. M4A claims a file with a `soun` track and
**refuses** any file with a `vide` handler; MP4 claims a file with a `vide` track.
Those cannot both be true, so the handlers are mutually exclusive whatever order
they are registered in — and a test asserts **exactly one** of them claims each of
five shapes, not merely that both never do. That distinction matters: two handlers
that declined everything would satisfy "never both" perfectly.

#### The refusal list, and what each refusal is protecting

| refused | why |
|---|---|
| fragmented (`moof`/`mvex`/`mfra`/`sidx`) | samples live in fragment runs with their own offset model; every assumption in the walker is wrong for one |
| protection boxes (`sinf`/`pssh`/`schm`/`frma`/`senc`) | scrubbing encrypted media at best fails, at worst produces a file that no longer decrypts |
| `encv`/`enca`/… sample entries | the *other* way a file says it is encrypted — a top-level box scan walks straight past it |
| an unmodelled track handler | the M1 scope decision, enforced: `mebx` is what an iPhone writes and nobody here has measured one |
| more than one `mdat` | offsets would need patching per region, which is untested |
| a chunk offset outside `mdat` | either we misread the file or it is already broken; patching a pointer we do not understand is how M4A once produced a file that parsed and decoded to noise |
| `mvhd`/`tkhd` versions other than 0/1 | see below |
| a chunk table declaring more entries than it holds | reading it short would silently lose chunks |

The header-version refusal is the subtle one. `mvhd` and `tkhd` write their
timestamps as **32-bit in version 0 and 64-bit in version 1**, and reading a
version-1 box as version 0 does not fail — it returns the top half of a timestamp,
a plausible-looking wrong number. So the version is read, and an unmodelled one is
refused rather than defaulted.

#### The fixture was wrong before the walker was

`tests/scrub/mp4_corpus.py` hand-builds an MP4 byte by byte and imports nothing
from `src/` (asserted by walking its import statements, as `heic_corpus` is). The
first version was structurally valid, parsed correctly, and **carried no readable
metadata at all**: the metadata `hdlr` had a zeroed manufacturer field where a
reader needs `mdir`+`appl`, and `loci` stored longitude and latitude in the order
they are spoken rather than the order they are stored, one byte short of its
terminator. ExifTool reported no tags and no GPS for a file that was carrying both.

Every later "the tag is gone" assertion would have been green from the first day
and meaningless — the DOCX `b"w:rsid" not in out`-against-compressed-bytes mistake,
in a different container. A test now asserts an independent reader *finds* the
metadata the fixture claims to carry, so the removal tests M3 writes cannot pass
vacuously.

The fixture's honest limit is recorded with it: its sample entries carry no codec
configuration, so ffprobe reads both tracks and still says `missing mandatory
atoms`. That is the right trade for testing container structure and wrong for M3,
whose acceptance test must **decode**. M3 uses the ffmpeg corpus for that.

#### Checked against the M0 measurements, not just against itself

The walker reproduces §5 on the real corpus: ffmpeg files come out with zeroed
movie timestamps, generic `VideoHandler`/`SoundHandler` names and an 8-byte `free`
before `mdat`; the AVFoundation file comes out with a wall-clock `mvhd` time,
`Core Media Video`, and **nothing removable before `mdat`**. Running it over MAT2's
output of the AVFoundation file shows the re-mux from the inside — handlers renamed
to ffmpeg's, timestamps zeroed — which is the §5 benchmark claim reproduced through
our own code rather than through ExifTool's.

`co64` is now exercised by an actual file for the first time in this project. It
was always handled by `isobmff.shift_chunk_offsets()`, but the M4A tests only ever
asserted that *some* table was patched.

**Five mutations, five caught.** Deleting the unmodelled-handler refusal, the
chunk-bounds check, the fragmentation refusal, the version read, and widening
`claims()` to accept audio each turn the suite red on the test written for them.

---

### 8.6 M3 as built — F1, and the decode test finally earning its keep

`formats/mp4/f1.py` drops `udta` whole (both metadata containers, so the GPS goes
from `loci` as well as from the tag list), drops `meta`/`free`/`skip`/`uuid`, zeroes
the timestamps in **all three** header boxes, blanks the `hdlr` track names, and
patches `stco`/`co64` by however far the media actually moved. Registered in
dispatch, because F1 now exists. All six M0 producers scrub and **decode to
byte-identical video**, including the faststart layout where offsets must move and
the AVFoundation file where they must move by a number the old code computed as
zero.

#### The engine is shared, and the reason is not tidiness

M4A F1 already did four fifths of this. Copying it would have been the specific
thing `CLAUDE.md` forbids — "a missed copy is a leak" — and the proof arrived
immediately: M4A F1 had been written before anyone measured `hdlr` names, so it
dropped every tag and left `Core Media Audio` in any Apple-muxed file. Copying
would have reproduced that in MP4 and left it unfixed in M4A.

So the strip moved into `standards/isobmff.py` as `strip_and_repack()`, and both
formats call it. The `hdlr` blanking was added there once, which closed the leak in
the shipped format as a side effect of building the new one.

#### The bug the decode test found

This project has written "decode, do not parse" three times. This is the milestone
where it caught something, and what it caught had been shipped since Phase 2.

**AVFoundation writes `mdat` with the 64-bit largesize header** — `size == 1`, then
a 64-bit length — for a 17 KB box that would fit in 32 bits. The format permits it.
Our parser reads `header_len = 16` correctly. But the function measuring where the
media *was* **reconstructed** that header as 8 bytes rather than reading it, so:

```
predicted before = 36      real before = 44
predicted after  = 36      real after  = 36
delta            = 0       real delta  = -8
```

No chunk offset was patched. The output kept byte-identical media, parsed
perfectly, reported the right duration — and decoded to static, because every
pointer into the sound was 8 bytes out. Every check the project had asked whether
the media survived. None asked whether the file still knew where it was.

Two things now answer that question. The input offset is read from the parsed box
(`mdat_payload_offset_of_input`, named so it cannot be confused with the predictor).
And `strip_and_repack` **follows every chunk offset from input to output and
compares the bytes it lands on**, refusing rather than returning a file whose
pointers moved. A hand-built largesize fixture pins it with neither ffmpeg nor a Mac
in the loop.

#### Blanking a name introduces a constant, and the guard said so

With every `hdlr` name blanked, every output carries a byte-identical `hdlr` box —
the rest of it is the format's. The fingerprint guard failed, correctly. FLAC's rule
is to omit a constant rather than normalize it, and there was nothing to omit:
`hdlr` is required and its other fields are not ours.

So it is **declared**, the way DOCX declares its empty `_rels`: generated from the
same `blank_handler_name()` the strip calls, and bounded by a test asserting the
declaration carries no locus at all. Keeping the names instead would leak "a Mac
made this", which is strictly worse than limit #9's "something canonically rewrote
this".

Getting the declaration to *fit* took two corpus fixes rather than a wider
declaration, and this is DOCX M11's lesson verbatim — **when the guard reports
something too broad, suspect the corpus before widening the exclusion.** The guard
reports maximal runs, and M4A's diverse corpus varied sample rate and layout but
not:

- **language**, the field immediately *before* `hdlr` — every ffmpeg file writes
  `und`, so the run started there and no declaration of the box could cover it;
- **duration**, which sets the size of `minf`, immediately *after* — at one
  duration every file's `minf` landed in `0x0100–0x01ff`, so the high three bytes
  of its size were common too and welded the run from the other side. Sample rate,
  channel count and codec do not break this; length does.

With both varied, the run is exactly the box plus the two format-fixed zero bytes
each side, which is what the declaration says.

#### What this did not change, and one thing it argues for

Regenerating M4A's matrix changed **no verdict and no leaking feature**. The A2
plugin never measured handler names, so the channel was invisible to the
measurement as well as to ExifTool. A locus absent from both the scrubber and the
measurement is what the DOCX locus census exists to prevent, and it is an argument
for putting handler names into the ISOBMFF structural features when MP4's plugin
lands at M4.

---

### 8.7 M4 as built — two channels, and what F1 actually closed

`tests/harness/plugins/mp4.py` measures nine structural features; `tests/scrub/
e_mp4.py` runs them over four producers at `raw` and at F1; `gen_matrix_mp4.py`
publishes the matrix. A1@F1 **passes**, A2@F1 **fails with its channel named**, F2
and F3 are `not_tested` because nobody ran them.

#### The cell reports what closed, not only what leaked

| feature | raw | after F1 |
|---|:--:|:--:|
| `free_bytes` | separates | **closed** |
| `box_inventory` | separates | **closed** |
| `handler_names` | separates | **closed** |
| `mdat_header_form` | separates | **closed** |
| `timestamps_present` | separates | **closed** |
| `brand` | separates | open |
| `compatible_brands` | separates | open |
| `top_level_order` | separates | open |
| `moov_before_mdat` | separates | open |
| `size` (encoder channel) | separates | open |

Five of nine is the finding. A cell that listed only the four survivors would read
identically whether F1 had collapsed most of the channel or none of it — the
correction DOCX's F2 cell had to make — so the experiment measures `raw` first and
a test asserts the five actually close.

What survives is coherent rather than arbitrary: a bit-preserving tier deletes,
zeroes and re-lays-out, so it cannot change what the muxer **chose to be** — the
brand it stamped, the standards it claimed, the order it wrote its sections in, and
whether it put `moov` before `mdat`. Those four are the F2 specification, and the
`not_tested` cells say so in as many words.

#### Two features exist because Phase 4 found them and nothing else was looking

`handler_names` is the field `exiftool -all=` leaves while reporting a file
unchanged (§5), and M4A's plugin never measured it — which is why that format
leaked it from Phase 2 until M3. `mdat_header_form` is the largesize header behind
limit #35. Both are normalised by F1 **by construction**, and measuring them anyway
is the difference between "we handle this" and "we believe we handle this": a
future change that stops closing them fails a cell instead of passing quietly.

#### The peer set has a second implementation, not a second invocation

Three producers are ffmpeg configurations; `avfoundation` is a pass-through re-mux
through Apple's own muxer, so the coded video is identical and only the container
differs. Measuring a container channel against one program's options would mostly
measure the options. It is macOS-only, and the cell **names the peer set it had** —
a peer set that silently shrinks turns "we compared four producers" into a claim
about three.

#### The guard failed first, and the corpus was wrong again

The fingerprint guard reported a **389-byte run** — an entire `trak` box — as our
signature. The diverse corpus varied brand, layout, largesize form and track count
while every hand-built track kept the same duration, dimensions and timescale, so
all four outputs shared a byte-identical track. The guard was right: an undiverse
corpus cannot tell a constant the *tool* introduces from one the *corpus* never
varied.

Third time this project has hit it — DOCX M11, M4A during M3, and here — and the
fix is the same each time: **vary the thing, do not widen the declaration.** With
duration, dimensions, timescale, track ids, language and sample-entry format varied,
the run collapsed to the same `hdlr` neighbourhood M4A had, which is already
declared. The declaration's padding widened from two zero bytes each side to four,
because the run length differs by format (M4A carries two trailing zeros, MP4
three) and tuning a pad per format would itself be a constant that drifts.

#### One thing the report was saying that was not true

With MP4 and HEIC both F1-only, the capability table quoted a cost for modes that
do not exist — "Deep clean — costs you: nothing at all" for a file type with no
deep clean. Those cells now read *not built for this file type yet* (limit #38).
Found by rendering the section rather than by a test, which is its own small
argument for rendering the thing you ship.

---

### 8.8 Real files, after M5 — two defects the synthetic corpus could not show

M1 said no real phone video; the scope decision stands (re-confirmed 2026-09-24).
But the machine does hold real video — five iPhone `.MOV`s, thirteen WhatsApp
`.mp4`s and an editor export — and running the shipped F1 against them, read-only
and **structure only** (box types, key names, sizes; never a value), found two
things a hand-built corpus had no way to contain.

#### What an iPhone `.MOV` actually carries

Identical on all five files: brand `qt  `, `mdat` **before** `moov`, and a QuickTime
`meta` box — which, unlike ISO's, has **no version/flags word**, so the shared
walker's `FULL_CONTAINERS` rule would read its children four bytes off. `moov/meta`
holds the familiar keys (`location.ISO6709`, `location.accuracy.horizontal`, `make`,
`model`, `software`, `creationdate`); the video track's own `meta` holds the lens
model and 35 mm focal length. Then **six `mebx` timed-metadata tracks**, keyed:

| track | keys (`com.apple.quicktime.` prefix dropped) |
|---|---|
| 1 | `video-orientation` |
| 2 | `cinematic-audio` |
| 3 | `detected-face`, `.bounds`, `.face-id`, `.roll-angle`, `.yaw-angle` |
| 4 | `live-photo-info` |
| 5 | `milli-lux`, `scene-illuminance` |
| 6 | `segment-identifier`, `uuid` |

Their samples live in `mdat` between the video and audio chunks. So the HEIC §4.1
lesson applies directly: dropping the six `trak`s removes the *description* and
leaves every face box in the file with nothing left pointing at it. A real
implementation compacts `mdat` to the chunks the kept tracks reference and
re-derives every surviving offset — not a single delta. Plus `tref` cleanup
(`cdsc`/`cdep`/`rndr` point from these tracks at the video) and an `apac` spatial-
audio track, which is content and would stay. That is the work the scope decision
declines, now with its size known. None of it was motion data, which is what this
document had said.

#### Defect 1: every QuickTime file met "no handler", not the refusal

`MP4_BRANDS` never included `qt  `, so no `.MOV` was ever claimed and the `mebx`
refusal §8 promised was unreachable. The editor export — one `avc1` track, one
`mp4a` track, no timed metadata — failed identically. Fail-closed, so nothing
leaked; but "no handler for magic" reads as a broken tool. QuickTime is now claimed
**only so it can be refused by name**, before any parse (limit #40). A test asserts
the CLI says *QuickTime* and never says *no handler*.

#### Defect 2: the strip was a denylist, and WhatsApp's `beam` walked through it

Every WhatsApp video carries a proprietary 24-byte top-level `beam` box, before
`moov`. `strip_and_repack()` dropped `udta`/`meta`/`free`/`skip`/`uuid` and kept
everything else, so `beam` survived a scrub that reported success. It is a
WhatsApp constant rather than a per-file id (two distinct payloads across thirteen
files), but a box no list names passing through is exactly the failure the HEIC
auxiliary-image rule was written against — **decide by what is kept**. The shared
strip now holds the top level to `ftyp`/`moov`/`mdat` and drops everything else,
with one exception set that is **refused** instead: `moof`/`mfra`/`sidx`/`ssix`/
`styp`/`emsg`/`prft`, which carry or index media outside `moov`/`mdat`, so dropping
one deletes picture or sound while every `stco` check still passes. M4A goes through
the same function and had no fragment refusal of its own; it has one now.

Checked on all thirteen WhatsApp files with an independent oracle: `ffmpeg -c copy
-f streamhash` per stream is identical before and after, and `beam` sits *ahead of*
`mdat` there, so that comparison also re-proves the offset patch on real files.


---

# Camera RAW

### 8.9 RAW M0 — measured ground truth (the opening spike)

**Corpus.** Eight real camera files, one per container family, all CC0 from
raw.pixls.us (the archive LibRaw and darktable test against), sha256-pinned in
`tests/corpus/raw/manifest.txt` and fetched to `~/metadata-research/raw/` — never
committed (14–35 MB each, 184 MB total). Decode oracle: **LibRaw 0.22.1 via
`rawpy`**, independent of anything in `src/`.

| file | container | magic |
|---|---|---|
| Canon EOS R6 Mark III `.CR3` | **ISOBMFF** (brand `crx `) | `....ftypcrx ` |
| Canon EOS 80D `.CR2` | TIFF | `II*\0` + `CR` |
| Nikon D750 `.NEF` (lossy-compressed) | TIFF | `II*\0` |
| Sony ILCE-7M3 `.ARW` | TIFF | `II*\0` |
| Fujifilm X-T4 `.RAF` | Fuji's own: header → JPEG + TIFF-ish CFA | `FUJIFILMCCD-RAW ` |
| Apple iPhone 12 Pro `.DNG` (ProRAW) | TIFF/DNG | `MM\0*` |
| Olympus E-M10 IV `.ORF` | TIFF variant | `IIRO` |
| Panasonic DC-G9 `.RW2` | TIFF variant | `IIU\0` |

Today every one is declined and nothing is written — seven as "no handler for
magic", and **CR3 misidentified as MP4**: brand `crx ` lists `isom` compatible and
carries a `vide` track, so the MP4 handler claims it and then refuses its `CTMD`
track. Safe, but for the wrong stated reason; CR3 needs a brand check ahead of MP4,
exactly as HEIC's `heic` brand is checked.

#### The leak surface: serials, counters, owners — mostly inside MakerNotes

Identity loci found by ExifTool, per file (values are CC0 public samples):

- **Body serial**, up to three copies: EXIF `SerialNumber` (Canon, Fuji), MakerNote
  `SerialNumber` (Nikon, Olympus) and MakerNote `InternalSerialNumber` (Canon,
  Sony, Fuji, Olympus, Panasonic). Fuji's internal serial embeds a **manufacture
  date**; Panasonic's reads `(XEL) 2018:03:01 no. 0012`.
- **Lens serial** (Canon, Fuji, Olympus, Panasonic).
- **Shutter / image counters** — Nikon `ShutterCount` 5710, Canon `ImageCount`
  4243, Fuji `ImageCount` 241, Sony `ShutterCount`. A counter plus a model links
  photos to one body *across time* without any serial at all.
- **Owner / Artist** set in-camera: Canon 80D `OwnerName` + `Artist` (a name),
  Nikon `Artist` (an **email address**).
- **GPS with heading and speed** (iPhone), Canon's `ImageUniqueID`, and the
  usual dates, firmware and software versions everywhere.
- XMP on seven of eight — mostly `CreatorTool`/`CreateDate`, restating EXIF.

#### The finding the design turns on: a MakerNote cannot simply be dropped

Zeroing the whole EXIF MakerNote **in place** (same length, so no offset moves) and
decoding before/after with LibRaw:

| file | MakerNote | sensor data | rendered image |
|---|---|---|---|
| DNG (ProRAW) | 1.3 KB | identical | **identical** — DNG is self-describing |
| ARW | 37 KB | identical | **identical** |
| CR2 | 42 KB | identical | camera white balance lost → **94.7% of pixels change** |
| NEF | 148 KB | — | **does not decode** |
| ORF | 1.5 MB | — | **does not decode** |
| RW2 | none in ExifIFD | | maker data lives in IFD0 and in the preview's EXIF |

Then blanking **only the identity values** in place — EXIF owner/body/lens serial;
Canon MakerNote owner + internal serial; Nikon MakerNote serial and shutter count,
separately and together — leaves the render **pixel-identical** on every file. So
F1 is two strategies, not one: drop the MakerNote where it is decode-irrelevant
(DNG, ARW), and blank identity values **per vendor, in place** where it is not
(CR2, NEF, ORF). The Nikon case was the one expected to break: Nikon encrypts its
colour-balance block keyed on serial and shutter count. LibRaw's D750 white balance
survived both being blanked. **Not measured, and not claimed:** whether the
still-encrypted blocks — ciphertext produced under the original serial and count —
are themselves a linking channel.

In-place blanking has a cost to state rather than discover: a zeroed slot keeps
the original field's *length* (a 12-character serial leaves 12 zero bytes), and a
run of zeros where every camera writes text is the scrubber's own mark (limit #9's
species). Both are A2-side, both measurable in the RAW matrix.

#### The embedded previews: three of eight carry a second full metadata copy

Every file embeds 2–4 JPEG/TIFF renditions (thumbnail → full-size `JpgFromRaw`,
up to 5.4 MB). Extracting each and running ExifTool on it:

- **Fujifilm** — the 4.2 MB `PreviewImage` has its own EXIF *and* XMP: make,
  model, software, date, **body serial, lens serial**.
- **Panasonic** — the 716 KB `JpgFromRaw` has its own EXIF with the
  **internal serial and lens serial**.
- **iPhone ProRAW** — the 5.4 MB preview carries the **full GPS position,
  altitude and heading**. And its altitude differs from the main IFD's in the last
  decimal (…326 vs …328): the two copies were written independently, so a check
  that searches the output for the *removed bytes* misses the second copy. The
  residual check has to be structural (is there an EXIF segment in the preview at
  all?), not value-matching.
- The other five previews carry no EXIF — measured, not assumed.

This is the "item with its own EXIF" the plan predicted from HEIC, confirmed on
three vendors, with GPS on the one phone in the set.

#### Subject-derived images: ProRAW ships a semantic matte

The iPhone DNG's `SubIFD1` is a DNG 1.6 **`SemanticMask`** — a 2016×1512 JPEG,
`AuxiliaryImageType urn:com:apple:photo:2020:aux:semanticskymatte`, with its own
`XMP-semanticSegmentationMatte` namespace. Same family as HEIC limits #30/#31: a
judgement about the picture's content, not a camera fact. The HEIC decision (drop
mattes, keep the HDR gain map) is the default to carry over; it is a decision, so
it gets recorded, not inherited silently.

#### CR3 is MP4's problem again, inside a still photo

The shared ISOBMFF walker parses CR3 unmodified: `moov/uuid(85c0b687…)` holds
`CMT1`–`CMT4` (TIFF blocks: IFD0, ExifIFD, **MakerNote**, GPS) and `THMB`; a
top-level XMP `uuid` and a `PRVW` preview `uuid`; and **four tracks** — a full-size
JPEG, two raw images, and a **`CTMD` timed-metadata track** whose samples in
`mdat` carry the image counter. The walker treats `uuid` as a leaf, so the Canon
uuid needs a child walk; and the `CTMD` samples are §12's `mebx` problem — removing
the track means compacting `mdat`, not deleting a `trak`.

### 8.10 RAW work items and order

By dependency and by threat model, not by market share:

1. **Shared: surgical TIFF-IFD writing** in `standards/tiff_ifd.py` — blank a
   value in place, follow `SubIFDs` (0x014A) and the vendor MakerNote IFD layouts
   (Canon plain, Nikon `Nikon\0` + own TIFF header, Olympus `OLYMPUS\0II`), and
   remove or rewrite an embedded preview's APP1. Written once: CR2, NEF, ARW, ORF,
   RW2, DNG and CR3's `CMT` blocks are all TIFF underneath.
2. **DNG first** — open spec, self-describing (MakerNote measured droppable),
   carries the one GPS-bearing preview and the semantic matte, and **CI can
   hand-build one** from nothing, as HEIC's corpus was built.
3. **ARW** — MakerNote droppable; previews carry no EXIF.
4. **CR2, NEF, ORF** — per-vendor in-place blanking; decode is the acceptance test.
5. **RAF, RW2** — the two with a metadata-bearing preview; RAF's own container.
6. **CR3** — brand-before-MP4 identification, Canon-uuid walk, and `CTMD`
   removal with `mdat` compaction, which also unblocks the `.MOV` scope decision.

Decode oracle for every step: LibRaw's `raw_image` must be **bit-identical** (the
sensor data is the content F1 preserves) and the camera-WB render pixel-identical.

### 8.11 RAW milestones

| # | Deliverable | Status |
|---|---|---|
| **M0** | Opening spike — the census above, eight vendors | ✅ (§13) |
| **M1** | Corpus: sha256-pinned CC0 manifest + fetch; hand-built DNG for CI | 🔜 — manifest committed; hand-built DNG next |
| **M2** | Surgical TIFF-IFD writer (shared) | 🔜 |
| **M3** | DNG F1, decode-tested, matte decision recorded | 🔜 |
| **M4** | ARW, then CR2/NEF/ORF F1 | 🔜 |
| **M5** | RAF, RW2, CR3 | 🔜 |
| **M6** | `RawPlugin` + matrices + A2; `limits.md` rows | 🔜 |

---

## 9. M12 as built — the merge, the plugin, and the A2 cell

**What came from where.** `main`'s MP4 handler won: it scrubs QuickTime and rebuilds
`mdat` from the kept sample tables, so the stale copy (§5.4) is never written, where
the branch's strip copied `mdat` whole and refused every `.MOV`. From the branch, kept:

- **The M4A largesize fix** (its §8.6). `main`'s M4A F1 still reconstructed the
  `mdat` header as 8 bytes, so an Apple-muxed `.m4a` whose `mdat` uses the 16-byte
  form came out with every chunk offset 8 bytes wrong: it parsed, it had the right
  duration, and it decoded to noise. M4A now goes through the branch's shared
  `strip_and_repack()`, which reads the header and follows every chunk offset into
  the output before returning (limit #41).
- **The top-level keep-list and fragment refusal for M4A** (WhatsApp's `beam` box).
  `main`'s MP4 F1 already kept only `ftyp`/`moov`/`mdat`.
- **Two refusals `main` lacked:** encrypted sample entries (`encv`, `enca`, `encs`,
  `drms`, `drmi`) and protection boxes, read from every `stsd` entry because a box walk
  never reaches a `sinf` inside one; and `mvhd`/`tkhd`/`mdhd` versions other than 0
  and 1, which a version-0 read turns into a plausible wrong number. The version
  refusal lives in the shared `zero_timestamps()`, so M4A has it too.
- **One `hdlr` blank, shared.** `main` zeroed the reserved words (QuickTime's
  component manufacturer, `appl`) as well as the name; the branch blanked only the
  name. The shared `blank_handler_name()` now does both and MP4 F1 calls it instead
  of a private copy. ffmpeg writes those words as zero, so M4A's matrix did not move.
- **`Mp4Plugin`, E-MP4 and the matrix**, plus 49 tests ported to this handler
  (`test_mp4_f1.py`, `test_mp4_identification.py`). The walker's own model tests were
  dropped; the pointer-invariant tests cover every layout they measured.
- **The non-MP4 fixes** that landed on the branch: DOCX missing from the CI report,
  the README-claims guard, the determinism and idempotence matrices derived from
  dispatch (they had silently skipped DOCX and HEIC), the PDF cost cell that
  contradicted limit #17, and the PDF size limit (#40).

**Two defects the merge found in itself.** The branch's ISO fixture declared no
samples and pointed both tracks at one chunk. A strip that copies `mdat` never
noticed; a rebuild that writes only referenced samples emitted an empty `mdat` for
it, correctly. The fixture now gives each track its own slice and a data reference.
And `main`'s scrub report listed nothing for an ffmpeg-tagged MP4 but its handler
names: it read QuickTime `mdta` keys and `udta` ©-atoms, not the iTunes `ilst` or the
`loci` box ffmpeg writes. F1 removed them; the report did not say so. Both readers
came across from the branch.

**The A2 cell, re-measured on this F1.** Four producers (three ffmpeg, one
AVFoundation re-mux). At `raw`, nine muxer features plus size separate them. After
F1 the same five close as on the branch — `free_bytes`, `box_inventory`,
`handler_names`, `mdat_header_form`, `timestamps_present` — and the same four
survive: `brand`, `compatible_brands`, `top_level_order`, `moov_before_mdat`. A2@F1
**fails**, with those four named as the F2 specification. A1@F1 passes.

**A tenth feature, measured rather than patched blind.** `track_ids` (the surviving
IDs and `next_track_ID`) was §6's open item. It separates **no one** in this peer set,
raw or scrubbed: none of the four writes a timed-metadata track. On the QuickTime
build F1 leaves IDs 1–2 with `next_track_ID` 4, against 3 for a file that never had
the third track; on a real clip, 1–3 and 10. So the file still counts what was
removed. At F1 the brand already names Apple's format, so this is not the binding
leak; it becomes one the moment F2 normalises the brand, and it is written into the
F2 specification (limit #37) instead of into F1.

**The fingerprint guard failed once more, and the corpus was wrong once more.** The
fixture's new tables pushed every scrubbed `minf` into 0x100–0x1ff, so the high bytes
of its size word were common to all four outputs and the guard reported them beside
the declared `hdlr` box. One diverse shape now carries ninety samples; the guard
passes with the declaration unchanged.

**Not measured, and said so:** the peer set has no phone in it. An A2 comparison
across iPhone models and apps needs real clips from more than one device, which this
machine does not have.

### 9.1 The memory preflight (limit #38)

§6 stated the memory cost rather than capping it, and the user then chose between a
fixed size cap, a check against the machine, and doing nothing. The answer was the
check, measured first. A video scrub peaked at **3.1×** the file over the
interpreter's baseline: the input, the output grown in a `bytearray`, and the
`bytes()` copy of it. Assembling the output as a list of views joined once took it
to **2.07–2.13×** on the four real clips, which is input plus output and the floor
without streaming. M4A measured **4.04×** on an hour-long file (its shared strip
parses the whole file, serializes, and parses its output again to verify) and is
declared, not optimised.

The check runs before the file is read, since reading a file that does not fit is
the failure. The prefix alone gives only an upper bound — every ISOBMFF format
starts `....ftyp`, so it would price a video at M4A's 4.5× — so an ISOBMFF file is
identified from its **skeleton**: the top-level boxes except `mdat`, read with
seeks. Available memory on macOS is the kernel's own free percentage
(`kern.memorystatus_level`); summing `vm_stat`'s free and inactive pages read 8.9 GB
on a Mac the kernel called 50% free, and would have refused videos it handles
comfortably. A test scrubs a 40 MB file in a fresh process and holds each declared
factor (2.5×, 4.5×) between the measured peak and 1.5× of it.

---

## 10. Camera RAW: two surveys, one plan

The two surveys measured the same eight raw.pixls.us files — byte for byte, checked
against `tests/corpus/raw/manifest.txt` on merging — and agree where they overlap: serials live in the maker note, a zeroed maker note breaks Nikon and Canon,
and three of eight previews carry their own metadata. The branch's survey adds the
experiment `main`'s left implicit: **blanking only the identity values, in place,
keeps every render pixel-identical**, Nikon included, whose colour block is encrypted
under exactly those values (§8.9). `main`'s M15 adds the benchmark tools (§7.5).

One milestone numbering, `main`'s, with the branch's order inside it:

| # | Deliverable |
|---|---|
| **M16** ✅ (§10.1) | Shared surgical TIFF-IFD writing in `standards/tiff_ifd.py`: the `IIRO`/`IIU` magics, `SubIFDs`, the vendor maker-note layouts (Canon plain, Nikon `Nikon\0` with its own TIFF header, Olympus `OLYMPUS\0II`), values blanked in place and size-preserving; a hand-built DNG so CI can test it |
| **M17** | RAW F1, **DNG first** (open spec, the GPS-bearing preview, the semantic matte), then ARW, then CR2/NEF/ORF. Acceptance: LibRaw `raw_image` bit-identical **and** the camera-WB render pixel-identical, every identity value absent from the bytes |
| **M18** | RAF and RW2 (the two with a metadata-bearing preview, RAF its own container), then CR3 (brand ahead of MP4, Canon `uuid` walk, `CTMD` removal with `mdat` rebuilt as MP4 F1 does) |
| **M19** | `RawPlugin`, matrices, A2; `limits.md` rows |

The open decision stays open (§7.6): drop the previews or keep a cleaned copy, to be
settled with viewer behaviour measured.

### 10.1 M16 as built — the TIFF writer, and an owner's name ExifTool cannot see

`standards/tiff_ifd.py` gained what raw needs, all of it size-preserving: the Olympus
and Panasonic magics (opt-in, so a JPEG's EXIF is still held to 42), `SubIFDs` and
IFD-typed pointers, `makernote()` for five layouts, and three writes on a mutable
buffer -- blank a value, blank a span inside one, remove an entry from its directory
in place. The maker-note layouts were read off the real files, not a reference:

| maker | header | IFD at | offsets count from |
|---|---|---|---|
| Canon | none | +0 | the TIFF start |
| Sony | none (or `SONY DSC `) | +0 / +12 | the TIFF start |
| Nikon | `Nikon\0` + version, then a whole TIFF header | inner header's IFD0 | that inner header |
| Olympus | `OLYMPUS\0II\x03\0` | +12, serials in the `Equipment` sub-IFD | the maker note |
| Apple | `Apple iOS\0\0\x01MM` | +14, big-endian | the maker note |

`formats/raw/identity.py` holds what is removed, per maker, with the two strategies
the survey measured: blank fields in place (Canon, Nikon, Olympus) or remove the whole
maker note (Sony, Apple). It is not registered with dispatch; F1 is M17.

**On the five TIFF-family files** (DNG, CR2, NEF, ARW, ORF): length unchanged, LibRaw
sensor data bit-identical, camera-white-balance render pixel-identical, and every
identity value ExifTool read before is gone after -- from 28 changed bytes on the
Nikon to Sony's whole 37 KB note.

**And one it could not read.** A search of the BYTES after that still found the Canon
80D's owner name: a third copy, 1,150 bytes into `CameraInfo` (0x000D), a 1,536-byte
model-specific block ExifTool does not decode for that body -- so it reported the name
gone. A per-model offset table cannot keep up with Canon's bodies, so every text value
blanked is then searched for across the maker note and each further copy blanked:
text of four characters or more, never outside the maker note, decode-checked like
everything else. The rule that found it is the one §5.4 found the iPhone video's stale
GPS with: the measuring stick reads fields, so the residual check reads bytes.

**CI** runs on `tests/scrub/raw_corpus.py`: a TIFF writer importing nothing from
`src`, building a DNG LibRaw decodes in each of the five layouts plus an unknown one,
both byte orders, with the Canon-style hidden copy planted. 57 tests; two mutations
(no copy search, no Nikon serial) each fail by name on the fixture and on the real
files. Not yet: GPS, XMP, dates, the previews and their own EXIF (M17), RAF, RW2 and
CR3 (M18).

**Stated, not measured yet:** a blanked field keeps its length, so a run of zeros sits
where every camera writes text -- limit #9's species, for the RAW A2 cell to measure.
And Nikon's colour block stays enciphered under the ORIGINAL serial and count; whether
that ciphertext is itself a linking channel is untested.
