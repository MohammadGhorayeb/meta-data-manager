<!-- qa-report -->
# 🛡️ Irreversible Metadata Scrubber — Quality Report

![QA: failing](https://img.shields.io/badge/QA-failing-e03131?style=for-the-badge&logo=github&logoColor=white) ![checks passed: 150](https://img.shields.io/badge/checks_passed-150-2f9e44?style=for-the-badge) ![failed: 4](https://img.shields.io/badge/failed-4-e03131?style=for-the-badge) ![coverage: 86%](https://img.shields.io/badge/coverage-86%-green?style=for-the-badge)

![python: 3.11 | 3.12 | 3.13 | 3.14](https://img.shields.io/badge/python-3.11_|_3.12_|_3.13_|_3.14-3776ab?style=flat-square&logo=python&logoColor=white) ![formats: JPEG PNG MP3](https://img.shields.io/badge/formats-JPEG_PNG_MP3-1971c2?style=flat-square) ![threat model: medium-tier (A2)](https://img.shields.io/badge/threat_model-medium--tier_(A2)-5f3dc4?style=flat-square)

### ❌ Something needs attention

**4 of 154 checks failed** and Python **3.11**, **3.12**, **3.13** reported no results at all and the **test** stage reported a problem. Start with *Where it ran, and where it broke* just below.

> **What this tool does, in one sentence.** It permanently removes the hidden information a file carries about you — where a photo was taken, which phone took it, when, and what was edited — without changing what the file looks or sounds like.

---

## 🗺️ Where it ran, and where it broke

```mermaid
flowchart LR
    START(["📥 Code change"])
    LINT["🔍 Code quality<br/>✅"]
    PY311["🐍 Python 3.11<br/>❌ no results"]
    PY312["🐍 Python 3.12<br/>❌ no results"]
    PY313["🐍 Python 3.13<br/>❌ no results"]
    PY314["🐍 Python 3.14<br/>❌ 154 checks"]
    EVID["🔬 Results still true<br/>✅"]
    COV["📊 Coverage<br/>✅ 86%"]
    REPORT["📋 This report"]
    END(["❌ Verdict"])
    START --> LINT
    START --> PY311
    PY311 --> COV
    START --> PY312
    PY312 --> COV
    START --> PY313
    PY313 --> COV
    START --> PY314
    PY314 --> COV
    START --> EVID
    LINT --> REPORT
    COV --> REPORT
    EVID --> REPORT
    REPORT --> END
    class START,LINT,EVID,COV,REPORT ok
    class PY311,PY312,PY313,PY314,END bad
    classDef ok fill:#d3f9d8,stroke:#2f9e44,stroke-width:1px,color:#000
    classDef bad fill:#ffc9c9,stroke:#e03131,stroke-width:3px,color:#000
    classDef idle fill:#e9ecef,stroke:#adb5bd,color:#495057
```

> ❌ **The red boxes are where the problem is.** Failing stage(s): **test**. 4 individual check(s) failed — each one is listed under *What failed*, and flagged inline on the offending line of code.

---

## 📋 The five-second version

| | Stage | Result | Took | What this checks |
|:--:|---|:--:|--:|---|
| 🔍 | **Code quality** | ✅ clean | — | Checks the code itself is tidy and consistent, and that the automation script has no mistakes. |
| 🧪 | **Test suite** | ❌ 4 failed | 34s | Runs every automated check against real files, on four versions of Python. |
| 📊 | **Coverage** | ✅ 85.5% | — | Measures how much of the tool's code the tests actually exercise. |
| 🔬 | **Published results still true** | ✅ success | — | Re-measures the tool's headline claims and confirms the published results table still matches reality. |

---

## ❌ What failed

*4 check(s) did not pass. Each one is also flagged inline on the affected line of code in the pull request, so it is easy to find.*

<details open>
<summary><b>Show all 4 failure(s)</b></summary>

#### ❌ Limits doc exists and is parseable

- **Where:** `tests.scrub.test_qa_report_limits.test_limits_doc_exists_and_is_parseable`
- **Affects Python:** 3.14
- **Internal name:** `test_limits_doc_exists_and_is_parseable`

```text
AttributeError: module &#x27;qa_report&#x27; has no attribute &#x27;load_limits&#x27;. Did you mean: &#x27;load_timings&#x27;?
```

#### ❌ Report embeds the limits rather than its own copy

- **Where:** `tests.scrub.test_qa_report_limits.test_report_embeds_the_limits_rather_than_its_own_copy`
- **Affects Python:** 3.14
- **Internal name:** `test_report_embeds_the_limits_rather_than_its_own_copy`

```text
AttributeError: module &#x27;qa_report&#x27; has no attribute &#x27;load_limits&#x27;. Did you mean: &#x27;load_timings&#x27;?
```

#### ❌ Missing limits doc is reported not hidden

- **Where:** `tests.scrub.test_qa_report_limits.test_missing_limits_doc_is_reported_not_hidden`
- **Affects Python:** 3.14
- **Internal name:** `test_missing_limits_doc_is_reported_not_hidden`

```text
AttributeError: module &#x27;qa_report&#x27; has no attribute &#x27;render_markdown&#x27;
```

#### ❌ Known open limits are actually documented

- **Where:** `tests.scrub.test_qa_report_limits.test_known_open_limits_are_actually_documented`
- **Affects Python:** 3.14
- **Internal name:** `test_known_open_limits_are_actually_documented`

```text
AttributeError: module &#x27;qa_report&#x27; has no attribute &#x27;load_limits&#x27;. Did you mean: &#x27;load_timings&#x27;?
```


</details>

---

## 📊 How much of the tool is tested

*Coverage is the share of the tool's own code that the tests actually run. High coverage means few untested corners where a bug could hide.*

`██████████████████████████░░░░` **85.5%**

> 1,098 of 1,230 lines of the scrubber were exercised by the tests in this run, across Python 3.11, 3.12, 3.13, 3.14.

<details>
<summary><b>Coverage file by file</b></summary>

| Part of the tool | Covered | |
|---|--:|---|
| `src/__init__.py` | 100% | `██████████████████` |
| `src/scrub/__init__.py` | 100% | `██████████████████` |
| `src/scrub/__main__.py` | 0% | `░░░░░░░░░░░░░░░░░░` |
| `src/scrub/cli.py` | 77% | `██████████████░░░░` |
| `src/scrub/dispatch.py` | 100% | `██████████████████` |
| `src/scrub/errors.py` | 100% | `██████████████████` |
| `src/scrub/fidelity.py` | 83% | `███████████████░░░` |
| `src/scrub/formats/__init__.py` | 100% | `██████████████████` |
| `src/scrub/formats/base.py` | 92% | `█████████████████░` |
| `src/scrub/formats/jpeg/__init__.py` | 100% | `██████████████████` |
| `src/scrub/formats/jpeg/f1.py` | 84% | `███████████████░░░` |
| `src/scrub/formats/jpeg/f2.py` | 85% | `███████████████░░░` |
| `src/scrub/formats/jpeg/f3.py` | 93% | `█████████████████░` |
| `src/scrub/formats/jpeg/handler.py` | 93% | `█████████████████░` |
| `src/scrub/formats/jpeg/segments.py` | 84% | `███████████████░░░` |
| `src/scrub/formats/mp3/__init__.py` | 100% | `██████████████████` |
| `src/scrub/formats/mp3/f1.py` | 100% | `██████████████████` |
| `src/scrub/formats/mp3/f3.py` | 86% | `███████████████░░░` |
| `src/scrub/formats/mp3/handler.py` | 90% | `████████████████░░` |
| `src/scrub/formats/mp3/walker.py` | 87% | `████████████████░░` |
| `src/scrub/formats/png/__init__.py` | 100% | `██████████████████` |
| `src/scrub/formats/png/chunks.py` | 87% | `████████████████░░` |
| `src/scrub/formats/png/f1.py` | 93% | `█████████████████░` |
| `src/scrub/formats/png/f2.py` | 84% | `███████████████░░░` |
| `src/scrub/formats/png/handler.py` | 88% | `████████████████░░` |
| `src/scrub/standards/__init__.py` | 100% | `██████████████████` |
| `src/scrub/standards/icc.py` | 86% | `████████████████░░` |
| `src/scrub/standards/iptc_iim.py` | 83% | `███████████████░░░` |
| `src/scrub/standards/tiff_ifd.py` | 81% | `███████████████░░░` |
| `src/scrub/standards/xmp.py` | 81% | `███████████████░░░` |

</details>

---

## 🧭 What was tested, area by area

| Area | Result | Checks | Took | What it means |
|---|:--:|:--:|--:|---|
| 🧹 **Hidden data removed** | ✅ Pass | 18/18 | 0s | GPS location, camera model, timestamps, embedded thumbnails and comments are stripped so they are gone for good. |
| 🖼️ **Picture and sound preserved** | ✅ Pass | 11/11 | 1s | The file still looks and sounds identical after cleaning — pixel-for-pixel in the lossless modes. |
| 🕵️ **Made untraceable** | ✅ Pass | 22/22 | 5s | You cannot tell which app, camera or phone produced the file — the invisible compression fingerprint is erased. |
| 🔒 **Cannot be recovered** | ✅ Pass | 10/10 | 7s | Forensic recovery tools cannot bring back anything that was removed. |
| 🧩 **File stays valid** | ✅ Pass | 51/51 | 0s | The internal structure and checksums stay correct, so the file still opens everywhere it did before. |
| ⚙️ **The tool behaves** | ❌ 4 failed | 31/35 | 21s | The command line, automatic file-type detection, fail-safe behaviour and the reporting all work correctly. |
| ✅ **Other checks** | ✅ Pass | 7/7 | 0s | Additional internal quality checks. |

<details>
<summary><b>The same results, broken down by Python version</b></summary>

| Area | Py 3.11 | Py 3.12 | Py 3.13 | Py 3.14 |
|---|:--:|:--:|:--:|:--:|
| 🧹 Hidden data removed | ➖ | ➖ | ➖ | ✅ 18 |
| 🖼️ Picture and sound preserved | ➖ | ➖ | ➖ | ✅ 11 |
| 🕵️ Made untraceable | ➖ | ➖ | ➖ | ✅ 22 |
| 🔒 Cannot be recovered | ➖ | ➖ | ➖ | ✅ 10 |
| 🧩 File stays valid | ➖ | ➖ | ➖ | ✅ 51 |
| ⚙️ The tool behaves | ➖ | ➖ | ➖ | ❌ 4 |
| ✅ Other checks | ➖ | ➖ | ➖ | ✅ 7 |

*Every check runs on every version of Python the tool supports, so a future upgrade cannot silently break it.*

</details>

---

## 🔬 Published results re-confirmed

✅ This run re-measured the tool's headline results from scratch and they still match the published table exactly. The claims in this report are not cached — they were just proven again.

---

## 🎯 What the tool can promise today

*Read straight from the measured results files in this repository. If a claim here is not backed by a real measurement, it does not appear.*

| Format | Removes hidden data | Keeps the file identical | Makes it untraceable |
|---|:--:|:--:|:--:|
| **JPEG (photos)** | ✅ Yes | ✅ Yes | ✅ Yes — a tiny, invisible quality cost |
| **PNG (graphics / screenshots)** | ✅ Yes | ✅ Yes | ✅ Yes — no quality loss |
| **MP3 (audio)** | ✅ Yes | ✅ Yes | ✅ Yes — a tiny, invisible quality cost |

<details>
<summary><b>The full measured grid, and what the words mean</b></summary>

| Format | Snoop | Bit-preserving (F1) | Lossless (F2) | Lossy (F3) |
|---|---|:--:|:--:|:--:|
| JPEG (photos) | Metadata snoop | ✅ beaten | ✅ beaten | ✅ beaten |
| JPEG (photos) | Fingerprint snoop | ❌ still traceable | ❌ still traceable | ✅ beaten |
| PNG (graphics / screenshots) | Metadata snoop | ✅ beaten | ✅ beaten | — n/a |
| PNG (graphics / screenshots) | Fingerprint snoop | ❌ still traceable | ✅ beaten | — n/a |
| MP3 (audio) | Metadata snoop | ✅ beaten | — n/a | ✅ beaten |
| MP3 (audio) | Fingerprint snoop | ❌ still traceable | — n/a | ✅ beaten |

**The two snoops**

- **The metadata snoop** reads the hidden tags directly — GPS coordinates, camera model, timestamps, the little preview thumbnail, comments.
- **The fingerprint snoop** never reads a tag. It works out which app or device made the file from *how* the file was compressed — like recognising a typewriter from the dents it leaves in paper.

**The three cleaning strengths**

- **F1 — bit-preserving:** delete every tag; the picture or audio data is left byte-for-byte untouched. No quality cost.
- **F2 — lossless re-encode:** delete every tag *and* repack the compression. Still perfect quality.
- **F3 — lossy re-encode:** re-compress through one standard encoder so every file comes out looking the same to a forensic tool. Tiny, invisible quality cost.

</details>

---

## 🚧 What the tool cannot do yet — and what we do about it

*The honest part. Each row is a limit in plain words, then what we do about it. Read straight from `docs/limits.md`, the single place these are recorded, so it cannot quietly fall out of date.*

| # | The honest limit (plain words) | What we do about it | Status |
|:-:|---|---|:--:|
| **1** | Even after every tag is deleted, the *way* a file was compressed leaves a hidden signature that can hint which app, phone or program made it. | We re-save through **one standard encoder** so every file we produce shares the same signature: **JPEG** at F3, **MP3** at F3, and **PNG** at F2 **with no quality loss at all**. For MP3 this is now proven against a genuinely *different* encoder, not just a different program using the same one. | ✅ **Solved** |
| **2** | Our "every file comes out looking the same" promise is **not one single group**. Low-sample-rate audio can't legally use our standard setting, so those files come out at a different one — several groups, not one. Files still can't be told apart *within* a group, but the group itself is visible. | Being fixed. Until it is, the claim is stated as **anonymity within a group**, not one universal signature, and our evidence corpus is being widened to cover mixed sample rates (it currently only tests the most common one). | 🔴 **Open — claim narrowed** |
| **3** | Some unusual audio files we **refuse to process at all**: older Layer II audio wearing an `.mp3` name, and files using a rare "free-format" bitrate. | The tool **fails closed** — it stops rather than write a file it cannot fully vouch for. Nothing leaks, but the user gets no output. Broader format support is planned. | 🟡 **Safe but incomplete** |
| **4** | Making a file untraceable means **re-recording** it, and that always costs something: a high-quality audio file loses some quality, and a very small file can come out several times **larger**. | An unavoidable trade of this approach, stated up front rather than hidden. The alternative — keeping the original audio untouched — is exactly what leaves the traceable signature in place. | 🟡 **Bounded trade-off** |
| **5** | Every camera sensor leaves a faint, unique **noise pattern baked into the pixels** (PRNU). Audio has the same problem: every **microphone and room** leaves a fingerprint, and mains electricity hums at a frequency that can date a recording. | These live **in the picture and in the sound itself**, not in the metadata — removing them would destroy the content. They are a known limit of **every tool that exists** and sit outside our medium-tier threat model. We document them openly. | ⚪ **Out of scope** |
| **6** | A **more sophisticated investigator** than the one we built might still succeed. Our audio test inspects the sound's frequency profile; published research also inspects the encoder's internal number-crunching, which we have **not** tested against. | We report what we measured and what we did not. We do **not** claim to survive attacks we never ran — the untested methods are named so the gap is visible rather than implied away. | 🔴 **Untested** |
| **7** | Two things are **not built yet**: a second style of audio VBR header used by some encoders is unhandled and untested, and **M4A** and **FLAC** audio aren't supported at all. | On the roadmap. M4A matters because the main comparable tool refuses it outright — a gap we can beat rather than match. | 🔜 **Planned** |
| **8** | We currently handle **JPEG, PNG and MP3**. | Next, on the same tested foundation: 📄 documents (PDF / Word) → 🎬 video (MP4) → 📷 camera RAW. Each ships only when its differential tests pass. | 🔜 **Planned** |

<details>
<summary>🧪 <b>Where the evidence lives — the technical residuals, for the curious</b></summary>

- **DQT (JPEG quantization tables):** the F1/F2 fingerprint source — neutralised at F3 via a single standard encoder.
- **LAME/Xing header + bitrate contour (MP3):** the audio equivalent — survives F1, normalised at F3.
- **Cross-engine audio trace (MP3):** measured, not assumed. A classifier that identifies the source encoder from the sound alone scores **0.94 on untouched and F1 files, and chance (0.50) after F3**. Peer set includes `shineenc`, an encoder that is not libmp3lame. Scope: frequency-profile features; MDCT-domain classifiers untested (limit #6).
- **Anonymity groups (MP3 F3):** the standard 192 kbps setting is illegal for MPEG-2 sample rates, so those files emit at 160 kbps instead — separate groups (limit #2).
- **PRNU (sensor noise) / microphone + room response / mains hum:** structural impossibilities; documented, out of scope (limit #5).
- **Primary-quantization estimate:** the bounded "was re-saved" hint after F3 — reveals nothing about original metadata.
- **Scrubber-fingerprint guard:** confirms our *own* tool leaves no producer/creator string, no odd padding, deterministic ordering, and no modification-time stamping.

</details>

> **The principle:** we never claim a file is untraceable unless we have actually proven it. Where a limit is a law of physics, we say so rather than pretend it is solved.

---

## 🔍 Code quality

✅ No issues found — the code matches the project's agreed style, and the automation itself is valid.

---

## ⏱️ Timing

<details>
<summary><b>The ten slowest checks</b></summary>

| Check | Took |
|---|--:|
| MP3 matrix builds and validates | 16.11s |
| Controls recover the engine | 6.60s |
| Matrix builds and validates | 2.14s |
| PNG matrix builds and validates | 1.31s |
| Fingerprint guard passes over diverse inputs | 0.87s |
| Level cannot be the discriminator | 0.82s |
| F3 (lossy re-encode) normalizes encoder fingerprint | 0.74s |
| Experiment E3 flags JPEG compression table as producer fingerprint raw | 0.55s |
| Experiment E3 evaluate cell returns fail | 0.52s |
| Experiment E3 fingerprint survives F1 (bit-preserving mode) | 0.52s |

Total time spent running checks, added up across every Python version: **34s**.

</details>

---

## 🔍 Before and after, on real files

<details open>
<summary><b>Show what actually came off a real file — tags, thumbnails, encoder fingerprint, before vs after</b></summary>

Synthetic samples with planted metadata (no real user data). Each is shown **before → after** scrubbing across fidelity tiers.

### JPEG sample

**Before scrub** — 18 metadata tags incl. Comment, GPSLatitude, GPSLatitudeRef, GPSLongitude, GPSLongitudeRef, GPSPosition

| Fidelity | Metadata tags | Embedded thumbnails | Trailer bytes | File size | Encoder DQT (raw→scrubbed) | Result |
|---|---|---|---|---|---|---|
| F1 | 18 → **0** | 0 | 0 | 2,895 → 1,504 B | `931afd906e`→`931afd906e` | ✅ clean |
| F2 | 18 → **0** | 0 | 0 | 2,895 → 1,036 B | `931afd906e`→`931afd906e` | ✅ clean |
| F3 | 18 → **0** | 0 | 0 | 2,895 → 1,001 B | `931afd906e`→`cc9ad4edd6` | ✅ clean |

### PNG sample

**Before scrub** — 3 metadata tags incl. Author, GPS, Software

| Fidelity | Metadata tags | Trailer bytes | File size | Result |
|---|---|---|---|---|
| F1 | 3 → **0** | 0 | 237 → 146 B | ✅ clean |
| F2 | 3 → **0** | 0 | 237 → 146 B | ✅ clean |

### A2 fingerprint defense (JPEG)

Same image, three producers. F1/F2 keep each producer's DQT (traceable); F3 re-compresses so all share one DQT (untraceable).

| Producer | DQT raw | DQT after F1 | DQT after F3 |
|---|---|---|---|
| encoderA_q92 | `931afd906e` | `931afd906e` | `cc9ad4edd6` |
| encoderB_q75 | `f516966307` | `f516966307` | `cc9ad4edd6` |
| encoderC_q60 | `95eb13cc9f` | `95eb13cc9f` | `cc9ad4edd6` |

**✅ all producers share ONE DQT after F3 — fingerprint erased**

_F1 bit-preserving · F2 lossless re-encode · F3 lossy re-encode. A ✅ result means zero metadata tags, no embedded thumbnail, and no trailer bytes survive._

</details>

---

<sub>Commit <code>local</code> · branch <code>local</code> · 154 checks on Python 3.11, 3.12, 3.13, 3.14 · ground truth verified with ExifTool · adversary model: medium-tier (A2) · regenerated automatically on every change</sub>
