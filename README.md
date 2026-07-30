# Irreversible Metadata Scrubber

[![QA](https://github.com/MohammadGhorayeb/meta-data-manager/actions/workflows/ci.yml/badge.svg)](https://github.com/MohammadGhorayeb/meta-data-manager/actions/workflows/ci.yml)
[![Quality Dashboard](https://img.shields.io/badge/QA%20dashboard-live-2563eb)](https://mohammadghorayeb.github.io/meta-data-manager/)

A tool that **irreversibly strips metadata from files of arbitrary type**, for privacy and anonymization. "Irreversible" means *forensic unrecoverability from the scrubbed file itself* — not merely deleting visible fields — against a medium-tier adversary (a journalist or amateur investigator using off-the-shelf forensic tools).

## Status
Phase 0 (harness + spine) and **Phase 1 images complete**: JPEG (F1/F2/F3) and PNG (F1/F2) fully implemented and measured, **129 tests passing**. Both Pareto matrices are generated under `tests/harness/results/`, and the scrubber-fingerprint guard passes at F1 and F2. University implementation project; a working tool is the deliverable. A plain-language progress report is at `docs/p1_report.pdf`.

### Results (measured, not assumed)
| Format | A1 metadata (F1/F2/F3) | A2 fingerprint (F1/F2/F3) |
|---|---|---|
| **JPEG** | pass / pass / pass | fail / fail / **pass** |
| **PNG**  | pass / pass / n-a  | fail / **pass** / n-a |

A1 (metadata) is defeated everywhere. A2 (encoder fingerprint) is defeated by re-compression — **JPEG F3** (lossy) or **PNG F2** (lossless, the standout result). The DQT producer-fingerprint result (experiment E3) and the F3 residuals (PRNU, primary quantization) are documented, never silently claimed clean.

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
Image loose ends are now closed — the PNG Pareto matrix is generated, and the JPEG fingerprint guard passes at F1 **and** F2 (format-mandatory / standard-libjpeg structure folded into `mandatory_constants`). Next:
- **Phase 2 — audio (MP3):** strip ID3v1/ID3v2 and APEv2 tags; document the **Lame encoder tag** residual that only a re-encode can remove (the audio analogue of the JPEG DQT).
- **Benchmark scaffold:** run MAT2 and ExifTool over the same corpus so every result has a side-by-side comparison — the evidence base for the funding proposal.
- **Optional:** the JPEG E4 residual bound (how much primary-quantization trace survives F3).

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
  implementation_plan.md   Phased build plan
  p1_images_plan.md        Phase 1 (images) work items, experiments, milestones
  p1_report.pdf            Plain-language progress & testing report
tests/
  harness/                 Phase 0 differential-testing harness + format plugins
  corpus/e3/               PNG/JPEG peer corpus for the A2 experiments (images git-ignored)
  scrub/                   Unit + harness tests, the E3 DQT experiment, forensic-recovery guard
src/scrub/
  cli.py  dispatch.py      Entry point + magic-number routing
  standards/               Shared modules: TIFF-IFD, XMP, ICC, IPTC-IIM (written once, reused)
  formats/jpeg/            JPEG handler: segments walker + f1/f2/f3
  formats/png/             PNG handler: chunk walker (CRC) + f1/f2
```

## Continuous integration — QA dashboard
Every push to `main` and every pull request runs `.github/workflows/ci.yml`. It produces a **plain-language QA dashboard** anyone (including non-technical stakeholders) can read:
- **Live dashboard** published to GitHub Pages — a styled page with a pass/fail banner, category cards (Metadata Removal, Picture Preserved, Made Untraceable, Cannot Be Recovered, File Stays Valid, Tool Behaviour), a capability table, and the before/after scrub flow.
- **Job Summary** — the same dashboard rendered on each run's page.
- **PR comment** — the summary is posted (and updated) on every pull request, so reviewers see results inline.
- **Artifacts** — the scrubbed sample files, downloadable for inspection.

Under the hood each run installs the tools (exiftool, jpegtran, ffmpeg), runs the **full test suite** (regression guard), and groups the results into the plain-English areas above. A failing suite turns the check red.

Generate the dashboard locally:
```
python -m pytest -q --junit-xml=pytest-results.xml
python scripts/qa_report.py --junit pytest-results.xml --html qa-site/index.html
```

**One-time setup for the live dashboard:** enable Pages (Settings → Pages → Source: **GitHub Actions**). To require a green build before merge, add a branch-protection rule on `main` requiring the `qa` check.

## Working with Claude Code
`CLAUDE.md` carries the project definition, framework, build approach, working style, and tooling so each session starts on the same page. Keep it lean; put detail in `docs/`.
