# CLAUDE.md — Irreversible Metadata Scrubber

Persistent context for Claude Code, read at the start of every session. Keep this file lean; depth lives in `docs/` (linked below). When something here would otherwise be re-explained every session, it belongs here; when it's a long procedure or detail, it belongs in `docs/`.

## What this project is
A tool that **irreversibly strips metadata from files of arbitrary type**, for privacy/anonymization. University implementation project: a research phase plus an implementation phase, structured as a 10-step plan. **The deliverable is a working tool, not a paper** — research only matters insofar as it informs the build. A parallel deliverable is a professional funding proposal (technical content + cost plan).

## Core definition (do not drift from this)
- **"Irreversible" = forensic unrecoverability from the scrubbed file itself**, not mere field deletion.
- **Threat model is medium-tier (adversary A2):** a journalist or amateur investigator using off-the-shelf forensic tools. Not a nation-state.
- The original file persisting on disk is a **workflow** concern, not a property of the scrubbed file.

## Hard constraints (every format)
1. **Content preservation** — file stays functional and perceptually identical (same pixels / waveform / visible text / runtime behavior).
2. **Irrecoverability beyond deletion** — handle redundant copies (EXIF + XMP + IPTC + ICC), embedded thumbnails/previews, side-channel leaks (file size, structural fingerprints, encoder signatures), and the scrubber's own fingerprint.
3. **All three operating depths are in scope** — high-level libraries, format-spec parsing, raw binary/hex (magic numbers, offsets, integrity-field recomputation).

## Framework (full detail in `docs/framework.md`)
- **Fidelity tiers:** F1 bit-preserving · F2 lossless re-encode · F3 lossy re-encode.
- **Adversary tiers:** A1 no-reference · A2 peer-corpus structural fingerprinting · A3 original-copy differential.
- **Feasibility is a Pareto/achievability matrix per format** (which A×F combinations are reachable) — **never a binary claim**, and requires empirical validation across all duplicate loci before it is stated as fact.
- **Verification primitive = differential testing:** two files, identical content, different metadata → scrub both → any non-noise diff is a leak. Use an n-way same-source set to test A2.

## Build approach (full detail in `docs/implementation_plan.md`)
- **Depth-first by dependency, not by popularity.** Format families are recursive containers, so build **leaves before containers**.
- **Phase 0 first (the spine):** differential-testing harness + test corpus + shared standard modules (EXIF/TIFF-IFD, XMP, ICC, ID3) + scrubber-fingerprint guard + magic-number dispatch skeleton. Build this before any single format.
- Then: P1 images (JPEG → PNG) → P2 audio (MP3) → P3 documents (PDF → OOXML) → P4 media/camera (MP4 → HEIC → RAW) → P5 executables → P6 tail + integration.
- Shared modules are written **once** and called by every format handler — never copy-pasted per format, because a missed copy is a leak.

## Definition of "done" for any format
1. Differential test passes at the claimed (A,F) point across **all known loci** (including embedded thumbnail/preview), not just named tags.
2. Residuals are **documented** in the Pareto matrix (e.g. DQT / PRNU / Lame / RSID), never silently ignored.
3. Scrubber-fingerprint guard passes on the output: no producer/creator string, normalized padding, deterministic standard-conformant ordering, no mtime stamping.

## Working style (follow this)
- **Terse and directive.** Interpret intent and execute; don't over-clarify.
- **Produce or edit files; don't describe planned changes.**
- **Run shell commands one at a time**, not in large paste blocks.
- **Direct technical depth, no intro framing.**
- **Confirm ambiguous numbers before locking them in**, then move on.
- **No claims without empirical validation.** Feasibility percentages require confirming every duplicate locus is cleared, not just named — before they go anywhere, especially the proposal.
- **Keep `README.md` current as milestones land** — the Status line, the results matrix, and the Near/Far-future goals sections. The user wants the README to always reflect reality.
- **Every measured limit goes in `docs/limits.md`, in plain words, when it is found.** It is the source of truth the CI QA report renders, so an undocumented limit is invisible to everyone reading the report. Narrow an overclaim there rather than leaving it standing.

## Environment
- macOS, zsh, Homebrew.
- Language: **Python** (assumed — change this line if not). The metadata ecosystem (pikepdf, oletools, Pillow, libheif/LibRaw bindings, binary-parsing libs) is Python-native.
- Research artifacts dir: `~/metadata-research/` with `step2/originals|scrubbed|reports/`.

## Tools (by role — detail in `docs/framework.md`)
- **Measuring stick (ground truth):** ExifTool.
- **Benchmark scrubbers (beat these):** MAT2, the whitelist approach.
- **Implementation candidates:** pikepdf/qpdf, Ghostscript (PDF); oletools/olefile, LibreOffice headless (Office/CFB); AtomicParsley, MP4Box, MediaInfo (multimedia); jhead, LibRaw, libheif (image/RAW); Exiv2.
- Licenses matter for bundle-vs-subprocess: MAT2 LGPL · ExifTool Artistic/GPL · qpdf Apache-2.0 · exiv2 GPL · oletools BSD.

## Repo map
- `CLAUDE.md` — this file (always-loaded brief).
- `docs/framework.md` — definitions, tiers, failure modes, conclusions, tool roles, standards, citations.
- `docs/implementation_plan.md` — the phased build plan.
- `tests/harness/` — Phase 0 differential-testing harness.
- `tests/corpus/` — Phase 0 test corpus (synthetic injection + real samples).
- `src/` — the scrubber: shared modules + dispatch + per-format handlers.

## Known conclusions (don't re-derive these)
- No surveyed tool achieves A2 resistance at F2 for any Tier-1 format; A2 without F3 re-encode is an open problem.
- JPEG A2-resistance generally requires F3 — in-place DQT quantization tables reveal the encoder (Kornblum).
- OOXML RSIDs survive every surveyed scrubber including MAT2 (Müller).
- The Lame tag in MP3 audio frames can't be removed without re-encoding.
- MP3 A2 is closed at F3 **across engines, per sample-rate group**, measured twice: E-LAME (header space — producers collapse to one canonical signature; both 44.1 and 22.05 kHz pass separately) and E-ENGINE (audio space — engine classification 0.94→0.50 at 44.1 kHz, 1.00→0.61 at 22.05 kHz, chance 0.50). Peer set includes `shineenc`, a non-libmp3lame engine. The grouping is deliberate: 192 kbps CBR is illegal for MPEG-2 rates so those emit at 160, and the source sample rate is content we preserve — never resample to collapse groups. Untested scope: MDCT-domain classifiers. Don't re-run these to re-establish the result; extend them.
- PDF has no published tool achieving A2 at F2.
- PRNU sensor noise survives F1/F2 and no metadata pipeline can remove it (Lukáš) — a structural impossibility; document it as such rather than treating it as a bug.
- FLAC reaches **A2 at F2 losslessly** (E-FLAC: block size/framing/vendor separate producers at F1, collapse at F2, audio bit-identical via STREAMINFO MD5). FLAC + PNG are the free-A2 formats; JPEG/MP3 need a lossy tier because their fingerprint is inside the compressed content. FLAC F1 emits **no** padding and **no** empty Vorbis comment — both were tool constants the fingerprint guard caught; don't "normalize" a constant you can simply omit.
