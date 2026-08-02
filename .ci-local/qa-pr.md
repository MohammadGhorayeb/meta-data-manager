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

**Coverage:** `██████████████████████████░░░░` **85.5%**

---

<sub>📋 The full report — area-by-area results, what the tool can promise for each file type, its honest limits, and before/after evidence on real files — is on the [Actions run page](#).</sub>
