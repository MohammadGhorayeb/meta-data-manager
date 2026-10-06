# Phase 5 Plan — Executables: ELF, Mach-O, PE

Programs are the one family where the hard constraint is not "looks the same" but
**"runs the same"**: same output, same exit code, and still accepted by the loader.
Nothing here reuses the media standard modules, and two things change the rules
relative to every earlier phase:

- **The file may be signed.** On Apple Silicon every executable carries a code
  signature hashing its own bytes page by page; change one byte and the kernel kills
  the program at launch (measured below). Any edit means recomputing integrity fields
  ourselves, so this is operating depth 3 as a requirement rather than a choice.
- **Some of what identifies the builder is program text.** A path the program prints
  from an `assert` or a panic is part of what it does. Editing it changes the output,
  so it is content, and we report it but never edit it (§5).

Same method as every phase: measure first. §0–§4 are the opening survey (M0), and §6
holds the decisions it forces.

---

## 0. The corpus (M0, 2026-10-06)

One deterministic program (`hello`: hashes its arguments, prints a digest, exits with
`digest % 7`), so "runs the same" is a byte comparison of two runs, not a judgement.
It was built by every toolchain available, **twice, in two build directories or under
two user names** (`alice`, `bob`). Any byte that differs between the two builds of a
variant depends on who built it, or where.

| Format | Where | Toolchains and variants | Runs on |
|---|---|---|---|
| Mach-O (arm64) | this Mac | Apple clang 15 `-O2`, `-g`, separate compile+link; `strip`; rustc 1.89 `-O`, `-g`; `cargo build --release`; swiftc `-O` | natively |
| ELF (aarch64) | `ubuntu:24.04` container | gcc 13.3 `-O2`, `-g`, `-static`, `-s`; `-g` then `strip`; clang 18 `-O2`, `-g`; rustc 1.75 `-O`; Go 1.22 plain, `-trimpath`, and inside a git checkout | container, natively |
| PE (x86-64) | `ubuntu:24.04` amd64 container | mingw-w64 gcc 13 `-O2`, `-g`, `-s`, `--no-insert-timestamp`, with a version resource; clang 18 + lld with a PDB; Go 1.22 `GOOS=windows` | Wine 9.0 |
| PE, MSVC-built (real) | already on disk | pip's `distlib` launchers (`t64.exe`, `w64-arm.exe`) and setuptools' (`cli-64.exe`, `gui-arm64.exe`), public open-source binaries | Wine 9.0 |

No MSVC toolchain was available, so the Rich header, which only Microsoft's linker
writes, is measured on the four real launchers only. Real distributed binaries
(Homebrew's `ffmpeg`, `lame`, `jpegtran`) carry **no** build paths at all.
Distributions build cleanly. The exposure is in programs people compile themselves,
and that is what this phase is for.

Research artifacts: `~/metadata-research/step2/exe/` (build scripts included). Paths
below show the user as `<user>`.

---

## 1. The leak surface, per locus

### Mach-O

| Locus | What it holds | When | Needed to run? |
|---|---|---|---|
| Debug map: `N_SO` / `N_OSO` stab symbols | the **build directory** (`/Users/<user>/…/buildA/`), the object's path, including the per-user temp dir `/private/var/folders/<per-user id>/T/hello-xxxx.o` | every `-g` build | no |
| `N_OSO` value | the object file's **mtime**: a build timestamp to the second | every `-g` build | no |
| Same debug map, **release Rust** | `/Users/<user>/.rustup/toolchains/…/libstd-….rlib`: the **home directory**, 4–13 entries | every plain `rustc -O` build; **not** `cargo build --release` (strips debug info by default since Rust 1.77) | no |
| `LC_UUID` | two kinds, measured by rebuilding in one directory: a **random** UUID, new on every link, for a one-step `clang -g` build (version-4 bits), a per-build tag that matches that build's debug symbols and crash reports; otherwise a **deterministic hash** of the output that does *not* include the debug-map paths (`rs_g` from two directories: same UUID) but does change with the output name's length, through the signature's size | every build | no (measured: an all-zero UUID loads) |
| Code-signature identifier | the **file name at link time** (`c_g`, `rs_O`), and it survives renaming | every arm64 build (linker-signed, ad hoc) | no (measured: blank loads) |
| Code-signature page hashes | SHA-256 of every 4 KiB page up to the signature | every arm64 build | **yes: any edit must recompute them** |
| `LC_BUILD_VERSION` | min OS, SDK, linker version (`ld 1053.12`) | every build | min OS / SDK: **yes** (the OS gates behaviour on the SDK a binary was linked against); linker version: no |
| `/rustc/<40-hex commit>/library/…` | the exact compiler build, in panic location strings | every Rust build | **program text** (§5) |

### ELF

| Locus | What it holds | When | Needed to run? |
|---|---|---|---|
| `.comment` | compiler **and distribution package** versions: `GCC: (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0`, plus clang / rustc lines when they linked | every gcc/clang/rustc build, **including `-s`** | no |
| `.note.gnu.build-id` | a hash over the output, **including debug info**: two users' `-g` builds differ in it | every build | no (measured) |
| DWARF `DW_AT_comp_dir` | the **build directory** (`/home/alice/proj`) | `-g` | no |
| DWARF `DW_AT_producer` | the **full compiler command line** (`GNU C17 13.3.0 -mlittle-endian -mabi=lp64 -g -O0 -fasynchronous-unwind-tables -fstack-protector-strong …`) | gcc `-g` | no |
| `.symtab` `STT_FILE` | source file names | unstripped builds | no |
| Go `.go.buildinfo` | module path, Go version, build flags, **`vcs.revision` (the commit hash), `vcs.time` (the commit time), `vcs.modified`** | any Go build in a git checkout, **`-trimpath` included** | no (read by `go version -m`, not by the program) |
| Go `.note.go.buildid` | content hash of the build's inputs | every Go build | no (measured) |
| Go `.gopclntab` | the **source path** (`/home/alice/proj/hello.go`) | without `-trimpath` | **program text**: `runtime.Caller` and every stack trace read it (§5) |

### PE

| Locus | What it holds | When | Needed to run? |
|---|---|---|---|
| COFF `TimeDateStamp` | link time, to the second | mingw and lld by default (Go writes 0; mingw `--no-insert-timestamp` writes 0) | no |
| Rich header (DOS stub, `DanS…Rich`) | every Microsoft tool and its **build number** that touched the program, with **object counts**: a per-project fingerprint (Webb) | MSVC only: all four real launchers | no |
| Debug directory, CodeView `RSDS` | **PDB path** plus a GUID and age unique to the build; real: pip's own `t64.exe` ships `C:\Users\<author>\Projects\simple_launcher\dist\t64.pdb` | MSVC release builds, lld with `--pdb` | no |
| Debug directory timestamps | the link time again, once per entry (CodeView, POGO, VC_FEATURE) | MSVC | no |
| Version resource | CompanyName, LegalCopyright, **OriginalFilename**, ProductName | when the project has an `.rc` | no (Explorer shows it) |
| mingw CRT DWARF (`/4`, `/19`, … sections) | the **distribution's package path and version** (`/usr/src/mingw-w64-11.0.1-3build1/…`) | **every** mingw build, `-O2` without `-g` too; the user's own build dir is added only by `-g` | no |
| `CheckSum` | derived from the bytes | mingw | only for drivers and system DLLs; recomputed anyway |
| Authenticode | the publisher's certificate | signed binaries | §6 D3 |

---

## 2. Does it still run? The acceptance test, measured before any code

Every locus in §1 marked "no" was edited in place, size-preserving, and the result was
run against the original with the same arguments:

| Format | Edit | Result |
|---|---|---|
| ELF: gcc, clang, Rust, Go | build-id / Go build ID descriptor zeroed, `.comment` zeroed | **identical** stdout and exit code, all four |
| PE: mingw (with version resource), clang+lld (with PDB) | `TimeDateStamp`, debug-entry timestamps, CodeView GUID/age/path, version strings zeroed; checksum recomputed | **identical** under Wine 9.0 |
| PE: real MSVC launchers `t64.exe`, `cli-64.exe` | the same, plus the Rich header zeroed | **identical**, but only on their own error path (neither has its script beside it); they show the loader accepts the edited file, not full-program evidence |
| Mach-O arm64 | **one byte** changed (the UUID), no re-sign | **killed at launch**: exit 137, `invalid signature (code or signature have been modified)` |
| Mach-O arm64 | debug-map strings and `N_OSO` mtimes blanked, UUID zeroed, **page hashes recomputed by our own code** (no `codesign`) | **identical**, and Apple's `codesign -v` accepts the signature we computed; home path gone |
| Mach-O arm64 | signature identifier blanked, or same-length replaced | runs |

The Mach-O row is the one this phase turns on, and it is already retired: an ad-hoc
CodeDirectory is a list of SHA-256 page hashes plus a header, and recomputing it needs
nothing from Apple. That keeps Mach-O inside "the scrubber parses itself", and lets
Linux CI verify our signatures with our own verifier while only the Mac can launch
the program.

---

## 3. What the standard tools do

| Tool | ELF | Mach-O | PE |
|---|---|---|---|
| **MAT2 0.14.0** | refused: lists no executable type | refused | refused |
| **ExifTool `-all=`** | "Writing of this type of file is not supported" | same | "Can't write EXE files" (it **reads** the version resource, timestamp and linker version) |
| **`strip`** | removes DWARF and `.symtab`; **keeps `.comment`**, and **keeps the build-id**, which was hashed from the debug info it removed: two users' stripped `-g` builds still differ in exactly those **20 bytes** | removes the debug map; **keeps `LC_UUID`**: for a one-step `clang -g` build that is the random per-build tag, so two stripped builds still differ in **48 bytes** (the UUID plus the signature hash of its page) and each still matches its own debug symbols; deterministic builds come out identical | mingw `strip`: removes DWARF; keeps the timestamp, the version resource and anything CodeView |

So `strip` removes the paths and leaves an ID that outlives them: on ELF a hash *of*
the removed paths, on Mach-O (one-step `-g`) a random tag issued with them. The
stripped file no longer says where it was built, yet it still matches the unstripped
build, its debug symbols and any crash report, and it still tells two builds apart.
An ID that survives the data it identifies is the executable form of the stale-copy
problem M7 found in video.

---

## 4. Findings worth stating on their own

1. **A plain `rustc -O` build on macOS embeds the user's home directory**, through
   the standard library's debug map, with no `-g`. `cargo build --release` does not.
2. **Go stamps the commit hash and commit time into every build made inside a git
   checkout**, and `-trimpath` does not stop it; `-buildvcs=false` does (measured).
3. **gcc's `-g` records the full compiler command line** in `DW_AT_producer`.
4. **mingw builds carry the distribution's toolchain package version even without
   `-g`**, from the C runtime's own debug sections.
5. **pip ships a launcher whose PDB path names its author's Windows user directory**:
   a real instance of the CodeView leak in software installed on most machines.
6. **Build IDs survive `strip` on both Unix formats** (§3): ELF's is hashed from the
   paths `strip` removes; Mach-O's, for a one-step `clang -g` build, is random per
   build. An early reading of this survey claimed the Mach-O UUID was path-derived
   too; rebuilding in one directory showed otherwise (deterministic builds: same UUID
   from two directories), and the claim was corrected before it went anywhere.

---

## 5. Program text is content

Three loci carry identity but are read by the program itself: `assert`'s `__FILE__`,
Rust's panic locations (`/rustc/<commit>/…`, and `/Users/<user>/.cargo/registry/…`
for dependencies), and Go's `.gopclntab` source paths (stack traces,
`runtime.Caller`). Replacing them changes what the program prints on that path, which
breaks the hard constraint. So F1 **reports** them ("this program's own text contains
a home-directory path") and leaves them. The real fix is at build time. Measured so
far: `cargo build --release` drops the macOS debug map, `go build -trimpath` drops
Go's source paths, and `-buildvcs=false` drops the commit stamp (`-trimpath` alone
leaves all four `vcs` lines). The C and Rust prefix-map options
(`-ffile-prefix-map`, `--remap-path-prefix`) are documented to do the same and are
**not yet measured**, so the report names only what has been. This becomes a limit in
`docs/limits.md` when the first handler ships.

---

## 6. Design decisions the survey forces

- **D1. F1 edits in place and moves nothing**, as camera RAW does. Every locus in §1
  can be blanked at its own length. No offset, section or segment moves, so no load
  command, section header or relocation needs rewriting. Dropping the blanked
  sections outright (smaller files, no zero-filled regions) is F2.
- **D2. IDs are recomputed from the scrubbed bytes, not zeroed.** UUID, build-id and
  Go build ID become a hash of the output with the ID field excluded. Zeros would
  mark the file as scrubbed (limit #9), a fresh hash looks like any build, and
  because the input no longer contains the paths (ELF) or the random tag (Mach-O),
  it no longer links to the original. The Mach-O UUID keeps the version-3 / variant
  bits `ld64` sets on its own deterministic UUIDs.
- **D3. Signatures.** A *linker-signed or ad-hoc* Mach-O signature carries no identity,
  so F1 re-signs it itself (prototyped, §2). An **identity signature** (Apple
  Developer ID, Authenticode) is refused, fail closed. Editing the file invalidates
  it, and a program that ran on the recipient's machine would then be blocked by
  Gatekeeper / SmartScreen, which breaks "runs the same". And the certificate is the
  publisher's identity by design, so removing it is not ours to decide silently.
- **D4. Program text is reported, never edited** (§5).
- **D5. Fat (universal) Mach-O**: each slice handled independently. In-place edits
  leave the fat header's offsets valid.
- **D6. Acceptance oracle.** "Runs the same" = identical stdout, stderr and exit code
  for a fixed argument set, run where the format runs (ELF natively on Linux CI, PE
  under Wine on Linux CI, Mach-O on macOS). Plus an independent parser (LIEF / pefile,
  test-side only, like `rawpy` for RAW) to confirm the structure. Where a format
  cannot run (Mach-O on Linux CI), our own signature verifier and the parser still
  check it, and the matrix says the execution check did not run there.

---

## 7. Milestones

The original plan order was PE → ELF → Mach-O. The survey changes it to **ELF first**,
because ELF is the only format CI can both build and execute natively, so the plugin
and the "runs the same" oracle get built against a format whose acceptance test runs
everywhere. Mach-O comes second (the riskiest part, the signature, is already
prototyped), and PE third (it needs Wine in CI).

| Milestone | Scope | Done when |
|---|---|---|
| **M1** — shared exe model + ELF F1 | `formats/exe/` (one handler, three walkers), ELF: `.comment`, build-id (D2), DWARF blanked, `STT_FILE`, Go buildinfo/build ID; program-text report | the §0 ELF corpus runs identically and no `alice`/`bob` difference survives outside program text |
| **M2** — Mach-O F1 | debug map + `N_OSO` mtimes, UUID (D2), signature identifier, own ad-hoc re-sign + verifier, fat binaries, identity signatures refused (D3) | the §0 Mach-O corpus runs identically, `codesign -v` passes on macOS, our verifier passes on Linux |
| **M3** — PE F1 | timestamps, Rich header, CodeView, version resource, mingw DWARF, checksum; Authenticode refused | the §0 PE corpus and the MSVC launchers run identically under Wine |
| **M4** — `ExePlugin` + matrix `exe` + CI | A1 differential over the alice/bob pairs per toolchain; A2 across toolchains (expected to **fail** at F1: the compiler is in the code itself, as the encoder is in a JPEG's DQT); Wine and Go in CI | matrix published, evidence gate re-measures it, limits recorded |

---

## 8. M1 as built — ELF at F1

`src/scrub/formats/exe/` — `elf.py` (walker, F1, residual check, report, advisories)
and `handler.py` (`ExeHandler`, format id `exe`, F1 only). Registered after RAW;
`\x7fELF` is shared with nothing.

**The rule is the loader's.** Every section without `SHF_ALLOC` is zeroed in place
except `.symtab`, `.strtab`, `.shstrtab` and the architecture attributes — an
allowlist, so a toolchain's next section is removed rather than missed. A file where
such a section lies inside a `PT_LOAD` range is refused (the flag is only a claim;
the loader maps segments). Source-file symbol names are blanked except bytes another
symbol's name shares (linkers merge string tails). The GNU build-id and the Go build
ID are recomputed: a SHA-256 of the cleaned file with both ID fields zeroed, the Go
one keeping its `/` positions and alphabet. Go's commit stamp, its pseudo-version
(Go ≥ 1.24 writes `v0.0.0-<time>-<hash>` as the module version) and the module path
are blanked in both copies — unless `runtime/debug.ReadBuildInfo` is linked, which
is checked by its name in the binary (the linker drops it when unused).

**Enforced on every scrub, not only in tests:** inside every `PT_LOAD` range, the
output may differ from the input only at the build IDs and the Go stamp, or nothing
is written.

**Measured on the survey corpus (§0), 13 files × 2 users:** no residuals;
**22 of 22** scrubbed programs run identically (Ubuntu 24.04); `go version -m` still
parses the cleaned build info; Go 1.25's pseudo-version handled. The survey's sharpest
finding closes: two users' stripped `-g` builds, 20 bytes apart after `strip` (the
build-id), come out **byte-identical**. Unstripped `-g` builds from paths of
different lengths still differ by ~21 bytes — the section sizes and the build-id
hashed over them (limit #49); equal-length paths come out identical, tested on real
gcc and clang builds in CI. What stays is program text (limit #50): Go's `.gopclntab`
source paths, reported by `advise()`.

**Tests:** `tests/scrub/elf_corpus.py` builds executables byte by byte (both widths,
both byte orders, C and Go shapes, shared string tails, every refusal case), read
independently with pyelftools (new test-side pin). `test_exe_elf.py` runs real gcc,
clang, `strip` and Go builds where Linux and the compilers exist — CI now installs
`gcc clang binutils git golang-go` in all three measuring jobs, recorded in the CI
contract. ELF joins the fuzz, idempotence and cross-process determinism suites.
Limits #49–#51.

---

## 9. M2 — Mac programs (Mach-O)

### 9.1 What the first survey had not built

Universal (`-arch arm64 -arch x86_64`), a dylib (with and without an absolute
install name), a binary re-signed with `codesign -s -`, Swift `-g`, an x86-64-only
binary, and Go cross-compiled for the Mac in a git checkout (`GOOS=darwin`):

- **The signature's identifier is metadata, and sometimes worse.** The linker writes
  the output's name; a universal build writes `hello-arm64.out` — named after the
  **source file**, from the driver's temporary per-architecture output; and
  `codesign -s -` writes `<name>-55554944<UUID>`, so the old UUID survives *inside
  the signature* and recomputing `LC_UUID` alone would have kept the link. Nothing
  checks it for an ad-hoc signature: the designated requirement is the cdhash
  (`codesign -d -r-`). Go's linker writes `a.out` — the crowd value F1 uses.
- **The linker does not sign x86-64.** Only arm64 slices carry signatures; `codesign
  -v` of a universal file reports "not signed at all" before *and* after (it reads
  the x86-64 slice), and `--arch arm64` verifies.
- **Swift `-g` adds `N_AST`**, the `.swiftmodule` path in the per-user temp dir.
- **Go writes an unmapped `__DWARF` segment** (vmsize 0, initprot 0): the Mach-O
  equivalent of ELF's unloaded sections — never mapped, so zeroed. Its build ID is
  at the start of `__TEXT` (`\xff Go build ID: "…"`); its build info is ELF's.
- **A dylib's install name is the loader's**: an absolute one names the user
  directory, and every program linked against it copies it. Kept, reported.

### 9.2 As built

`exe/macho.py` (thin and universal; 32/64-bit; either byte order), `exe/codesign.py`
(SuperBlob, every CodeDirectory, SHA-1/256/384, 4 KiB and 16 KiB pages), with Go's
rules shared from `exe/go.py` and the unchanged-bytes check from `exe/common.py`.
Per slice: unmapped segments zeroed; path stabs (`N_SO`, `N_OSO`, `N_SOL`, `N_AST`,
`N_BINCL`) blanked except shared string tails, `N_OSO` mtimes zeroed; `LC_UUID`
and Go's build ID recomputed from a hash of the cleaned slice *without* the
signature (which hashes them); identifier → `a.out` when it fits (limit #53);
every code slot re-hashed. Refused: identity signatures (limit #52), objects, core
dumps, dSYMs (#51), an unmapped segment over mapped bytes. Enforced on every scrub:
inside mapped segments only the listed edits and the signature may differ.

**Measured on 20 real Mac programs** (clang `-O2`/`-g`/separate compile, strip,
rustc, cargo, swiftc, universal, dylib, `codesign`-signed, x86-64-only, Go):
no residuals, every program **runs the same** (the x86-64 one under Rosetta), a
program linked against a cleaned dylib still loads it, and Apple's `codesign -v`
accepts every recomputed signature. **Builds from two directories converge to
byte-identical output** — including one-step `clang -g`, whose inputs differ in a
random UUID, temp object names and an object time — where `strip` left 48 bytes.

**Tests:** `macho_corpus.py` writes thin, universal, 32-bit big-endian, dylib, Go,
`codesign`-style and identity-signed programs byte by byte and signs them with its
own second implementation; macholib (new test-side pin) reads them independently,
and `codesign -v` accepts them as built. `test_exe_macho.py` runs real clang
(`-g`, `-O2`, universal), rustc, swiftc and Go builds on macOS. **CI gains a macOS
job** (`macos-latest`, free for a public repository) that runs the executable tests
natively and reports as its own stage; Linux checks the same signatures with our
verifier and the corpus's signer. Mac programs join the fuzz suite.

### 9.3 Found on the way: ELF on x86-64

M1's first CI run failed on real x86-64 builds: gcc's default 8-aligned
`.note.gnu.property` (CET) exposed a note walker that aligned sizes instead of
offsets. The aarch64 containers M1 was tested in write no such note. Fixed
(`ba57885`), pinned by a property note in every 64-bit corpus build, and real-compiler
tests re-run in an x86-64 container.
