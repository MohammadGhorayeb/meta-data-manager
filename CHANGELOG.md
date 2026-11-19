# Changelog

## Unreleased

Measurements after the tag; the tool's behaviour is unchanged.

- A 4.5 GB video, past the 4 GiB mark: streamed in 220 MB, byte for byte the same output
  as holding it whole (`scripts/perf_big_video.py`, limit #38).
- The benchmark on Linux, in the pinned image: every memory peak at or below the Mac's
  (`docs/performance.md`).
- JPEG's strongest clean on 1,258 real JPEGs: none refused, the lowest 27.0 dB against
  the 25 dB cut-off, which is no longer provisional (limit #87).
- The MAT2 comparison covers Excel and PowerPoint, re-measured on every run
  (`docs/benchmark.md`).
- The pinned image on x86-64 and on arm64 (`.github/workflows/e-install.yml`): 39 of 42
  cleans byte for byte the same; the strongest cleans of MP3, M4A and PDF differ, so they
  can show the kind of processor that cleaned them (limit #88).

## 1.0.0rc1 (2026-11-19)

The first release candidate. Every result it claims is measured and re-measured by
the test suite; what it cannot do is in [`docs/limits.md`](docs/limits.md).

**File types.** Pictures: JPEG, PNG, HEIC, TIFF, WebP, GIF, BMP, SVG, EMF/WMF and
camera RAW (Canon CR2 and CR3, Nikon, Sony, Fujifilm, Olympus, Panasonic, iPhone
ProRAW). Audio: MP3, FLAC, M4A. Video: MP4, MOV. Documents: PDF, Word, Excel,
PowerPoint, EPUB. Programs: Linux, Mac and Windows. Archives: ZIP. Whole folders, with
one results sheet.

**Cleaning levels.** A light clean for every type; lossless deep cleans for JPEG, PNG,
FLAC, M4A, PDF and Word; full cleans for JPEG, MP3, M4A, PDF and Word.

**Excel and PowerPoint** have published results: metadata removal passes in every
producer shape measured, and the programs that wrote them remain distinguishable by
how they write the document (limit #82). LibreOffice's per-save field IDs are removed
(limit #81).

**Robustness.** 160,476 deliberately damaged files and 15 purpose-built attacks: every
one is cleaned or refused with its own exit code, no refusal leaves a file behind, and
nothing hangs. Every external program has a time limit. A clean that is killed outright leaves
its copy of the original only in a private folder, removed by the next run.

**Performance** (`docs/performance.md`). Video's light clean is streamed: a 1 GB clip
needs 44 MB of memory, not 2 GB. Folder mode keeps one ExifTool running and takes
`--jobs N`: 300 photos with the check, 48.5 s to 1.8 s. The memory check prices each
level by what drives its cost: file size, pixels, or length.

**Installing.** The tool installs as `scrubmeta` (`pipx install .`, Apache-2.0; a
checkout still runs as `python -m src.scrub`). `scrubmeta doctor` shows what an install
can do. Three Python libraries
at run time. A container image with every program at one exact version, because files
cleaned at the strongest level can otherwise show which versions cleaned them
(limit #88).

**Documentation.** A user guide (`docs/user_guide.md`), every command in it run from
a clean install: install, the three levels, folders, the report, every exit code and
refusal. The plain-language *What scrubmeta can't do yet* is generated from
`docs/limits.md`, and a test fails if the two disagree or a limit promises work no list
carries.

**Behaviour changes to know about.**
- The output may no longer be the input itself: `a.jpg a.jpg` used to replace the
  original, and is now refused (exit 2).
- New exit code 9: a helper program is missing, refused the file or ran too long.
  These used to be exit 1.
- Word refuses a part no parser can read, as Excel and PowerPoint do: 3 of 784
  real Word documents change from cleaned to refused.
- JPEG's full clean checks pixels (PSNR) instead of a perceptual hash: photos of clear
  skies and night scenes are no longer refused, and colour or brightness faults no
  longer pass (limit #87).
- In folder mode, `--no-report` only hides the per-file lines; the ExifTool check, which
  there deletes a file it contradicts, is turned off only by `--no-verify`.

**Known before release.** A deliberate refusal shares its exit code with a fault of the
same class: Word's tracked changes exit 6, a signed PDF or a video with subtitles exit 4,
a .NET or identity-signed program exit 3. The message says which; the user guide says to
read it first.
