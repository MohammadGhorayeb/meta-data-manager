# Implementation Plan — Depth-First by Dependency

## Strategy
Depth-first per format family (solve one family to its Pareto frontier, then move on), but families are **sequenced by dependency, not popularity**. Build a shared horizontal spine once, then go vertical on each family in an order where every format's embedded dependencies already exist.

## Sequencing principle
Format families are not independent. The container-recursion failure mode (#4) means document/media containers *embed* leaf formats:
- PDF embeds JPEG (DCTDecode), PNG, ICC, fonts, arbitrary file attachments.
- DOCX/OOXML is a ZIP of XML + embedded JPEG/PNG/EMF + `docProps/thumbnail.jpeg`.
- MP4 / HEIC (both ISOBMFF) embed cover art / thumbnail images + EXIF.
- RAW embeds a full-size JPEG preview + thumbnail, each with its own EXIF.

So a "complete" PDF scrubber at F2 needs working image-stream scrubbing. Build leaves first; containers then reuse them. (PDF-first only works if you rasterize everything to F3 — Dangerzone's approach — which abandons the F2 path you specifically want to characterize.)

Second axis: the metadata *standards* are cross-format. EXIF/TIFF-IFD, XMP, ICC, ID3 each appear in many formats. They're shared libraries, not per-format code.

Resulting order: spine → image leaves → audio leaf → document containers (PDF, then Office) → media/camera containers → executables → long tail.

---

## Phase 0 — Spine (build once, before any format)
The reusable foundation every later phase calls.

- **Differential-testing harness.** Generate content-identical / metadata-variant file pairs, scrub both, diff, classify each diff as noise vs leak. This is the "done" gate for every format. Build first.
- **Test corpus.** Per-format files with metadata injected into *every* known locus (EXIF, XMP, IPTC, ICC, embedded thumbnail), so validation proves all duplicates cleared — not just named tags.
- **Shared standard modules:** EXIF/TIFF-IFD parser+stripper, XMP handler, ICC handler, ID3 (v1 / v2.3 / v2.4) handler. Reused across phases.
- **Scrubber-fingerprint guard.** Test + checklist that the tool emits no identity: no producer/creator string, normalized padding, no mtime stamping, deterministic ordering. Wired into the harness so every format is checked against it.
- **Dispatch skeleton.** Magic-number detection → route to format handler. Each phase registers its handler here.

Done when: harness runs end-to-end on a stub format and the fingerprint guard correctly fails a deliberately-leaky stub.

---

## Phase 1 — Image leaves: JPEG → PNG
- **JPEG.** EXIF, XMP, IPTC, ICC, JFIF, Adobe APP14; **embedded thumbnail with its own EXIF**; DQT quantization-table fingerprint (Kornblum) and primary-quant survival after recompression (Sorell) → these push A2 to F3. Depths: lib for tags, spec-parse for segment walk, hex for marker/segment surgery.
- **PNG.** tEXt/zTXt/iTXt, tIME, iCCP (ICC), eXIf; chunk ordering + compression-filter fingerprint. Simpler — good second.
- **Pareto target:** characterize F1/F2 for A1; document that A2 needs F3 (encoder/DQT leak).

Done when: differential test shows no non-noise diff at the claimed (A,F) point across all loci incl. thumbnail; residuals (DQT, PRNU) recorded in matrix.

---

## Phase 2 — Audio leaf: MP3 (+ M4A / FLAC tags)
- ID3v1 + ID3v2 coexistence, APE tags, padding; **Lame tag in audio frames** (can't remove without re-encode → F2 fails, F3 needed).
- Fold in M4A (MAT2 *refused* this — a gap to beat) and FLAC/Vorbis comments; tag logic reuses across them.

Done when: both tag systems cleared, frame-level Lame leak documented as F3-only, M4A handled where MAT2 wasn't.

---

## Phase 3 — Document containers: PDF → OOXML
Leaves now exist; recursion is tractable. Honors your "PDF then Office" instinct.

- **PDF.** Info dict; XMP (reuse P0); **incremental-update history** (old object versions left in the bytes — must rewrite, not append); embedded file attachments; **embedded image streams (reuse P1 on DCTDecode)**; font-subsetting fingerprints; glyph-position redaction leak (Bland); object-ordering / producer fingerprint. This is your flagged A2-at-F2 open problem — push it, document the frontier honestly.
- **OOXML/DOCX.** ZIP **local-header vs central-directory timestamps must match** after rewrite; `core.xml` / `app.xml` / `custom.xml`; **RSIDs** (survive every surveyed scrubber incl. MAT2 — Müller); `people.xml`; comments + tracked changes; embedded media (reuse P1); `docProps/thumbnail.jpeg`. Robustness target: the macOS AppleDouble ZIP crash from Step 2. Legacy `.doc`/CFB via olefile if in scope.

Done when: PDF passes differential test at claimed point with embedded images clean and no incremental history recoverable; DOCX clears RSIDs and matches ZIP timestamps; both survive the corpus without crashing.

---

## Phase 4 — Media & camera containers: MP4 → HEIC → RAW
- **MP4 (ISOBMFF).** `udta` + `©nam` atoms repeated across tracks; atom ordering (encoder fingerprint); cover art (reuse P1); free/skip atoms; `mvhd` timestamps.
- **HEIC (ISOBMFF).** Reuses the MP4 atom parser; EXIF item (reuse P1), embedded thumbnail item, multiple image items. Sits at the MP4∩JPEG intersection — both deps satisfied by now.
- **RAW (CR2/CR3, NEF, ARW, DNG).** Per-vendor; **embedded full-size JPEG preview + thumbnail, each with EXIF** (major leak — reuse P1); maker notes; **PRNU sensor noise** documented as a structural impossibility (Lukáš) — survives all fidelity tiers, no metadata pipeline removes it. Hardest — do last.

Done when: ISOBMFF parser shared between MP4/HEIC; RAW previews scrubbed; PRNU recorded as an impossibility result in the matrix, not a bug.

---

## Phase 5 — Executables: PE → ELF → Mach-O
Separate universe — no standard-module reuse.

- **PE.** Version-info resource; **Rich Header at 0x80** (VS toolchain fingerprint, Webb); PDB path in debug dir; timestamps; section padding.
- **ELF.** `.comment`, build-id, symbol/debug sections, section names.
- **Mach-O.** `LC_UUID`, code-signature remnants, load-command strings.
- Hard constraint: runtime behavior identical after scrub. Mostly raw/spec-parse depth.

Done when: each survives execution post-scrub; Rich Header / build-id / LC_UUID cleared, validated differentially against toolchain-variant builds.

---

## Phase 6 — Tier-2 long tail + integration
- SVG, TIFF, GIF, WebP, EPUB, etc. — mostly reuse standard modules.
- Final dispatch/CLI, batch mode, plugin-registration polish.
- The per-format Pareto matrix, now empirically filled, becomes the tool's spec sheet.
- **Decoy metadata (`--decoy`), default off** — deliberate *spoofing* rather than
  removal, on an axis orthogonal to F1/F2/F3: the fidelity tiers trade content, this
  trades truthfulness. Motivation is the one attack our A1/A2/A3 ladder does not
  model — where **the fact of scrubbing is itself the leak** (a document leaked from
  a pool of three, and only one person's files are conspicuously clean). Removal is
  correct for A2, which asks *which producer*, not *was this cleaned*.
  Cross-format by nature: PDF `/Info` dates, JPEG `DateTimeOriginal`, MP3 `TDRC`,
  MP4 `creation_time` all pose the identical question, so it is one shared module,
  not a per-handler feature.
  Design constraints, all of them learned the hard way elsewhere in this project:
  - **A random date is worse than no date.** A spoofed value must be consistent with
    the file's own technology floor — PDF version header (1.5 = object streams,
    2003; 1.7 = 2006), filter set, font technology, the embedded JPEG's encoder
    traits that Phase 1 can already classify. A 2004 date on a file using object
    streams is falsified by reading the header, and it upgrades "this was cleaned"
    into "this was cleaned *and* forged". So: compute the earliest compatible date,
    then draw above that floor.
  - **The generator is a fingerprint.** A fixed `+00'00'` offset, uniform
    second-precision, or a uniform-over-a-decade draw where real documents cluster
    on weekday business hours *is* a producer signature across n files from one
    user — the same class of tool constant that caught FLAC's empty Vorbis comment
    and pikepdf's `/Producer`. The fingerprint guard must see this mode's output.
  - **Where a timestamp is mandatory, join a crowd instead of inventing one.** ZIP
    local file headers carry a required MS-DOS timestamp that cannot be omitted, so
    DOCX (P3 M6) must pick a constant: `1980-01-01 00:00`, the ZIP epoch and the
    reproducible-builds convention, which carries zero per-file entropy. That is the
    principle in miniature and it is the opposite of randomising.
  - **Ships only behind `E-DECOY`** — a classifier separating our synthetic dates
    from genuine ones, with valid controls. Without that it is an unvalidated claim,
    which this project does not publish.
  - **`docs/limits.md` states the dual-use property plainly** when the mode lands:
    convincing false provenance on a document has an obvious second use in forgery.
  Not needed for the file's own bytes today: the scrubber has **no wall-clock call at
  all** (`grep -rn "utime|datetime.now|time.time()|strftime" src/scrub/` is empty), so
  outputs carry no date to begin with. Only the output's filesystem mtime says "now",
  which is a workflow concern and a one-line `touch -t` for anyone who cares.

---

## Cross-cutting invariants (hold every phase)
- **Differential test is the definition of "done."** No format ships on inspection alone.
- **Three depths as the leak demands:** high-level lib for plain fields, spec-parse for structure/ordering, raw hex for integrity-field recomputation and fingerprint surgery.
- **Scrubber-fingerprint hygiene** checked on every output.
- **Living Pareto matrix:** each format's reachable (A,F) points + documented residuals (DQT, PRNU, Lame, RSID frontier) updated as you go. Doubles as proposal evidence.
