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
| **M12** | `Mp4Plugin`, matrix, and the A2 cell: the muxer channel (brand, top-level order, handler names, box inventory), with the encoder channel reported separately, as M4A does | |
| **M13** | F2 candidate: SEI user-data removal, measured for decode identity | |
| **M14** | `limits.md` rows, the benchmark row, README | 🟡 limits #34–#39 and README done; the benchmark row waits for M12 |
| **M15** | Camera RAW opening survey: eight makers, before any code | ✅ (§7) |

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
