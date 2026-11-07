# November plan — production readiness (7–30 November 2026)

The September schedule ([`schedule_report.pdf`](schedule_report.pdf)) ends with one
month of hardening and a **release candidate on 30 November**: robustness,
performance, packaging, documentation and release. Every feature promised for the
31 October freeze is in (19 published matrices, 1,764 tests, 80 limits). This plan
turns the four schedule headings into milestones with a measurable "done", and adds
what an audit of the repo on 7 November found missing for a release.

**Scope rule for the month:** no new formats and no new fidelity tiers. A milestone
either finishes something already shipped, makes the tool safe and fast on inputs we
do not control, or makes it installable and explainable. Everything else goes on the
post-1.0 list (§8), written down rather than started.

---

## 1. Where we stand (audit, 7 November)

| Area | State | Gap for a release |
|---|---|---|
| Formats | 19 matrices published; Excel and PowerPoint F1 built 30 Oct | **XLSX/PPTX have no matrix** (O5): they fail our own definition of done (differential test at the claimed point, guard, residuals documented) |
| Robustness | suite fuzz corpus covers 16 types; one-off campaigns: 21,000 files on ZIP/EPUB/HEIC/MP4, 3,000 each on XLSX/PPTX | **XLSX/PPTX not in the suite's fuzz corpus** (one-off only); audio/video only lightly; no catalogue of *hostile* inputs (bombs, entity expansion, allocation, recursion, traversal); only 4 timeouts in `src/` |
| Privacy of the run itself | output written atomically (temp + rename, `cli.py`) | F2/F3 copy the **unscrubbed input into `/tmp` work dirs** (PDF, DOCX, M4A, MP3, FLAC); a kill or crash mid-scrub leaves it there |
| Performance | nothing measured except video's memory factor (2.1× the file, limit #38) | no speed figures for any format; whole-file in memory; folder mode sequential, one ExifTool spawn per file |
| Packaging | `python -m src.scrub` from a checkout; `pyproject.toml` deliberately has no `[project]` | no install, no `--version`, runtime and test dependencies in one pinned file, 8 external programs found only at run time |
| Licence | **no `LICENSE` file** | a release without one cannot legally be used by anyone |
| Name | none chosen | **`scrub` is taken**: Homebrew's `scrub` *"writes patterns on magnetic media to thwart data recovery"* — the wrong `scrub` on `PATH` destroys the file it is given |
| Docs | README, limits, per-phase plans, plain-language "what we can't do yet" (30 Oct, untracked) | no user guide (install, levels, exit codes, results sheet, refusals) |
| Repo | 5 commits on `main` not pushed (f569023..1fb76b0); 3 merged worktrees still on disk; progress/limits reports untracked | push needs a fresh ask |

---

## 2. Milestones

### N0 — housekeeping (7–8 Nov)
- Push f569023..1fb76b0 (Excel, PowerPoint, PDF F2 #80) — **on the user's go-ahead**.
- Decide the untracked `progress_report.*` / `what_we_cant_do_yet.*` and the README
  sentence linking the progress report: commit or drop.
- Remove the three merged worktrees (`admiring-pare`, `adoring-hodgkin`, `bold-leavitt`;
  `git branch --no-merged main` is empty).

### N1 — finish what shipped late: Excel and PowerPoint to "done" (9–11 Nov)
O5 from [`office_plan.md`](office_plan.md) §6, which the freeze left open.
- `XlsxPlugin` / `PptxPlugin`, `gen_matrix_xlsx` / `gen_matrix_pptx`, published under
  `check_evidence.py`: A1@F1 across every locus in the §3 census, guard passes.
- A2@F1 measured on real producers: **Excel/PowerPoint 16.114 on this Mac** (check
  scripting works first), LibreOffice, Numbers/Keynote export. Name the surviving
  channels, as every other A2 fail does.
- O0b real-file census: needs **permission to download** the Apache POI and
  LibreOffice `.xlsx`/`.pptx` test collections (the Word census used the same sources).
- Word's malformed-part rule (5 of 946 real Word files would change outcome): measure
  each of the 5, decide, record.
- Badge, README results table, QA-report labels gain XLSX and PPTX.

**Done:** both matrices published and green on CI; README's table has their rows.

**Status (7 Nov): built, local** ([`office_plan.md`](office_plan.md) §7). Matrices
`xlsx` and `pptx` published: A1@F1 pass in every producer shape (4 and 3), A2@F1 a
named failure in the model channel, guard pass. Office could not be a producer
(scripted Excel refused every save), so the peer sets are LibreOffice, openpyxl,
XlsxWriter and python-pptx. Found and fixed on the way: a per-save GUID in every
LibreOffice text field (limit #81). Word adopted the malformed-part rule (682/102).
Still open: O0b (needs D6) and the live MAT2 section in `benchmark.py`.

### N2 — robustness: hostile files refused safely, every type (12–18 Nov)
Schedule item 1. Two halves: random damage (fuzzing) and deliberate attacks.

**N2a — fuzz every handler.** `scripts/fuzz_campaign.py`: 3 seeds × 1,000 mutations
per registered handler and shape (puts XLSX/PPTX into the suite and adds the MP3,
FLAC, M4A, MP4/MOV and metafile shapes it misses). Contract per input: exit code is a *declared* refusal, never
"unexpected"; no output written; under a time and memory ceiling. Every escape found
becomes a replayed regression test in `test_fuzz.py`, as in P6 §14.1.

**N2b — attack catalogue** (`tests/scrub/test_hostile.py`), one row per attack ×
format it applies to, each with an expected refusal:

| Attack | Where it bites |
|---|---|
| Decompression bomb | ZIP/OOXML/EPUB members, PNG `zTXt`/`iCCP`/IDAT, PDF Flate streams, SVGZ |
| XML entity expansion, deep nesting | DOCX/XLSX/PPTX parts, SVG, EPUB OPF/NCX, XMP packets |
| Allocation from declared sizes/counts | TIFF/RAW IFD counts, ISOBMFF box sizes, `stsz`/`stco` entry counts, PE/ELF/Mach-O tables, EMF record sizes |
| Recursion depth | ZIP in DOCX in ZIP…, EMF+ inside EMF inside WMF, `embedded.py` chains |
| Pathological time | inputs that are small but slow (quadratic walks, PDF object loops) → per-file timeout |
| Archive and folder paths | `../`, absolute names, duplicate names, names equal under macOS case-folding, symlinks, output folder inside input, output == input |

**N2c — no residue.** Kill a scrub (SIGKILL, SIGINT) at every stage of each F2/F3
path and assert nothing derived from the input is left in the temp directory or the
output folder. Fix by work dirs created inside the output's directory with `0700`
and swept at start-up, or by passing data over pipes where the tool allows it.

**N2d — exit-code contract** written once (user guide §N5) and asserted for every
outcome the CLI and folder mode can produce.

**Done:** zero unexpected exits across the campaign; every catalogue row refuses
within its ceiling; kill test leaves nothing; limits.md gains a row for each attack
we refuse rather than handle.

**Status (8 Nov): done, local.** Campaign: 160,476 files, 36 shapes (46 shape-and-level
pairs); 1,803 escapes in 11 classes (jpegtran and other external-program failures raised as the bare
`ScrubError`; Pillow errors behind valid PNG CRCs; `bytes.index` in Mach-O; a
`LookupError` encoding in EPUB; `struct.error` in PE resources; undecodable PDF
names; an SVG refusal message that crashed; `None` boxes in CR3; our own safety
checks raised bare). All fixed with replayed tests that fail without the fix; the
re-run found 7 more in 3 classes (CR3's residual check on a block
with no readable IFD, a JPEG jpegtran accepts but Pillow cannot decode, a PDF filter
name that is not text -- refused, since a damaged `/DCTDecode` would skip the JPEG
clean); a third run: 160,476 files, zero escapes. No hang, no residue. Catalogue: 15 rows, 7 of them failed when
probed (see `test_hostile.py`'s docstring); limits #83-#86. New: `tools.run`
(time limit on every external program), `workdir` (private, swept), exit 9
(`ToolError`), `cli.exit_code` by class hierarchy, Office memory preflight (28x
XML, measured). Provisional numbers to confirm: the time limit (5 min + 20 s/MB)
and the XML factor's margin.

### N3 — performance: measured, then bounded (16–24 Nov)
Schedule item 2. Measure before changing anything.

**N3a — measure.** `scripts/perf_bench.py`: per format and tier, a size ladder
(1 MB → 100 MB; video, ZIP and PDF to 4 GB), wall time and peak RSS, with the ExifTool
cross-check timed separately. Output `docs/performance.md` and a QA-report section;
CI runs a small ladder as an informational regression check.

**N3b — stream what has no size bound.** MP4/MOV first: F1 already rebuilds `mdat`
from the sample tables, so it can copy sample ranges from disk to disk instead of
holding both copies — this retires limit #38's 2.1× factor. Then M4A (same walker)
and ZIP members. Formats that never reach a problematic size stay in memory.

**N3c — throughput for folder mode.** `--jobs N` (process pool; LibreOffice already
has private profiles), and one long-lived ExifTool (`-stay_open`) for the
cross-check instead of a spawn per file.

**Targets — proposed, confirm before locking:** peak memory for MP4/MOV/M4A/ZIP F1
independent of file size (≤ 512 MB); a folder of 1,000 iPhone photos at F1 in under
5 minutes on this Mac with `--jobs` = cores.

**Done:** `performance.md` published; a 4 GB video cleaned within the memory target
with output byte-identical to the in-memory path; #38 narrowed or closed.

### N4 — packaging: one install, one fingerprint (19–26 Nov)
Schedule item 3.

- **A real package**: `[project]` + `[build-system]` in `pyproject.toml`, import package
  renamed from `src` to the chosen name, a console command, `--version`; runtime
  dependencies split from test dependencies (pytest, jsonschema, ImageHash are not
  runtime). Install with `pipx install`.
- **`<tool> doctor`**: lists each external program (ffmpeg, lame, jpegtran,
  LibreOffice, poppler, Ghostscript, ExifTool, …), its version, and which formats and
  tiers are therefore available; the CLI refuses an unavailable tier with that message.
- **A container image** with every external program at a pinned version — the
  "everything included" install, and the reference environment for published results.
- **E-INSTALL — does the install fingerprint the user?** Our A2 passes collapse
  producers to *one* output, but that output is made by the user's own Pillow, zlib,
  lame and ffmpeg. MP3 F3 writes the LAME version string into every file; PNG F2, ZIP
  and DOCX depend on the deflate implementation (zlib vs zlib-ng, as on recent Fedora).
  If two installs give different bytes for the same input, the cleaned file tells an
  investigator **which machine setup cleaned it**. Measure: one corpus scrubbed on
  macOS, Linux (Ubuntu), the container, Python 3.11 vs 3.14; every byte difference is a
  finding. Fix by pinning (the container) or by a limit row naming the channel.
- Clean-machine install job in CI: build the wheel, install it in a fresh container,
  run `doctor` and a smoke scrub of every format.
- **Licence** chosen and `LICENSE` added; third-party notice listing each external
  program's licence (Ghostscript is AGPL, ExifTool Artistic/GPL, LAME LGPL, LibreOffice
  MPL) — subprocess use, but the container image *distributes* them.

**Done:** `pipx install` + `doctor` work on a clean Mac and a clean Linux container;
E-INSTALL table published; licence in the repo.

### N5 — documentation and release candidate (23–30 Nov)
Schedule item 4.
- **User guide** (`docs/user_guide.md`): install, the three levels in plain words,
  single file / folder, the report and the ExifTool block, the results sheet, every
  exit code, every refusal class and what to do about it, the workflow limits (the
  original stays on disk; macOS provenance tag, #64).
- **Final limits write-up**: `what_we_cant_do_yet` regenerated from `limits.md` (one
  source of truth), every 🔜 row either done, moved to the post-1.0 list, or re-worded.
- **Blind test**: someone who did not build the tool installs it from the guide alone
  and cleans their own files; every place they get stuck is a doc or UX fix.
  (Needs a person — the user's to arrange.)
- `CHANGELOG.md`, version `1.0.0rc1`, tag, release notes; README Status rewritten for a
  release; a 30 November progress report against the September baseline.
- Funding proposal: refresh its figures from the published matrices if it is due
  (date unknown — see §6).

**Done:** tag `v1.0.0rc1` on a green CI run; guide followed end to end by the blind
tester.

---

## 3. Calendar

| Week | Dates | Focus | Exit |
|---|---|---|---|
| 0 | Sat 7 – Sun 8 | N0 | pushed, worktrees gone, decisions in §6 taken |
| 1 | Mon 9 – Sun 15 | N1 (Mon–Wed), N2a–b start (Thu–) | XLSX/PPTX matrices green; fuzz campaign running |
| 2 | Mon 16 – Sun 22 | N2 finish; N3a measure; N3b start | robustness done; `performance.md` first numbers |
| 3 | Mon 23 – Sun 29 | N3b–c finish; N4; N5 guide + blind test | streaming video; `pipx install` + container; guide drafted |
| — | **Mon 30** | N5 release | `v1.0.0rc1` tagged, progress report |

Slack is thin in week 3. If anything slips, N3c (`--jobs`) and the container image
move after the RC; streaming video (N3b) and the licence do not.

---

## 4. Order and why

1. **N1 before N2**: fuzzing XLSX/PPTX before their matrices exist would harden
   handlers whose definition of done is still open.
2. **N2 before N3**: streaming rewrites the MP4 write path; the fuzz campaign and
   attack catalogue must exist first so the rewrite is checked against them.
3. **N3a before N3b**: no optimisation on a guess. Streaming is chosen for video because
   limit #38 is the one measured problem; the benchmark decides whether anything else
   needs it.
4. **N4 after the code settles**, but E-INSTALL early in its week: a platform
   difference found there may need a code fix before the RC.

---

## 5. Risks

- **Office scripting on this Mac** may need permissions or licence checks (unverified):
  then N1's A2 runs on LibreOffice + exported files only, and says so.
- **Streaming MP4** touches the handler that rebuilds `mdat`; the decode-identical and
  byte-identical-to-in-memory checks are the guard. Budget two days of slip.
- **E-INSTALL may fail broadly** (e.g. every deflate-based output differs on zlib-ng).
  Then the honest result is a limit row and "use the container for A2", not a rewrite.
- **The blind test depends on a second person** being available in week 3.

---

## 6. Decisions needed from the user

| # | Decision | Recommendation |
|---|---|---|
| D1 | Command / package name (`scrub` is taken by a disk-wiping tool) | a distinct name, chosen once, used for the command, package and container |
| D2 | Licence | the university's rules first; otherwise a permissive or LGPL-style licence (only subprocess use of GPL/AGPL tools) |
| D3 | Install channels | `pipx` + container image; Homebrew tap after 1.0 |
| D4 | Performance targets in N3 | the proposed numbers, or others — confirm before N3b starts |
| D5 | O4 (Office files inside Office files) in November? | **after the RC**: it is feature work; it would clear 28 of Word's 33 embedded-object refusals and PowerPoint's chart workbooks, so it is first on the post-1.0 list |
| D6 | Download POI/LibreOffice `.xlsx`/`.pptx` test collections for O0b | yes, same handling as the Word corpus (outside the repo, aggregates only) |
| D7 | Funding-proposal due date | needed to place it in the calendar |
| D8 | Push the 5 local commits | yes, after a local full-suite run |

---

## 7. Measurement rules carried into November

- A robustness or performance claim reaches README or the proposal only with the
  campaign or benchmark that produced it, as for every matrix cell.
- Every refusal added in N2 is a `limits.md` row the day it is added.
- Tests touching new tools are checked for macOS-only assumptions before a push
  (ffmpeg encoders, `sips`, `codesign`); ELF in `linux/amd64` containers.

---

## 8. After 1.0 (not this month)

In rough order of real-world value:
1. **O4** Office-in-Office recursion (D5), then Word's HD Photo (#68) and embedded-font keys.
2. **F2 tiers specified by A2@F1 failures**: MP4 (#37), HEIC (#33), EPUB (#62), BMP (#71),
   TIFF/GIF/SVG, programs' container traits (#55).
3. Refusals with real users behind them: fragmented MP4 (#39), ZIP64 (#59, natural after
   streaming), multi-picture HEIC (#67), other RAW makers (#46), .NET (#54), BigTIFF (#56),
   UTF-16 SVG (#57), VBRI (#7), rare BMPs (#72).
4. New families: OpenDocument, legacy Office (`.doc/.xls/.ppt`), WAV, MKV/AVI.
5. Experiments: M13 x264 SEI (#36), RAW camera-body A2 (#47), JPEG E4, MDCT-domain MP3
   classifiers (#6).
6. Decoy mode (`--decoy`), behind E-DECOY ([`implementation_plan.md`](implementation_plan.md) Phase 6).
7. Real-file gaps: DJI and Android clips, store EPUBs, real PowerPoint-written decks.
