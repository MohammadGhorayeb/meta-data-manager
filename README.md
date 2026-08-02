# Irreversible Metadata Scrubber

[![QA](https://github.com/MohammadGhorayeb/meta-data-manager/actions/workflows/ci.yml/badge.svg)](https://github.com/MohammadGhorayeb/meta-data-manager/actions/workflows/ci.yml)

A tool that **irreversibly strips metadata from files of arbitrary type**, for privacy and anonymization. "Irreversible" means *forensic unrecoverability from the scrubbed file itself* — not merely deleting visible fields — against a medium-tier adversary (a journalist or amateur investigator using off-the-shelf forensic tools).

## Status
Phase 0 (harness + spine), **Phase 1 images complete** (JPEG F1/F2/F3, PNG F1/F2), and **Phase 2 audio underway** (MP3 F1 + F3, now with **cross-engine A2 evidence**). Fully implemented and measured, **150 tests passing**. Per-format Pareto matrices are generated under `tests/harness/results/`, and the scrubber-fingerprint guard passes. University implementation project; a working tool is the deliverable. A plain-language progress report is at `docs/p1_report.pdf`; a benchmark vs standard tools is at `docs/benchmark.md`.

### Results (measured, not assumed)
| Format | A1 metadata (F1/F2/F3) | A2 fingerprint (F1/F2/F3) |
|---|---|---|
| **JPEG** | pass / pass / pass | fail / fail / **pass** |
| **PNG**  | pass / pass / n-a  | fail / **pass** / n-a |
| **MP3**  | pass / n-a / pass  | fail / n-a / **pass** |

A1 (metadata) is defeated everywhere. A2 (encoder fingerprint) is defeated by re-encoding — **JPEG F3** (lossy), **PNG F2** (lossless, the standout result), or **MP3 F3** (lossy). Each format's fingerprint (JPEG DQT / PNG deflate / MP3 LAME-Xing contour) and F3 residuals (PRNU, primary quantization, MP3 generation loss + within-LAME-class anonymity) are documented, never silently claimed clean.

MP3 A2 is now measured **across encoder engines**, not just front-ends: the peer set includes [shine](https://github.com/toots/shine), a fixed-point encoder that is not libmp3lame and writes no Xing/LAME header at all. Two experiments back the A2@F3 cell — **E-LAME** in header space (all producers collapse to one canonical signature) and **E-ENGINE** in audio space, which asks the harder question the headers can't: with the header normalized, can a peer-corpus adversary still recover the source *engine* from the waveform?

The claim is deliberately **per sample-rate group**, and is verified in each group rather than in aggregate. 192 kbps CBR is illegal for MPEG-2 sample rates, so those files emit at 160 and form their own group; the group follows the source's sample rate, which is content we **preserve by design** — resampling would change the audio. Producer-anonymity holds inside every group, in structure (both rates pass E-LAME) and in sound (E-ENGINE: 44.1 kHz **0.94 → 0.50**, 22.05 kHz **1.00 → 0.61**, against chance 0.50). Scope stated honestly: spectral features; MDCT-domain classifiers untested. Every known limit lives in [`docs/limits.md`](docs/limits.md).

## Usage
```
python -m src.scrub --fidelity F1|F2|F3 <input> <output>
```
`F1` bit-preserving · `F2` lossless re-encode · `F3` lossy re-encode. The tool dispatches by magic number (not file extension) and is **fail-closed**: any case it cannot fully scrub raises and writes no output. Verify a result with `exiftool <output>`.

## Approach in one paragraph
Metadata hides in redundant copies across coexisting standards (EXIF, XMP, IPTC, ICC), in embedded thumbnails and previews, in stale history left in the bytes, and in structural side-channels (file size, encoder ordering, quantization tables) — so naive field-deletion leaks. Feasibility is expressed per format as a **Pareto matrix** over fidelity tiers (F1 bit-preserving, F2 lossless re-encode, F3 lossy re-encode) and adversary tiers (A1 no-reference, A2 peer-corpus fingerprinting, A3 original-copy differential), validated by **differential testing**: scrub two content-identical, metadata-different files and treat any non-noise diff as a leak.

## Build order
Depth-first **by dependency, not popularity** — leaf formats before the containers that embed them. A shared spine (test harness + reusable EXIF/XMP/ICC/ID3 modules + dispatch) is built first, then JPEG/PNG → MP3 → PDF/OOXML → MP4/HEIC/RAW → executables → long tail. See `docs/implementation_plan.md`.

## Near-future goals
Images are done + benchmarked ([`docs/benchmark.md`](docs/benchmark.md)), and **Phase 2 audio (MP3)** has F1 (bit-preserving tag strip — ID3v1/v2, APEv2, Lyrics3, embedded GPS-tagged album art, appended hitchhikers) and F3 (canonical 192 kbps CBR re-encode that erases the LAME/Xing encoder fingerprint). The cross-engine A2 residual is now **measured, not assumed** (see Results above). Next:
- **Close Phase 2 properly:** the plan's audio scope also includes **M4A** — which MAT2 refuses outright, so it is a gap to beat — and **FLAC/Vorbis comments**, both reusing the same tag logic. Plus a `docs/p2_audio_plan.md` to match `p1_images_plan.md`.
- **Phase 3 — documents (PDF, then OOXML/Word):** the RSID problem and PDF incremental-update history.
- **Optional:** the JPEG E4 residual bound (primary-quantization trace after F3).

## Far-future goals
The full arc toward the deliverable — one tool that irreversibly scrubs files of arbitrary type:
- **Phase 3 — documents (PDF, then OOXML/Word):** the **RSID** problem (revision-save IDs survive every surveyed tool, MAT2 included) and PDF incremental-update history.
- **Phase 4 — media & camera (MP4 → HEIC → RAW):** container atom/box surgery, and **PRNU** sensor-noise as a documented structural impossibility (removable only by destroying the image).
- **Phase 5 — executables**, then **Phase 6 — the long tail + whole-pipeline integration** (magic-number dispatch across every handler, recursive container scrubbing).
- **Deliverable:** a professional **funding proposal** — technical content backed by the measured Pareto matrices, plus a cost plan.
- **End goal:** forensic unrecoverability validated by differential testing at **every** duplicate locus, per format, against the medium-tier (A2) adversary.

## Repository layout
```
CLAUDE.md                  Always-loaded brief for Claude Code
docs/
  framework.md             Definitions, tiers, failure modes, conclusions, tools, citations
  limits.md                Every known limit in plain words — source of truth for the CI report
  implementation_plan.md   Phased build plan
  p1_images_plan.md        Phase 1 (images) work items, experiments, milestones
  p1_report.pdf            Plain-language progress & testing report
tests/
  harness/                 Phase 0 differential-testing harness + format plugins
  corpus/e3/               PNG/JPEG peer corpus for the A2 experiments (images git-ignored)
  scrub/                   Unit + harness tests, forensic-recovery guard, and the
                           A2 experiments: E3 (JPEG DQT), E-LAME (MP3 header space),
                           E-ENGINE (MP3 audio space, cross-engine)
src/scrub/
  cli.py  dispatch.py      Entry point + magic-number routing
  standards/               Shared modules: TIFF-IFD, XMP, ICC, IPTC-IIM (written once, reused)
  formats/jpeg/            JPEG handler: segments walker + f1/f2/f3
  formats/png/             PNG handler: chunk walker (CRC) + f1/f2
```

## Continuous integration — QA report on the Actions page
Every push to `main` and every pull request runs `.github/workflows/ci.yml`, which publishes a **rich, plain-language QA report right on the GitHub Actions run page** (Job Summary) — no external site needed. Anyone, including non-technical stakeholders, can read it:
- a **verdict** (badges + banner) and a 30-second benefit summary;
- a **pass/fail pie** and results grouped into plain-English areas (Metadata Removal, Picture Preserved, Made Untraceable, Cannot Be Recovered, File Stays Valid, Tool Behaviour);
- a per-format **capability table** and a mermaid diagram of how the fingerprint is defeated;
- a **"What we can't do yet — and how we solve it"** section, rendered straight from [`docs/limits.md`](docs/limits.md) — the single source of truth for every known limit, so the published report cannot drift from reality (a test enforces the wiring);
- the **before → after** scrub flow on real sample files.

The same report is posted (and updated) as a **PR comment**, and the scrubbed samples are uploaded as an artifact. A failing suite turns the check red (the report still publishes so failures are visible).

Generate the report locally:
```
python -m pytest -q --junit-xml=pytest-results.xml
python scripts/qa_report.py --junit pytest-results.xml --summary qa-summary.md
```

To require a green build before merge, add a branch-protection rule on `main` requiring the `qa` check.

## Working with Claude Code
`CLAUDE.md` carries the project definition, framework, build approach, working style, and tooling so each session starts on the same page. Keep it lean; put detail in `docs/`.
