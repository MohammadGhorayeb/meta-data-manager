# Phase 0 Differential-Testing Harness

The **verification primitive** — and the definition of "done" — for every metadata
scrubber, built *before* any single format handler. Phase 0 scope: no
format-specific scrubbing logic. It targets adversaries **A1** and **A2**; **A3**
is out of scope as a target (the threat model assumes the adversary does not hold
the original) and is emitted in matrices only as `not_tested`.

This README is the authoritative entry point for the harness. A briefer
pre-existing note lives at `tests/harness/README (1).md`; this file supersedes it.
The full design lives in two companion documents: `harness_methodology.md`
(Part A — the *why*) and `harness_spec.md` (Part B — the *what*).

---

## 1. The one idea

Every check in this harness is the **same statistical test** with a different
grouping factor: *does a feature vary across groups by more than it varies within
a group?* Within-group variation is the **noise floor**; anything that separates
groups beyond the floor is a **signal**. This is a variance/ANOVA decomposition
over categorical features, and it is why thresholds are never arbitrary
constants — **the threshold is the measured floor.** For F1 and deterministic F2
the floor is provably empty, so any between-group difference is a leak (byte
identity as a special case). For non-deterministic F2 and F3 the floor is
non-empty and the same rule applies against it.

Build the engine once (`oracle/variance.py`); wire four callers by changing only
the grouping factor:

| Grouping factor | Within-group = floor | Between-group signal | Target | Module |
|---|---|---|---|---|
| **repeat** (same input, scrubbed N×) | — (*defines* the floor) | n/a | the yardstick | `oracle/floor.py` |
| **metadata variant** (same content, different metadata) | repeat noise | metadata leak | **A1** | `oracle/leak.py` |
| **source** (same producer, peer files) | repeat noise | source fingerprint | **A2** | `oracle/peerset.py` |
| **input** (everything varies) | n/a | tool-constant ⇒ scrubber signature | fingerprint guard | `oracle/fingerprint_guard.py` |

The decomposition rule (categorical, Phase 0): a feature is a **leak** iff
`n_between > max(max_within, 1)` — distinct representative values across groups
exceed the worst-case distinct values within any single group. Deterministic
clean: `n_between=1` ⇒ no leak. Leaky: `max_within=1`, `n_between=#variants>1` ⇒
leak. Noisy nonce: `max_within=#repeats` ≥ `n_between` ⇒ floor, not a leak.

> Terminology: the project calls this "differential testing," but the technique
> is more precisely **metamorphic testing** — one program (the scrubber), related
> inputs, and a metamorphic relation between outputs (metadata-invariance of the
> scrubbed result). The self-determinism baseline is the identity relation. We
> keep the "differential" name and implement metamorphic relations; both are the
> standard answer to the test-oracle problem (no known-correct "correctly scrubbed
> file" exists).

---

## 2. Two contracts, not one

The harness separates *what we test* from *format knowledge we use to test*
(`contract.py`):

1. **`Scrubber`** — *the thing under test.* A black box: `run(in_path, out_path,
   fidelity) -> ScrubResult`. Implementations: `SubprocessScrubber` (wraps a real
   tool via a `{in} {out} {fidelity}` command template) plus the Python stub
   scrubbers used as fixtures. It may be written in any language and shipped as a
   binary; the harness language is independent of it.
2. **`FormatPlugin`** — *harness-side format knowledge.* `matches()` (magic
   dispatch), `annotate(offset) -> structure_name`, `canonical_content()` (for
   content-identity in feature space), `mandatory_constants()` (format-required
   invariants the fingerprint guard must exclude). Phase 0 ships `ToyFormatPlugin`
   (synthetic) and `GenericPlugin` (format-agnostic defaults), both in
   `dispatch.py`. Real plugins arrive in later format-handler phases.

They have different lifecycles (one is the product, one is test infrastructure)
and different authors. Keeping them apart is what lets the harness test a scrubber
it knows nothing about while still gaining format-specific localization when a
plugin exists.

---

## 3. Corpus — synthetic primary, real-seed secondary

Both generators ship; they prove different things.

- **Synthetic (primary) gives *soundness*.** `corpus/toy_format.py` is a minimal
  TOYF codec (`MAGIC | content_len | content | TLV metadata`) that cleanly
  separates a content region from a metadata region. `corpus/synthetic.py` writes
  byte-identical content into every member of a set, so **content-identity is a
  theorem, not a measurement** — when a synthetic test fails, the bug is in the
  harness, not in some library's re-encoding quirk. Each generator decodes and
  re-compares its own output as a self-check.
  - `make_a1_variants(content, n_variants=3, n_repeats=5, tmpdir)` — identical
    content; variant *i* carries one record `(type=7, value=sentinel_i)` where
    sentinels are `AAAA…`/`BBBB…`/`CCCC…`. Sentinels make "the diff correlates
    with the variant" a **decidable, self-localizing** membership test. ≥3
    variants give the correlation test signal to separate from the floor.
  - `make_a2_corpus(content, n_sources=2, members_per_source=3, tmpdir)` —
    identical content across all members and sources; source *s* carries a
    per-source marker `(type=0xA0, value=b"SRC{s}")` plus a per-member incidental
    record `(type=0x10, value=random)`.
- **Real-seed (secondary) gives *external validity*.** It proves the harness
  survives contact with real bytes, where content-identity must be **verified, not
  assumed** (injection tools can perturb content). `corpus/content_identity.py`
  extracts canonical content and compares — exact-hash for F1/F2, perceptual for
  F3 — and a pair that fails is rejected as a corpus bug. The injection path
  (`corpus/inject.py`, ExifTool default + `piexif`/`mutagen`/`pikepdf`/`zipfile`
  fallbacks, each emitted variant content-verified or rejected) is implemented but
  **secondary and optional**: there are no real format scrubbers yet, so the
  optional Phase-0 real run uses `exiftool -all=` as a real black-box scrubber over
  local JPEGs. Real seeds live
  at `~/metadata-research/step2/originals/` (`config.REAL_SEED_DIR`), are absent in
  this environment, and every use is guarded with an existence check + `pytest.skip`.

---

## 4. F3 — perceptual identity is a content-preservation guard, separate from the leak oracle

For F3 the content is *expected* to change (lossy re-encode), so byte offsets are
meaningless and the oracle lifts entirely into feature space. Two distinct jobs,
never conflated:

- **Content preservation (a hard constraint):** confirm the scrubbed output is
  perceptually the *same content*. Images: `imagehash.phash`, Hamming distance
  below a threshold. Audio: Chromaprint via `fpcalc -raw`, raw-fingerprint
  correlation. The threshold is **calibrated per format** against a same-content
  re-encode baseline — never hard-coded (re-encode the same content twice, take
  the max observed distance as the floor, set the threshold just above it).
  Video is a documented `NotImplementedError` stub.
- **Leak detection (the irrecoverability constraint):** the §1 variance
  decomposition over the F3 feature vector — ExifTool fields (`field:` prefix) +
  structural features (`struct:` prefix: segment/atom/chunk inventory, lengths,
  padding) — with the floor as the threshold. This feature vector is what makes
  "all duplicate loci, not just named tags" real in feature space.

Perceptual hashing answers "did the content survive?"; the variance engine answers
"did metadata leak?" — different modules, different failure meanings. In
`oracle/leak.py` an F3 cell that fails content preservation fails *independently*
with `reason="content_not_preserved"`, recorded in `Cell.perceptual`.

**Phase 0 status:** F1/F2 byte-space oracles, the floor, A1, A2, and the
fingerprint guard are fully implemented and gated. The F3 feature/perceptual
modules are present too: `oracle/fields.py` (ExifTool + structural extraction)
and `oracle/perceptual.py` (image pHash now; audio Chromaprint lazily, video a
documented `NotImplementedError` stub). `oracle/perceptual.py` and `imagehash`/
`PIL` import at module top, but the audio path is fully lazy so the module imports
cleanly with no `fpcalc` present and only fails when the audio path is actually
called. The F3 branch of `oracle/leak.py` imports these lazily, so the F1/F2 paths
stay import-safe regardless. F3 thresholds are required parameters, never
hard-coded.

---

## 5. Scrubber-fingerprint guard

Same engine, grouping factor = **input** (vary content, metadata, and source —
everything). A feature invariant across *all* inputs is a candidate tool
signature; subtract the format's declared `mandatory_constants()` (magic numbers,
structural invariants) and the remainder is the scrubber's fingerprint — a
producer string, padding habit, mtime stamp, or non-standard ordering that
identifies the tool and is itself an A2 vector against the tool's users as a class.
Because a tool signature is *constant across variants*, the A1 per-pair oracle
correctly sees no variant-correlated leak and passes it; the signature is only
visible under input-invariance. This is why the guard is a **separate check** —
demonstrated by `version_stub`, which passes A1 and is caught only here. The guard
is position-independent (a trailing tag shifts with content length), so it uses
common substrings, not offset alignment (`oracle/fingerprint_guard.py`).

---

## 6. Results — the Pareto matrix

One JSON document per (format, tool), validated against
`schemas/pareto_matrix.schema.json` by `runner/matrix.py`. Cells are keyed by
(adversary, fidelity) over {A1, A2} × {F1, F2, F3}, each with `verdict`
(`pass`/`fail`/`not_applicable`/`not_tested`), the measured `noise_floor`, a list
of `leaks` with evidence pointers, and an F3-only `perceptual` block. The
tool-global `scrubber_fingerprint` block sits outside the cells.

`matrix.assemble(...)` also **emits A3 cells** for every fidelity with
`verdict:"not_tested"`, `reason:"out_of_scope_threat_model"` — keeping the same
shape as the research matrices while honestly recording that A3 was never tested.
No A3-resistance check is ever built. `matrix.write(doc, path)` dumps JSON and then
`validate()`s it against the schema. Tracked matrices go to `results/<format>_<tool>.json`;
bulk evidence goes to `results/evidence/` and is gitignored (`results/.gitignore`
ignores `evidence/`, keeps `*.json`).

---

## 7. File layout (`tests/harness/`)

```
tests/harness/
  README.md                     # this file (authoritative)
  README (1).md                 # earlier brief note (superseded)
  requirements.txt              # pinned Python deps (system binaries are NOT pip-installable)
  __init__.py
  contract.py                   # V, ScrubResult, Scrubber, FormatPlugin, Locus, Leak,
                                 #   FloorReport, PerceptualReport, Cell, SubprocessScrubber
  dispatch.py                   # Dispatcher, GenericPlugin, ToyFormatPlugin
  config.py                     # N_REPEATS=5, MIN_SIG_LEN=4, HEX_CONTEXT=16, N_VARIANTS=3,
                                 #   N_SOURCES=2, MEMBERS_PER_SOURCE=3, HARNESS_VERSION, paths
  corpus/
    __init__.py
    toy_format.py               # TOYF codec: MAGIC, pack, unpack, read, write
    synthetic.py                # make_a1_variants, make_a2_corpus,
                                 #   A1_SENTINEL_FIELD, A2_MARKER_TYPE
    content_identity.py         # canonical_content, same_content
    inject.py                   # real-seed injection (exiftool default + piexif/mutagen/
                                 #   pikepdf/zipfile fallbacks); content-verified or rejected
  oracle/
    __init__.py
    variance.py                 # THE engine: FeatureMap, FeatureVerdict, decompose(cells, groups, repeats)
    diff.py                     # align_features, hex_context, locus_from, to_feature_maps
                                 #   feature ids: byte@{off}:{len}, byte_tail@{first_div}, length_divergence
    floor.py                    # measure(scrubber, plugin, input_path, fidelity, n) -> (FloorReport, repeats)
    leak.py                     # evaluate_a1 (variant × repeat)
    peerset.py                  # evaluate_a2 (source × repeat)
    fingerprint_guard.py        # common_substrings, maximal, evaluate -> (verdict_str, detail_list)
    fields.py                   # F3: exiftool_fields + structural_features + extract (field:/struct:)
    perceptual.py               # F3 content-preservation: image pHash, audio Chromaprint (lazy),
                                 #   video NotImplementedError stub; check_preserved
  stubs/
    __init__.py
    clean_stub.py               # CleanStub      (REQUIRED) strips all metadata -> deterministic, A1 pass
    leaky_stub.py               # LeakyStub      (REQUIRED) keeps first record  -> variant-correlated A1 leak
    version_stub.py             # VersionStub    (REQUIRED) appends TOYSCRUB/1.2.3 -> A1 pass, guard FAIL
    noisy_clean_stub.py         # NoisyCleanStub (OPTIONAL) per-run nonce -> non-empty floor, not a leak
    per_source_stub.py          # PerSourceStub  (OPTIONAL) keeps 0xA0 marker -> A2 source fingerprint
  runner/
    __init__.py
    cases.py                    # Case model
    run.py                      # run_case / run_suite orchestration
    matrix.py                   # assemble (emits A3 not_tested), write, validate
  schemas/
    pareto_matrix.schema.json   # Pareto-matrix JSON schema (draft-07)
  results/
    .gitignore                  # ignore evidence/, keep *.json matrices
  tests/
    __init__.py
    conftest.py                 # fixtures (see below)
    test_toy_format.py
    test_variance.py
    test_diff.py
    test_floor.py
    test_dispatch.py
    test_fingerprint_guard.py
    test_peerset_a2.py          # OPTIONAL gate (AC5)
    test_exit_gate.py           # REQUIRED gate (AC1–AC3)
```

The F3 modules (`oracle/fields.py`, `oracle/perceptual.py`) and the real-seed
injector (`corpus/inject.py`) are present but exercised only on the secondary /
F3 paths, which `pytest.skip` when their system binaries (`exiftool`, `fpcalc`) or
local real seeds are absent. The F3 branch of `oracle/leak.py` imports the F3
modules lazily, so the F1/F2 exit-gate oracles remain import-safe regardless.

### Test fixtures (`tests/conftest.py`)

Reuse these in unit tests; do not redefine them: `content`, `toy_plugin`,
`clean_stub`, `leaky_stub`, `version_stub`, `noisy_clean_stub`, `per_source_stub`,
`a1_variants` (factory: `a1_variants(n_variants=3, n_repeats=5)`), `a2_corpus`
(factory), `toyf_file` (a single TOYF path), `diverse_inputs` (varied TOYF paths
for the guard). Tests use absolute imports, e.g. `from tests.harness.oracle import variance`.

---

## 8. How to run

Use the repo's venv. From the repo root
(`/Users/Moe/Desktop/meta-data-manager`):

```bash
# install pinned Python deps (one time)
./.venv/bin/python -m pip install -r tests/harness/requirements.txt

# run the whole harness suite
cd /Users/Moe/Desktop/meta-data-manager && ./.venv/bin/python -m pytest tests/harness -q

# just the required exit gate
./.venv/bin/python -m pytest tests/harness/tests/test_exit_gate.py -q
```

System binaries (`exiftool`, `ffmpeg`, `fpcalc`) are **not** pip-installable —
install them separately (macOS: `brew install exiftool ffmpeg chromaprint`).
`fpcalc` (chromaprint) is optional: any audio-fingerprint path must degrade or
`pytest.skip` at runtime and must never crash at import.

---

## 9. Phase 0 exit gate

Run `pytest tests/harness -q`. The gate is satisfied iff **AC1–AC3** pass (the
three REQUIRED stub scrubbers over the synthetic TOYF corpus):

- **AC1 — leaky flagged (REQUIRED).** `evaluate_a1(LeakyStub, ToyFormatPlugin,
  make_a1_variants(content, n_variants=3), "F1")` ⇒ `Cell.verdict == FAIL` with
  ≥1 `Leak(kind="variant_correlated")` whose locus falls in the surviving record's
  value bytes and whose `correlates_with` names the sentinel field; floor empty
  (`deterministic == True`).
- **AC2 — clean passes (REQUIRED).** Same call with `CleanStub` ⇒
  `Cell.verdict == PASS`, `leaks == []`, `noise_floor.deterministic == True`,
  `variable_loci == []`.
- **AC3 — fingerprint guard catches the version stub (REQUIRED).**
  `evaluate_a1(VersionStub, …)` ⇒ `PASS` (no variant-correlated leak — proving
  independence), while `fingerprint_guard.evaluate(VersionStub, ToyFormatPlugin,
  diverse_inputs, "F1")` ⇒ `"fail"` with a signature decoding to `TOYSCRUB/1.2.3`;
  and `fingerprint_guard.evaluate(CleanStub, …)` ⇒ `"pass"`.

Optional/recommended acceptance: **AC4** (non-empty floor handled — `NoisyCleanStub`
nonce classified as floor, not a leak, no false positive) and **AC5** (A2 peer-set
— `PerSourceStub` over a 2-source corpus yields a `source_fingerprint` and FAIL,
`CleanStub` over the same corpus passes). **AC-matrix:** every produced matrix
validates against the schema and carries A3 cells marked
`not_tested / out_of_scope_threat_model`.

---

## 10. Phase 0 boundaries (do not cross)

- No real-format parsing in any scrubber or in the engine. `ToyFormatPlugin`
  offset mapping is allowed (TOYF is test infrastructure). ExifTool/ffmpeg/fpcalc
  are external subprocess tools only — never linked in.
- Build only A1 + A2 verification. A3 cells are emitted as `not_tested`; no A3
  checks exist.
- Video perceptual identity is a documented `NotImplementedError` stub.
- Real-seed scrubbing is limited (no format handlers exist); the optional real
  path uses `exiftool -all=` as a benchmark black-box scrubber, gated on local
  files via `pytest.skip`.
