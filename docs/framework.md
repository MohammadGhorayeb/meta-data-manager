# Framework — Irreversible Metadata Scrubbing

The stable technical reference. `CLAUDE.md` summarizes this; depth lives here.

## Core thesis
Naive scrubbers fail because metadata lives in **multiple redundant loci** across ~14 format families. Four recurring failure modes:
1. **Redundant copies** — the same metadata duplicated across coexisting standards (EXIF + XMP + IPTC + ICC) or container locations.
2. **Stale history left in the bytes** — old object versions, revision history, prior state not actually removed.
3. **Side-channel / structural fingerprints** — file size, chunk/atom ordering, encoder signatures, default field values.
4. **Incomplete container recursion** — failing to descend into embedded media and nested files.

## Definitions
- **Irreversible** = forensic unrecoverability *from the scrubbed file itself*, not field deletion. A field whose value is blanked but whose presence/length/position still leaks is not scrubbed.
- **Threat model (A2)** = a journalist or amateur investigator with off-the-shelf forensic tools. Medium-tier, not a nation-state.
- The original file persisting on disk is a **workflow** concern, outside the scrubbed file's properties.

## Fidelity tiers
- **F1 — bit-preserving:** metadata removed without re-encoding content; content bytes untouched.
- **F2 — lossless re-encode:** content re-encoded losslessly (decoded and re-serialized) to normalize structure.
- **F3 — lossy re-encode:** content re-encoded lossily, sacrificing perceptual fidelity to destroy structural fingerprints.

## Adversary tiers
- **A1 — no-reference:** adversary inspects the single scrubbed file.
- **A2 — peer-corpus structural fingerprinting:** adversary has many files from the same source and looks for a shared structural signature (encoder ordering, DQT tables, padding habits).
- **A3 — original-copy differential:** adversary has the original and diffs against the scrubbed copy.

## Feasibility = a Pareto/achievability matrix
For each format, state which **A×F** combinations are reachable — never a binary "feasible/infeasible." A matrix entry may only be claimed after **empirical validation** that *all* duplicate loci are cleared, not just the named tags.

## Verification primitive — differential testing
Two files with **identical content but different metadata** → scrub both → any **non-noise** diff is a leak.
- **F1/F2:** outputs should be byte-identical; localize any differing bytes to a structure (which chunk/atom/segment leaked).
- **F3:** outputs won't be byte-identical; the oracle becomes semantic — confirm perceptual identity, then check that no extracted field or structural fingerprint *correlates with* the input variant (ExifTool as ground truth).
- **A2 specifically:** use the **n-way** version — a set of same-source files checked for a shared surviving fingerprint, not just a pair.

The test corpus must inject metadata into *every* known locus so validation proves all duplicates cleared. Build it both ways: synthetic injection (known ground truth, systematic coverage) and real-world samples (realistic, surfaces loci you wouldn't synthesize).

## Redundant-copy loci scrubbers commonly miss
- **JPEG** — embedded thumbnail with its own EXIF.
- **PDF** — incremental-update history retaining old object versions.
- **OOXML** — revision history in `word/document.xml` and `word/people.xml`, plus RSIDs.
- **MP4** — `udta` + `©nam` atoms repeated across tracks.
- **MP3** — ID3v1 + ID3v2 coexistence.
- **ZIP/OOXML** — local-header vs central-directory timestamps that must match after a rewrite.

## Side channels surviving content scrubbing
File-size fingerprints, encoder-specific chunk/atom ordering, default field values signaling the originating tool, padding sizes, default compression params, font-subsetting patterns, ICC profile presence/identity, line-ending and encoding fingerprints.

## The scrubber-fingerprint problem (itself an A2 vector)
The scrubber can sign its own output: recomputed integrity values done a characteristic way, padding habits, default choices, mtime stamping, version strings written to creator/producer fields. This replaces the source's fingerprint with the tool's. Watch MAT2, ExifTool `-all=`, qpdf `--linearize`, ImageMagick `-strip`. Guard: emit no identity, normalize/zero timestamps, deterministic *standard-conformant* ordering, padding matching the format's common case — and run the differential/peer test on the tool's *own* outputs.

## Format priority (Tier-1, by real-world privacy impact)
JPEG → PNG → PDF → DOCX/OOXML → MP4 → MP3 → HEIC → camera RAW → executables, then a Tier-2 long tail for breadth.

## Key conclusions
- No surveyed tool achieves A2 resistance at **F2** for any Tier-1 format. Achieving A2 without F3 re-encoding is an **open problem**.
- **JPEG** A2-resistance generally *requires* F3: in-place quantization tables (DQT, `0xFFDB`) are a device/software fingerprint and survive EXIF stripping (Kornblum); the primary quantization matrix can leak even after double compression (Sorell).
- **OOXML** RSIDs survive every surveyed scrubber including MAT2 (Müller).
- The **Lame tag** in MP3 audio frames can't be removed without re-encoding.
- **PDF** has no published tool achieving A2 at F2.
- **PRNU** sensor noise is a per-camera fingerprint in pixel data that survives F1/F2; no metadata pipeline can remove it (Lukáš). A structural impossibility — document it, don't chase it.

## Tool roles (categorize by role, not as a flat list)
- **Measuring sticks (ground truth):** ExifTool.
- **Benchmark scrubbers (the bar to beat):** MAT2, the whitelist approach.
- **Implementation candidates:**
  - General: ExifTool, MAT2, Exiv2.
  - PDF: qpdf/pikepdf, Ghostscript, Dangerzone.
  - Office/CFB: oletools/olefile, Open XML SDK, LibreOffice headless.
  - Multimedia: AtomicParsley, MP4Box, mkvpropedit, MediaInfo.
  - Image/RAW: jhead, LibRaw, libheif.
- **Licenses (bundle-vs-subprocess decision):** MAT2 LGPL · ExifTool Artistic/GPL · qpdf Apache-2.0 · exiv2 GPL · oletools BSD.

## Governing standards
EXIF (CIPA DC-008), XMP (ISO 16684), IPTC IIM + IPTC Photo Metadata, ICC profile spec, ID3 v1/v2.3/v2.4, APE, Vorbis comments, PDF (ISO 32000-1/-2), ISOBMFF (ISO/IEC 14496-12), ZIP/OOXML (ISO/IEC 29500), MS-CFB, PE/COFF, ELF, Mach-O, EBML/Matroska, RIFF.

## Academic anchors
- **Voisin 2012** (MAT toolkit, arXiv:1212.3648) — whitelist approach: parse, keep only required fields, discard the rest. Correct for A1; can't handle structural fingerprinting or embedded-media recursion.
- **Nikitin et al. 2019 PoPETs** (PURBs) — formalizes plaintext-header and *length* leakage; file length and structural ordering are themselves metadata.
- **Bland et al. 2023 PoPETs** ("Story Beyond the Eye") — glyph positions break PDF text redaction; A3 provenance from a reference copy.
- **Müller et al. 2020 WOOT** — OOXML RSID leakage.
- **Lukáš / Fridrich / Goljan 2006 IEEE TIFS** — PRNU sensor noise; structural impossibility result.
- **Kornblum 2008 DFRWS** — JPEG quantization tables as device/software fingerprints; survive EXIF stripping.
- **Sorell 2010** — primary quantization matrix can leak after double compression; F3 not always sufficient against determined A2.
- **Webb 2018 DIMVA** — PE Rich Header (offset `0x80`) fingerprints the Visual Studio toolchain; invisible to standard version-info stripping.

## Real-world leak incidents (threat-model motivation)
EXIF-GPS deanonymization (e.g. the McAfee/Vice photo); OOXML author/path leaks in legal filings; PDF redaction failures (TSA, NSA, McCabe report); build-ID / PDB-path exposure in malware attribution.
