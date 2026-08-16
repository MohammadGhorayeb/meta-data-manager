# Irreversible Metadata Scrubber

[![CI](https://github.com/MohammadGhorayeb/meta-data-manager/actions/workflows/ci.yml/badge.svg)](https://github.com/MohammadGhorayeb/meta-data-manager/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11_|_3.14-3776ab?logo=python&logoColor=white)](https://github.com/MohammadGhorayeb/meta-data-manager/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/coverage-84%25-green)](https://github.com/MohammadGhorayeb/meta-data-manager/actions/workflows/ci.yml)
[![Formats](https://img.shields.io/badge/formats-JPEG_·_PNG_·_MP3_·_FLAC_·_M4A-1971c2)](tests/harness/results/)
[![Threat model](https://img.shields.io/badge/threat_model-medium--tier_(A2)-5f3dc4)](docs/framework.md)
[![Honest limits](https://img.shields.io/badge/honest_limits-documented-orange)](docs/limits.md)

A tool that **irreversibly strips metadata from files of arbitrary type**, for privacy and anonymization. "Irreversible" means *forensic unrecoverability from the scrubbed file itself* — not merely deleting visible fields — against a medium-tier adversary (a journalist or amateur investigator using off-the-shelf forensic tools).

## Status
Phase 0 (harness + spine), **Phase 1 images complete** (JPEG F1/F2/F3, PNG F1/F2), and **Phase 2 audio complete** — MP3 (F1 + F3, cross-engine A2 evidence), FLAC (F1 + F2, lossless A2) and M4A (F1/F2/F3, the format MAT2 refuses). **Phase 3 documents is underway**: **PDF F1 is implemented** — our own walker, serializer and content tokenizer, recursive into embedded images — and the revision-history experiment is measured (see below). Fully implemented and measured, **278 tests passing**. Per-format Pareto matrices are generated under `tests/harness/results/`, and the scrubber-fingerprint guard passes. University implementation project; a working tool is the deliverable. A plain-language progress report is at `docs/p1_report.pdf`; a benchmark vs standard tools is at `docs/benchmark.md`.

### Results (measured, not assumed)
| Format | A1 metadata (F1/F2/F3) | A2 fingerprint (F1/F2/F3) |
|---|---|---|
| **JPEG** | pass / pass / pass | fail / fail / **pass** |
| **PNG**  | pass / pass / n-a  | fail / **pass** / n-a |
| **MP3**  | pass / n-a / pass  | fail / n-a / **pass** |
| **FLAC** | pass / pass / n-a  | fail / **pass** / n-a |
| **M4A**  | pass / pass / pass | fail / fail\* / fail\* |
| **PDF**  | pass / — / —       | — / — / — (F2/F3 not built yet) |

A1 (metadata) is defeated everywhere. A2 (encoder fingerprint) is defeated by re-encoding — and **twice now at no quality cost at all**: **PNG F2** and **FLAC F2** are lossless, returning bit-identical pixels and bit-identical audio, while **JPEG F3** and **MP3 F3** must spend a lossy generation to get there. The dividing line is whether the fingerprint lives in a separable layer (PNG's deflate, FLAC's metadata blocks and framing) or is baked into the compressed content itself (JPEG's DQT, MP3's LAME/Xing contour). Each format's fingerprint and residuals (PRNU, primary quantization, MP3 generation loss, within-class anonymity) are documented, never silently claimed clean.

\* **M4A carries two producers in one file** — the muxer that arranged the boxes and the encoder that made the coded audio — and the cells say which one leaked rather than averaging them. F2 fully normalizes the muxer layout (box order, brand, `free` slack) while copying the audio stream, so it is lossless. F3 additionally collapses producers whose first-generation audio was identical to **byte-identical output**; what survives is the *primary-encoding trace* left by a source that was itself encoded differently (192 kbps vs 128) — and it is now narrowed to a single channel, **file size** (~1.4%), since the audio itself no longer betrays it. That trace has since been measured (**E-M4A-AUDIO**): the source's own quality setting reads at **0.88** on untouched and copy-tier files and **falls to chance (0.50) after F3**. Engine identity was *not* separable even on untouched files (0.56) — ffmpeg's AAC and Apple's are near-twins spectrally — so that question is reported as unanswerable with this corpus rather than claimed either way. **MAT2 refuses M4A entirely** (measured, 0.14.0), so every cell here is capability the benchmark alternative lacks.

MP3 A2 is now measured **across encoder engines**, not just front-ends: the peer set includes [shine](https://github.com/toots/shine), a fixed-point encoder that is not libmp3lame and writes no Xing/LAME header at all. Two experiments back the A2@F3 cell — **E-LAME** in header space (all producers collapse to one canonical signature) and **E-ENGINE** in audio space, which asks the harder question the headers can't: with the header normalized, can a peer-corpus adversary still recover the source *engine* from the waveform?

The claim is deliberately **per sample-rate group**, and is verified in each group rather than in aggregate. 192 kbps CBR is illegal for MPEG-2 sample rates, so those files emit at 160 and form their own group; the group follows the source's sample rate, which is content we **preserve by design** — resampling would change the audio. Producer-anonymity holds inside every group, in structure (both rates pass E-LAME) and in sound (E-ENGINE: 44.1 kHz **0.89 → 0.53**, 22.05 kHz **0.94 → 0.58**, against chance 0.50; neither significantly above chance at n=36). Scope stated honestly: spectral features; MDCT-domain classifiers untested. Every known limit lives in [`docs/limits.md`](docs/limits.md).

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
Images are done + benchmarked ([`docs/benchmark.md`](docs/benchmark.md)), and **Phase 2 audio is closed**: **MP3** (tag strip + canonical re-encode with cross-engine A2 evidence), **FLAC** (lossless A2, bit-identical audio) and **M4A** (ISOBMFF box surgery with sample-table patching — the format MAT2 refuses). Its ledger of what audio can and cannot be made anonymous — six solved results, four structural limits, four gaps named rather than glossed — is at the end of [`docs/p2_audio_plan.md`](docs/p2_audio_plan.md). Next:
- **Phase 3 — documents (PDF, then OOXML/Word)** is under way: the plan is [`docs/p3_documents_plan.md`](docs/p3_documents_plan.md). Two named targets — PDF **incremental-update history** (old revisions left in the file: the famous redaction-disclosure mode) and **OOXML RSIDs**, which survive every surveyed tool including MAT2. The A2-at-F2 frontier for PDF is the open research question, to be characterised rather than asserted.

  **Measured (M1).** A PDF is edited by *appending*, so "deleted" text is still in the file; truncate at an earlier `%%EOF` and the earlier draft opens as a document. On a 3-revision corpus: **ExifTool `-all=` adds a fourth revision and removes nothing** (it warns about this itself), while **MAT2** destroys the document's history by re-rendering but then clears `/Info` by appending an update of its own — leaving `cairo 1.18.4` and a **wall-clock timestamp with the operator's UTC offset** one revision down, reproduced on both its paths and on a real 295 KB file. Details in [`docs/benchmark.md`](docs/benchmark.md) Evidence 6.

  **Built (M2): PDF F1.** A rewrite from the object graph through **our own serializer** — pikepdf reads, we emit the bytes, because qpdf's constant header comment and its inherited `/ID[0]` both fail this project's own fingerprint guard. History dies by construction: only what the catalog reaches is written, so superseded objects are never emitted and there is no deletion pass to get wrong. Recursive from day one — an embedded JPEG goes through the Phase 1 handler and its entropy-coded scan comes back byte-identical while its EXIF and thumbnail are gone. Clears `/Info`, XMP *wherever it hangs* (including off an image), `/ID`, page `/Thumb`, `/PieceInfo`, `/SpiderInfo`, OCG `/CreatorInfo`, annotation authors, and **inline images** — a `BI … ID … EI` JPEG that no object-graph walk can see. Verified on six real producers (Skia, LibreOffice, Quartz, cairo, ExifTool output, synthetic), text byte-identical throughout. Encrypted, signed, XFA, attachment-bearing and hybrid-xref files are **refused**, not half-scrubbed.

  Redaction — text under a black box in a single-revision file — is a *different* leak that none of this touches, and is kept explicitly separate ([limit #13](docs/limits.md)).
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
  p2_audio_plan.md         Phase 2 (audio) plan + closing ledger: solved vs cannot
  p3_documents_plan.md     Phase 3 (documents) plan — PDF then OOXML
  p1_report.pdf            Plain-language progress & testing report
tests/
  harness/                 Phase 0 differential-testing harness + format plugins
  corpus/e3/               PNG/JPEG peer corpus for the A2 experiments (images git-ignored)
  scrub/                   Unit + harness tests, forensic-recovery guard, and the
                           A2 experiments: E3 (JPEG DQT), E-LAME (MP3 header space),
                           E-ENGINE (MP3 audio space, cross-engine),
                           E-PDF-HISTORY (PDF revision rollback)
src/scrub/
  cli.py  dispatch.py      Entry point + magic-number routing
  standards/               Shared modules: TIFF-IFD, XMP, ICC, IPTC-IIM (written once, reused)
  formats/jpeg/            JPEG handler: segments walker + f1/f2/f3
  formats/png/             PNG handler: chunk walker (CRC) + f1/f2
  formats/mp3/             MP3 handler: frame/tag walker + f1/f3
  formats/flac/            FLAC handler: metadata-block walker + f1/f2
  formats/m4a/             M4A handler: ISOBMFF box surgery + f1/f2/f3
  formats/pdf/             PDF handler: structure walker + our own serializer +
                           content-stream tokenizer + recursive f1
scripts/
  qa_report.py             Builds the plain-language CI report (run page + PR comment)
  check_evidence.py        Re-measures the Pareto matrices; fails if a published claim drifted
  coverage_gate.py         Gates tested code; names untested files instead of averaging them away
  scrub_flow_report.py     Before → after evidence on real sample files
  benchmark.py             Comparison vs ExifTool / MAT2 / jpegtran → docs/benchmark.md
pyproject.toml             Tool config only (pytest, ruff, coverage) — not a package
requirements-dev.txt       Everything CI installs: runtime deps + ruff + coverage
```

## Continuous integration — QA report on the Actions page
Every push to `main` and every pull request runs `.github/workflows/ci.yml`. It has six clearly-named jobs:

| Job | What it does |
|---|---|
| **Code quality** | `ruff` over the codebase (findings appear as inline annotations on the diff) plus `actionlint` over the workflow itself. Formatting drift is reported but never gates. |
| **Tests (Python 3.11–3.14)** | The full suite on every supported interpreter, `fail-fast: false`. 3.11 is a hard floor — numpy/scipy/PyWavelets all require it. |
| **Coverage** | Combines the coverage data from all four versions (each leg writes its own file, so a branch reachable on only one interpreter still counts), then gates via [`scripts/coverage_gate.py`](scripts/coverage_gate.py). It holds *code the tests actually reach* to a floor, and lists files nothing reaches by name instead of letting them drag the average down — so landing a new format's scaffolding never fails the build, and never hides either. |
| **Published results still true** | Re-measures every Pareto matrix from scratch and fails if a published verdict no longer matches — see [`scripts/check_evidence.py`](scripts/check_evidence.py). Enforces *no claims without empirical validation* mechanically. |
| **QA report** | Builds and publishes the report. Runs even when everything else failed. |
| **CI** | The single required status check. Everything above is allowed to finish so the report always publishes; this job is the only one that turns the run red. |

The report itself renders **on the run page** (Job Summary) — no external site — and is written for a non-technical reader:
- a **verdict** (badges + one-sentence banner) and a five-second stage table with ✅/❌ and timings;
- a **Mermaid pipeline diagram** with every box coloured by what actually happened, so a red box shows exactly where it broke;
- a **coverage bar**, overall and file by file;
- results grouped into plain-English areas (*Hidden data removed*, *Picture and sound preserved*, *Made untraceable*, *Cannot be recovered*, *File stays valid*, *The tool behaves*), with a ✅-grid across Python versions;
- **what failed**, in readable words, with the file and line — also annotated inline on the pull-request diff;
- a per-format **capability table** read from the measured Pareto matrices, so a format the tool cannot yet do can never be claimed;
- the **"What we can't do yet"** section rendered straight from [`docs/limits.md`](docs/limits.md) — the single source of truth for every known limit, so the published report cannot drift from reality (tests enforce the wiring, and a missing document is reported loudly rather than rendering as a clean bill of health);
- the **before → after** scrub flow on real sample files.

A condensed version is posted and updated in place as a **PR comment**. Test results, coverage (including browsable HTML), the report and the scrubbed samples are all uploaded as artifacts.

Run the same checks locally:
```
python -m pip install -r requirements-dev.txt
ruff check .
python -m pytest -q --cov --junit-xml=pytest-results.xml
python -m coverage json -o coverage.json
python scripts/coverage_gate.py --coverage-json coverage.json --floor 82
python scripts/check_evidence.py --out evidence.json
python scripts/qa_report.py --junit pytest-results.xml --coverage-json coverage.json \
    --coverage-gate-json coverage-gate.json --evidence-json evidence.json \
    --summary qa-summary.md
```

To require a green build before merge, add a branch-protection rule on `main` requiring the **`CI`** check — that one job covers all the others.

## Working with Claude Code
`CLAUDE.md` carries the project definition, framework, build approach, working style, and tooling so each session starts on the same page. Keep it lean; put detail in `docs/`.
