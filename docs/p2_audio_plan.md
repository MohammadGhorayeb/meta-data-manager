# Phase 2 Plan — Audio Leaves: MP3 → FLAC → M4A

The plan P1 got and P2 never did. Written mid-phase, so it records what is already
built as well as what is left — the gap it is meant to close is exactly the one that
opened without it: MP3 shipped, the README moved on to "next phase", and the audio
scope the implementation plan actually called for (M4A, FLAC) quietly fell out of
view.

Everything plugs into the Phase 0 harness: the scrubber is a `Scrubber` behind the
`scrub {in} {out} --fidelity` CLI, format knowledge lands as a `FormatPlugin`, and
"done" is a validated Pareto matrix in `tests/harness/results/`, never inspection.

**Pareto targets:**
- **MP3** — A1 at F1; A2 requires F3 (the LAME/Xing signature lives in the audio
  frames and cannot be removed without re-encoding). F2 is not applicable: there is
  no lossless re-encode of a lossy format.
- **FLAC** — A1 at F1, and **A2 at F2**. FLAC is lossless, so re-encoding is
  content-preserving by construction; this is the audio counterpart of the PNG
  result, and the only place in audio where A2 should be reachable without quality
  loss.
- **M4A** — A1 at F1. A2 at F2 for ALAC (lossless, so repacking is free) and at F3
  for AAC. M4A is ISOBMFF, so its atom walker is the **Phase 4 spine** for MP4 and
  HEIC — build it as a shared module, not an M4A private.

---

## 0. Ground truth: what exists

Built and measured (W1–W4, M1–M2 below): the MP3 walker, F1, F3, the `MP3Plugin`,
the E-LAME and E-ENGINE experiments, and the MP3 Pareto matrix.

Not built: FLAC and M4A — both named in the implementation plan's Phase 2, neither
started. MAT2 **refuses M4A outright**, which makes it the one format in this phase
where we can beat the benchmark rather than match it.

---

## 1. Work items (dependency order)

### W1 — MP3 structure walker ✅
`src/scrub/formats/mp3/walker.py`. Accounts for every byte into named regions so F1
can drop the metadata ones and keep audio verbatim: ID3v2 front tag, MPEG frames
located by header *size* (never by scanning for `FF FB`, which appears inside tags),
the Xing/Info + LAME header frame, and the EOF trailer stack (APEv2 / Lyrics3 /
ID3v1 / appended ID3v2.4 footer, any order) plus appended "hitchhiker" bytes.
Fails closed on anything it cannot account for.

### W2 — MP3 F1 (bit-preserving) ✅
Drops every tag region, keeps audio frames byte-identical. Residual check re-walks
the output and treats anything but a clean audio-only stream as a leak.

### W3 — MP3 F3 (canonical re-encode) ✅
Decode → re-encode through one locked LAME setting (192 kbps CBR, joint stereo,
`-q 2`), so the producer is laundered. Perceptual gate = band-limited aligned
cross-correlation. Two gate defects found and fixed by the experiments below, both
of the same shape: the gate compared bands that contained no audio, and refused files
it had scrubbed correctly.

### W4 — `MP3Plugin` ✅
`structural_features` exposes the A2 channel: LAME version string, Xing magic,
bitrate histogram, channel mode, **sample rate** (added late — without it the peer
set could not see the anonymity grouping at all).

### W5 — FLAC walker + F1 ✅
`src/scrub/formats/flac/`. FLAC is the friendliest structure in the phase: a
`fLaC` magic, then a chain of METADATA_BLOCKs (`STREAMINFO`, `PADDING`, `APPLICATION`,
`SEEKTABLE`, `VORBIS_COMMENT`, `CUESHEET`, `PICTURE`), each with a 4-byte header
carrying a last-block flag, and then audio frames. Metadata is cleanly separated from
audio, so **F1 is bit-preserving by construction**.
- Keep: `STREAMINFO` (mandatory, but its MD5 of the unencoded audio is a content
  property, not metadata).
- Drop: `VORBIS_COMMENT` entirely — including the **vendor string**, which names the
  encoder and is a producer fingerprint sitting in a metadata block, not the audio.
  (Planned as "emit a canonical empty comment block"; changed after the fingerprint
  guard flagged that block's fixed 12 bytes as OUR signature. Omitting beats
  normalising whenever a constant can simply not be written.)
- Drop: `PICTURE` (cover art — the nested-image locus; art carries its own EXIF/GPS,
  so it is recursion into P1, not a byte blob to delete blindly), `APPLICATION`,
  `CUESHEET`.
- Drop: `PADDING` — its *size* is a producer tell (encoders pad differently), and a
  fixed replacement is just our own tell, so none is emitted at all.
- Also handle: ID3v2 **prepended to a FLAC file**, which is out of spec but common in
  the wild, and bytes appended after the last audio frame.

### W6 — FLAC F2 (lossless re-encode) ✅
The A2 defense that costs nothing. Re-encode via `flac` at one locked setting so
block sizes, frame headers and padding all come from one producer. Content identity
is exact: decoded PCM must be **bit-identical**, not perceptually close — anything
less is a bug, not a trade-off. This is the audio analogue of the PNG F2 result.

### W7 — `FlacPlugin` ✅
`structural_features`: vendor string, block-type inventory and order, padding size,
min/max block size and frame size from `STREAMINFO`, seektable presence. These are
the A2 channel — the FLAC equivalent of the DQT.

### W8 — ISOBMFF atom walker (shared) ✅
`src/scrub/standards/isobmff.py`, **not** under `formats/m4a/`. A box tree walker:
4-byte size + 4-byte type, `size==1` → 64-bit largesize, `size==0` → to EOF, `uuid`
boxes, and full-box version/flags. Phase 4's MP4 and HEIC handlers reuse this
unchanged — the whole reason M4A is worth doing before documents.

### W9 — M4A F1 ✅
Metadata loci, all of which must go: `moov/udta` (including `©nam`-style iTunes
atoms under `meta/ilst`), `moov/meta`, free/skip atoms (which can hide arbitrary
data), `mvhd`/`tkhd`/`mdhd` **creation and modification timestamps**, cover art in
`ilst/covr` (recursion into P1 again), and any trailing bytes. Timestamps are the
easy-to-miss one: they are structural fields, not tags, so a tag-oriented scrubber
leaves them.

### W10 — M4A F2/F3 ✅
F2 = repack the container (rebuild the box tree in canonical order, normalise
`free` space) leaving the audio samples untouched — valid for both AAC and ALAC since
neither is re-encoded. F3 = re-encode AAC through one locked setting, mirroring MP3.

---

## 2. Experiments

| # | Question | Status |
|---|---|---|
| **E-LAME** | Does the MP3 header identify the producer, and does F3 erase it? | ✅ Yes / yes, per sample-rate group, cross-engine |
| **E-ENGINE** | With the header normalised, does the *sound* still identify the source engine? | ✅ No — 0.94→0.50 at 44.1 kHz, 1.00→0.61 at 22.05 kHz |
| **E-FLAC** | Do FLAC block layout, padding size and vendor string identify the encoder, and does F2 erase them **losslessly**? | ✅ Yes / yes — fingerprint at F1, gone at F2, audio bit-identical |
| **E-M4A** | Do atom ordering and `free`-space layout identify the muxer, and does the coded audio identify the encoder? | ✅ Yes to both at F1. F2 normalises the layout losslessly; F3 collapses same-source producers to byte-identical output |
| **E-M4A-AUDIO** | With the container normalised, is the source's own encode still readable from the sound? | ✅ No — the source quality setting reads at 0.88 unscrubbed and falls to chance after F3. Engine identity was *not* separable even unscrubbed (0.67), so that question stays open rather than being claimed |

E-ENGINE's design rules apply to all of these and were learned the hard way: compare
**engines, not front-ends** (two front-ends of one engine produce identical output,
so separating them measures nothing); use **content that carries the signal** (a pure
tone has no high-band energy, so no encoder trace can exist in it); and assert the
**controls** — if the attack cannot identify the producer of an *unscrubbed* file,
its failure on a scrubbed one proves nothing.

---

## 3. Milestones

| # | Deliverable | Status |
|---|---|---|
| **M1** | MP3 F1 + walker + plugin + dispatch | ✅ |
| **M2** | MP3 F3, E-LAME, matrix | ✅ |
| **M3** | Cross-engine evidence (shineenc), E-ENGINE, per-group verification | ✅ |
| **M4** | FLAC F1 + F2 + plugin + E-FLAC + matrix — **A2 at F2, losslessly** | ✅ |
| **M5** | ISOBMFF shared walker + M4A F1 + plugin + matrix | ✅ |
| **M6** | M4A F2/F3 + E-M4A; benchmark row vs MAT2's refusal | ✅ (benchmark row still to write up) |

Phase 2 is **done** when all three formats have validated matrices, every residual is
recorded in `docs/limits.md`, and the benchmark table shows the M4A gap closed.

---

## 4. Known limits carried out of this phase

Recorded in `docs/limits.md`, not here — that file is the single source of truth and
the CI report renders it. In brief: MP3 anonymity is per sample-rate group; Layer II
and free-format MP3s are refused; VBRI headers are unhandled and untested;
MDCT-domain classifiers are untested; and microphone/room response and mains hum are
structural impossibilities, the audio counterpart of PRNU.

---

## 5. Phase 2 closing ledger — what audio can and cannot be made anonymous

Every row below is measured, not argued. Where a claim is bounded, the bound is
stated; where a question could not be answered with the tools available, it says so
instead of resolving itself in our favour.

### Solved

| # | Result | Evidence |
|---|---|---|
| 1 | **All named metadata is removed, at every tier, for all three formats** — tags, embedded cover art (a nested image carrying its own EXIF/GPS), ghost tags at the end of the file, appended hitchhiker data, and the structural timestamps that are not tags at all. | A1 passes in every matrix cell |
| 2 | **FLAC reaches A2 for free** — the encoder's framing fingerprint is erased with **bit-identical audio**. Alongside PNG, one of only two formats where anonymity costs nothing. | E-FLAC; STREAMINFO MD5 |
| 3 | **MP3 reaches A2 across genuinely different engines**, per sample-rate group, in header space *and* in the sound: an engine classifier goes 0.94 → chance at 44.1 kHz, 1.00 → 0.61 at 22.05 kHz. | E-LAME, E-ENGINE, peer set incl. `shineenc` |
| 4 | **M4A is handled at all** — the format MAT2 refuses outright. Container layout is normalised losslessly at F2; F3 collapses same-source producers to byte-identical output. | E-M4A |
| 5 | **The M4A audio residual is cleared within what we can measure**: the source's own quality setting reads at 0.88 on untouched files and falls to chance after F3. | E-M4A-AUDIO |
| 6 | **The tool leaves no fingerprint of its own.** Three candidates were caught by the guard during this phase and removed rather than excused: FLAC's fixed padding, FLAC's fixed empty comment block, and ffmpeg's vendor string leaking through re-encodes. | fingerprint guard, all matrices |

### Cannot be solved, and why

| # | Limit | Why it is not a bug |
|---|---|---|
| 1 | **The microphone, the room, and mains hum.** A recording's acoustic fingerprint and the electrical hum that can date it live in the sound itself. | Removing them means destroying the audio. The exact counterpart of PRNU in photographs — a structural impossibility, not an unfinished feature (limit #5) |
| 2 | **A lossy format cannot reach A2 without a lossy re-encode.** MP3's signature is inside the compressed audio, so F2 does not exist for it in any useful sense. | Only FLAC and PNG keep their fingerprint in a separable layer. This is a property of the formats, not of the tool (limit #1) |
| 3 | **Anonymity is within a class, never invisibility.** A scrubbed file is recognisably scrubbed, and MP3's class is per sample rate because we refuse to resample. | Hiding *that* a file was cleaned is a different problem from hiding *where it came from*; only the second is promised (limits #2, #9) |
| 4 | **What the first encoder discarded is gone and stays gone.** Re-encoding cannot restore it, so a trace of the original encode persists in principle. | The audio analogue of Sorell's primary-quantization residual. Measured as *not recoverable* by our classifier — but "we could not find it" is not "it is not there" (limits #4, #10) |

### Not attempted — named so the gap is visible

| # | Gap | Status |
|---|---|---|
| 1 | **MDCT-domain classifiers.** Published forensics inspects the encoder's internal number-crunching; our attacks work on the frequency profile. A stronger adversary than ours may well succeed. | Untested (limit #6) |
| 2 | **VBRI headers** (Fraunhofer's alternative to Xing) are unhandled and untested. | Files using them are refused, not half-cleaned (limit #7) |
| 3 | **Layer II audio and free-format bitrates** are refused outright. | Fail-closed: safe, but the user gets no output (limit #3) |
| 4 | **The AAC cross-engine result cannot be reproduced in CI** — it needs Apple's encoder, which is macOS-only. | Reported as "not measured" there rather than inheriting the number (limit #12) |

**Phase 2 is closed.** Every cell in the three matrices is measured, every residual
is in `docs/limits.md`, and the two questions this phase could not answer are named
above rather than left implicit.
