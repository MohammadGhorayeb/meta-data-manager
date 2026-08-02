<!-- qa-report -->
# 🛡️ Irreversible Metadata Scrubber — Quality Report

![QA: passing](https://img.shields.io/badge/QA-passing-brightgreen?style=for-the-badge&logo=github&logoColor=white) ![checks passed: 616](https://img.shields.io/badge/checks_passed-616-2f9e44?style=for-the-badge) ![failed: 0](https://img.shields.io/badge/failed-0-lightgrey?style=for-the-badge) ![coverage: 86%](https://img.shields.io/badge/coverage-86%25-green?style=for-the-badge)

![python: 3.11 | 3.12 | 3.13 | 3.14](https://img.shields.io/badge/python-3.11_|_3.12_|_3.13_|_3.14-3776ab?style=flat-square&logo=python&logoColor=white) ![formats: JPEG PNG MP3](https://img.shields.io/badge/formats-JPEG_PNG_MP3-1971c2?style=flat-square) ![threat model: medium-tier (A2)](https://img.shields.io/badge/threat_model-medium--tier_(A2)-5f3dc4?style=flat-square)

### ✅ Everything passed — all 616 checks are green

Nothing leaked, no file was damaged, and a scrubbed file still cannot be traced back to the device that made it.

> **What this tool does, in one sentence.** It permanently removes the hidden information a file carries about you — where a photo was taken, which phone took it, when, and what was edited — without changing what the file looks or sounds like.

---

## 🗺️ Where it ran, and where it broke

```mermaid
flowchart LR
    START(["📥 Code change"])
    LINT["🔍 Code quality<br/>✅"]
    PY311["🐍 Python 3.11<br/>✅ 154 checks"]
    PY312["🐍 Python 3.12<br/>✅ 154 checks"]
    PY313["🐍 Python 3.13<br/>✅ 154 checks"]
    PY314["🐍 Python 3.14<br/>✅ 154 checks"]
    EVID["🔬 Results still true<br/>✅"]
    COV["📊 Coverage<br/>✅ 86%"]
    REPORT["📋 This report"]
    END(["✅ Verdict"])
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
    class START,LINT,PY311,PY312,PY313,PY314,EVID,COV,REPORT,END ok
    classDef ok fill:#d3f9d8,stroke:#2f9e44,stroke-width:1px,color:#000
    classDef bad fill:#ffc9c9,stroke:#e03131,stroke-width:3px,color:#000
    classDef idle fill:#e9ecef,stroke:#adb5bd,color:#495057
```

> Every box is green: the code was checked, the tests ran on every supported version of Python, coverage held, and the published results were re-confirmed from scratch.

---

## 📋 The five-second version

| | Stage | Result | Took | What this checks |
|:--:|---|:--:|--:|---|
| 🔍 | **Code quality** | ✅ 1 issues | 22s | Checks the code itself is tidy and consistent, and that the automation script has no mistakes. |
| 🧪 | **Test suite** | ✅ 616 passed | 3m 16s | Runs every automated check against real files, on four versions of Python. |
| 📊 | **Coverage** | ✅ 85.7% | 19s | Measures how much of the tool's code the tests actually exercise. |
| 🔬 | **Published results still true** | ✅ success | 1m 03s | Re-measures the tool's headline claims and confirms the published results table still matches reality. |

---

**Coverage:** `██████████████████████████░░░░` **85.7%**

---

<sub>📋 The full report — area-by-area results, what the tool can promise for each file type, its honest limits, and before/after evidence on real files — is on the [Actions run page](#).</sub>
