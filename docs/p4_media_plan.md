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
| **M5** | `HeicPlugin` + matrix + the A2 channel (W4) | 🔜 |
| **M6** | `limits.md` rows | ✅ — #30 (the segmentation blob), #31 (the auxiliary-image trade), plus the residuals block |

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

What remains is container structure and the colour profiles, whose headers are
sanitised and whose tag data is not — limit #14, in a third format.

Phase 4 continues to MP4 (cheap, reuses `stco` patching as-is) and then RAW, whose
embedded full-size previews are the same "item with its own EXIF" shape HEIC
establishes here.
