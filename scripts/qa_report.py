"""Plain-language QA report for the GitHub Actions run page.

Turns the raw outputs of a CI run — JUnit XML (one per Python version), coverage
JSON, ruff JSON, the evidence-freshness check, per-job timings and the Pareto
matrices — into a single Markdown document that renders natively on the Actions
run page (Job Summary) and, condensed, as a PR comment.

The audience is explicitly non-technical: someone who has never seen this
codebase should open the report and understand what was tested, what passed,
what failed, and what that means for them. Everything is derived from the run's
own artifacts — nothing about the tool's capabilities is hardcoded, because a
report that can silently drift out of date is worse than no report.

Usage (see .github/workflows/ci.yml for the real invocation):

    python scripts/qa_report.py \\
        --junit junit \\
        --coverage-json coverage.json \\
        --lint-json ruff.json \\
        --evidence-json evidence.json \\
        --stage lint=success --stage test-3.14=failure \\
        --timing-dir timing \\
        --summary qa-summary.md --pr-comment qa-pr-comment.md
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

MARKER = "<!-- qa-report -->"

# --------------------------------------------------------------------------- #
# Plain-English vocabulary
# --------------------------------------------------------------------------- #
# QA categories: which tests belong to which everyday-language area, matched on
# the test's node id. Specific patterns are matched before broad ones.
CATEGORIES = [
    ("metadata", "🧹", "Hidden data removed",
     "GPS location, camera model, timestamps, embedded thumbnails and comments "
     "are stripped so they are gone for good.",
     ["removes_every_metadata", "removes_metadata", "removes_text", "no_secret",
      "residual", "no_leak", "_a1_", "leak", "strip", "canonicaliz",
      "present_before", "comment_leak"]),
    ("content", "🖼️", "Picture and sound preserved",
     "The file still looks and sounds identical after cleaning — pixel-for-pixel "
     "in the lossless modes.",
     ["preserves_pixel", "content_preserved", "lossless", "preserves_alpha",
      "preserves_palette", "preserves_quantization", "phash", "pixel",
      "preserved", "bit_exact", "entropy_bytes", "dimensions", "perceptual",
      "audio"]),
    ("fingerprint", "🕵️", "Made untraceable",
     "You cannot tell which app, camera or phone produced the file — the "
     "invisible compression fingerprint is erased.",
     ["e3_dqt", "e_engine", "e_lame", "fingerprint_guard", "peerset_a2",
      "png_a2", "fingerprint", "normalizes_dqt", "normalizes_deflate", "dqt",
      "deflate", "a2", "engine", "encoder"]),
    ("irreversibility", "🔒", "Cannot be recovered",
     "Forensic recovery tools cannot bring back anything that was removed.",
     ["forensic_recovery", "recover", "carv"]),
    ("format", "🧩", "File stays valid",
     "The internal structure and checksums stay correct, so the file still "
     "opens everywhere it did before.",
     ["segment", "chunk", "crc", "walk", "assemble", "standard", "toy_format",
      "diff", "variance", "floor", "exit_gate"]),
    ("tooling", "⚙️", "The tool behaves",
     "The command line, automatic file-type detection, fail-safe behaviour and "
     "the reporting all work correctly.",
     ["cli", "dispatch", "matrix", "exit", "integration_exiftool", "determinist",
      "subprocess_scrubber", "qa_report", "report"]),
    ("other", "✅", "Other checks", "Additional internal quality checks.", []),
]

_MATCH_ORDER = ["irreversibility", "fingerprint", "content", "tooling",
                "format", "metadata", "other"]

# Jargon -> everyday words, used to turn a test's function name into a sentence
# a non-technical reader can follow.
GLOSSARY = {
    "f1": "F1 (bit-preserving mode)", "f2": "F2 (lossless re-encode)",
    "f3": "F3 (lossy re-encode)", "a1": "the metadata snoop",
    "a2": "the fingerprint snoop", "a3": "the original-copy snoop",
    "dqt": "JPEG compression table", "exif": "EXIF camera data",
    "xmp": "XMP metadata", "iptc": "IPTC caption data",
    "icc": "ICC colour profile", "id3": "ID3 audio tags",
    "apev2": "APE audio tags", "ifd": "TIFF directory", "crc": "checksum",
    "prnu": "sensor noise", "phash": "perceptual image fingerprint",
    "cbr": "constant bitrate", "vbr": "variable bitrate",
    "xing": "MP3 header", "lame": "the LAME encoder",
    "mpo": "multi-picture JPEG", "jpeg": "JPEG", "png": "PNG", "mp3": "MP3",
    "cli": "command line", "gps": "GPS location", "utf": "text encoding",
    "e3": "experiment E3", "toyf": "the test format",
}

# The roadmap, in build order. A format with a Pareto matrix on disk is reported
# as supported; the rest are reported as planned. Landing a format's matrix is
# all it takes for this report to start claiming it — there is no second list to
# remember to update.
ROADMAP = [
    ("jpeg", "JPEG photos"), ("png", "PNG graphics and screenshots"),
    ("mp3", "MP3 audio"), ("flac", "FLAC lossless audio"),
    ("m4a", "M4A / AAC audio"),
    ("pdf", "PDF documents"), ("ooxml", "Word documents"),
    ("mp4", "MP4 video"), ("heic", "HEIC iPhone photos"),
    ("raw", "camera RAW files"),
]

FORMAT_LABEL = {"jpeg": "JPEG (photos)", "png": "PNG (graphics / screenshots)",
                "mp3": "MP3 (audio)", "flac": "FLAC (lossless audio)",
                "m4a": "M4A (Apple / AAC audio)",
                "pdf": "PDF (documents)", "ooxml": "Word (.docx)",
                "mp4": "MP4 (video)", "heic": "HEIC (iPhone photos)",
                "raw": "Camera RAW"}

# Every stage of the pipeline, with what it checks said without jargon.
STAGES = [
    ("lint", "🔍", "Code quality",
     "Checks the code itself is tidy and consistent, and that the automation "
     "script has no mistakes."),
    ("test", "🧪", "Test suite",
     "Runs every automated check against real files, on four versions of Python."),
    ("coverage", "📊", "Coverage",
     "Measures how much of the tool's code the tests actually exercise."),
    ("evidence", "🔬", "Published results still true",
     "Re-measures the tool's headline claims and confirms the published results "
     "table still matches reality."),
]


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #
@dataclass
class Case:
    """One test result, from one Python version."""

    nodeid: str
    name: str
    status: str          # pass | fail | skip
    time: float
    leg: str             # "3.14"
    file: str = ""
    line: str = ""
    message: str = ""


@dataclass
class Run:
    """Everything one CI run produced, in one place."""

    cases: list[Case] = field(default_factory=list)
    legs: list[str] = field(default_factory=list)
    coverage: dict | None = None
    coverage_gate: dict | None = None
    lint: list | None = None
    evidence: dict | None = None
    stages: dict = field(default_factory=dict)
    timings: dict = field(default_factory=dict)
    flow: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def passed(self) -> int:
        return sum(1 for c in self.cases if c.status == "pass")

    @property
    def failed(self) -> int:
        return sum(1 for c in self.cases if c.status == "fail")

    @property
    def skipped(self) -> int:
        return sum(1 for c in self.cases if c.status == "skip")

    @property
    def total(self) -> int:
        return len(self.cases)

    @property
    def duration(self) -> float:
        return sum(c.time for c in self.cases)

    @property
    def unique_failures(self) -> list[Case]:
        """Failing tests, one entry per test rather than per Python version."""
        seen, out = set(), []
        for c in self.cases:
            if c.status == "fail" and c.nodeid not in seen:
                seen.add(c.nodeid)
                out.append(c)
        return out

    @property
    def missing_legs(self) -> list[str]:
        """Expected Python versions that reported nothing at all."""
        seen = {c.leg for c in self.cases}
        return [leg for leg in self.legs if leg not in seen]

    def ok(self) -> bool:
        bad = {"failure", "cancelled", "timed_out"}
        return (self.failed == 0 and not self.missing_legs
                and not (set(self.stages.values()) & bad))


_LEG_RE = re.compile(r"(3\.\d{1,2})")


def _leg_from_path(path: str) -> str:
    """Recover the Python version from an artifact path like junit-3.12/x.xml."""
    m = _LEG_RE.search(path)
    return m.group(1) if m else "local"


def parse_junit(paths: list[str]) -> tuple[list[Case], list[str]]:
    """Read every JUnit XML given. Accepts files, directories, or globs."""
    files: list[str] = []
    for p in paths:
        if os.path.isdir(p):
            files.extend(sorted(glob.glob(os.path.join(p, "**", "*.xml"),
                                          recursive=True)))
        else:
            hits = sorted(glob.glob(p))
            files.extend(hits if hits else ([p] if os.path.exists(p) else []))

    cases: list[Case] = []
    legs: list[str] = []
    for f in files:
        leg = _leg_from_path(f)
        if leg not in legs:
            legs.append(leg)
        try:
            tree = ET.parse(f)
        except (ET.ParseError, OSError):
            continue
        for tc in tree.iter("testcase"):
            status, message = "pass", ""
            for child in tc:
                if child.tag in ("failure", "error"):
                    status = "fail"
                    message = (child.get("message") or "") or (child.text or "")
                elif child.tag == "skipped":
                    status = "skip"
                    message = child.get("message") or ""
            cases.append(Case(
                nodeid=f"{tc.get('classname', '')}.{tc.get('name', '')}",
                name=tc.get("name", ""),
                status=status,
                time=float(tc.get("time") or 0.0),
                leg=leg,
                file=tc.get("file", ""),
                line=tc.get("line", ""),
                message=message.strip(),
            ))
    return cases, sorted(legs)


def load_json(path: str | None):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def load_timings(directory: str | None) -> dict:
    """Per-job wall-clock, written by each job as {"job": ..., "seconds": ...}."""
    out: dict[str, float] = {}
    if not directory or not os.path.isdir(directory):
        return out
    for p in sorted(glob.glob(os.path.join(directory, "**", "*.json"),
                             recursive=True)):
        d = load_json(p)
        if isinstance(d, dict) and "job" in d and "seconds" in d:
            try:
                out[str(d["job"])] = float(d["seconds"])
            except (TypeError, ValueError):
                continue
    return out


def _extract_block(text: str, name: str) -> str:
    """Pull the text between <!-- NAME:BEGIN --> and <!-- NAME:END -->."""
    begin, end_marker = f"<!-- {name}:BEGIN -->", f"<!-- {name}:END -->"
    start, end = text.find(begin), text.find(end_marker)
    if start == -1 or end == -1 or end < start:
        return ""
    return text[start + len(begin):end].strip()


def load_limits() -> tuple[str, str]:
    """The honest-limits table + residual list, read from docs/limits.md.

    Kept OUT of this script deliberately. When the limits lived here as prose
    they went stale the moment a phase shipped — the table still said "JPEG and
    PNG only" after MP3 had landed — and a stale limits section is worse than
    none, because readers take it as complete. One file, read by both humans and
    this report, cannot drift from itself.
    """
    path = os.path.join(REPO, "docs", "limits.md")
    if not os.path.exists(path):
        return "", ""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    return _extract_block(text, "LIMITS"), _extract_block(text, "RESIDUALS")


def load_capabilities() -> list[dict]:
    """Per-format capability rows, derived from the Pareto matrices on disk."""
    rows = []
    for fmt, _ in ROADMAP:
        doc = load_json(os.path.join(REPO, "tests", "harness", "results",
                                     f"{fmt}_irreversible_scrubber.json"))
        if not doc:
            continue
        cells = {(c["adversary"], c["fidelity"]): c["verdict"]
                 for c in doc.get("cells", [])}
        a1 = {f: cells.get(("A1", f)) for f in ("F1", "F2", "F3")}
        a2 = {f: cells.get(("A2", f)) for f in ("F1", "F2", "F3")}

        removes = "✅ Yes" if any(v == "pass" for v in a1.values()) else "❌ Not yet"

        # Which modes strip the metadata *without touching the content at all*.
        # Never a blanket "yes": F3 re-encodes, so claiming both an identical
        # file and untraceability in the same row would promise something this
        # tool cannot do in one pass.
        lossless = [f for f in ("F1", "F2") if a1[f] == "pass"]
        keeps = f"✅ Yes — in {' and '.join(lossless)}" if lossless else "❌ Not yet"

        untraceable = "❌ Not yet"
        for f in ("F1", "F2", "F3"):
            if a2[f] == "pass":
                cost = ("no quality loss" if f in ("F1", "F2")
                        else "a tiny, invisible quality cost")
                untraceable = f"✅ Yes — in {f}, {cost}"
                break
        rows.append({"fmt": fmt, "label": FORMAT_LABEL.get(fmt, fmt.upper()),
                     "removes": removes, "keeps": keeps,
                     "untraceable": untraceable, "a1": a1, "a2": a2})
    return rows


# --------------------------------------------------------------------------- #
# Presentation helpers
# --------------------------------------------------------------------------- #
def humanize(name: str) -> str:
    """`test_f3_normalizes_encoder_fingerprint` -> a readable sentence."""
    n = re.sub(r"^test_", "", name)
    n = re.sub(r"\[.*\]$", "", n)             # drop parametrise ids
    words = [GLOSSARY.get(w, w) for w in n.split("_") if w]
    if not words:
        return name
    s = " ".join(words)
    return s[0].upper() + s[1:]


def bar(pct: float, width: int = 30) -> str:
    filled = max(0, min(width, round(pct / 100 * width)))
    return "█" * filled + "░" * (width - filled)


def pct_colour(pct: float) -> str:
    if pct >= 90:
        return "brightgreen"
    if pct >= 80:
        return "green"
    if pct >= 60:
        return "yellow"
    return "red"


def badge(label: str, message: str, colour: str, style: str = "flat-square",
          logo: str = "") -> str:
    def esc(s):
        # shields.io: `%` must be percent-encoded first, then `-`/`_`/space are
        # escaped by doubling. Order matters — encoding `%` last would mangle
        # the `%25` this step just produced.
        return (str(s).replace("%", "%25").replace("-", "--")
                .replace("_", "__").replace(" ", "_"))
    url = (f"https://img.shields.io/badge/{esc(label)}-{esc(message)}-"
           f"{colour}?style={style}")
    if logo:
        url += f"&logo={logo}&logoColor=white"
    return f"![{label}: {message}]({url})"


def fmt_secs(s: float) -> str:
    if s <= 0:
        return "—"
    if s < 60:
        return f"{s:.0f}s"
    return f"{int(s // 60)}m {int(s % 60):02d}s"


def icon(outcome: str) -> str:
    return {"success": "✅", "failure": "❌", "skipped": "⏭️",
            "cancelled": "🚫", "": "➖"}.get(outcome, "➖")


def details(summary: str, body: str, open_: bool = False) -> str:
    tag = "<details open>" if open_ else "<details>"
    return f"{tag}\n<summary>{summary}</summary>\n\n{body}\n\n</details>"


# --------------------------------------------------------------------------- #
# Sections
# --------------------------------------------------------------------------- #
def section_header(run: Run) -> str:
    ok = run.ok()
    cov = run.coverage or {}
    pct = float(cov.get("totals", {}).get("percent_covered", 0.0))

    badges = [
        badge("QA", "passing" if ok else "failing",
              "brightgreen" if ok else "e03131", "for-the-badge", logo="github"),
        badge("checks passed", str(run.passed), "2f9e44", "for-the-badge"),
        badge("failed", str(run.failed),
              "e03131" if run.failed else "lightgrey", "for-the-badge"),
    ]
    if cov:
        badges.append(badge("coverage", f"{pct:.0f}%", pct_colour(pct),
                            "for-the-badge"))

    small = []
    if run.skipped:
        small.append(badge("skipped", str(run.skipped), "868e96"))
    if run.legs:
        small.append(badge("python", " | ".join(run.legs), "3776ab", logo="python"))
    caps = load_capabilities()
    if caps:
        small.append(badge("formats", " ".join(c["fmt"].upper() for c in caps),
                           "1971c2"))
    small.append(badge("threat model", "medium-tier (A2)", "5f3dc4"))

    if ok:
        banner = (f"### ✅ Everything passed — all {run.passed} checks are green\n\n"
                  "Nothing leaked, no file was damaged, and a scrubbed file still "
                  "cannot be traced back to the device that made it.")
    else:
        bits = []
        if run.failed:
            bits.append(f"**{run.failed} of {run.total} checks failed**")
        if run.missing_legs:
            bits.append("Python **" + "**, **".join(run.missing_legs) + "** "
                        "reported no results at all")
        broken = sorted(k for k, v in run.stages.items() if v == "failure")
        if broken:
            bits.append("the **" + "**, **".join(broken) + "** stage reported a "
                        "problem")
        if len(bits) > 1:
            summary = ", ".join(bits[:-1]) + ", and " + bits[-1]
        else:
            summary = bits[0] if bits else "The run did not complete cleanly"
        banner = ("### ❌ Something needs attention\n\n" + summary
                  + ". Start with *Where it ran, and where it broke* just below.")

    return "\n".join([
        "# 🛡️ Irreversible Metadata Scrubber — Quality Report",
        "",
        " ".join(badges),
        "",
        " ".join(small),
        "",
        banner,
        "",
        "> **What this tool does, in one sentence.** It permanently removes the "
        "hidden information a file carries about you — where a photo was taken, "
        "which phone took it, when, and what was edited — without changing what "
        "the file looks or sounds like.",
    ])


def section_flow_diagram(run: Run) -> str:
    """Pipeline diagram, with each box coloured by what actually happened."""
    nodes: list[tuple[str, str, str]] = []   # (id, label, outcome)
    edges: list[tuple[str, str]] = []

    nodes.append(("START", '(["📥 Code change"])', "success"))
    nodes.append(("LINT",
                  f'["🔍 Code quality<br/>{icon(run.stages.get("lint", ""))}"]',
                  run.stages.get("lint", "")))
    edges.append(("START", "LINT"))

    for leg in run.legs:
        nid = "PY" + leg.replace(".", "")
        outcome = run.stages.get(f"test-{leg}", "")
        if not outcome:
            fails = sum(1 for c in run.cases if c.leg == leg and c.status == "fail")
            outcome = "failure" if fails else "success"
        n_leg = sum(1 for c in run.cases if c.leg == leg)
        if n_leg == 0:
            # An expected version that produced no results at all — the job died
            # before pytest ran. Silence here would read as success.
            outcome, label = "failure", f'["🐍 Python {leg}<br/>❌ no results"]'
        else:
            label = f'["🐍 Python {leg}<br/>{icon(outcome)} {n_leg} checks"]'
        nodes.append((nid, label, outcome))
        edges.append(("START", nid))
        edges.append((nid, "COV"))

    nodes.append(("EVID",
                  f'["🔬 Results still true<br/>{icon(run.stages.get("evidence", ""))}"]',
                  run.stages.get("evidence", "")))
    edges.append(("START", "EVID"))

    cov = run.coverage or {}
    cov_pct = float(cov.get("totals", {}).get("percent_covered", 0.0))
    nodes.append(("COV",
                  f'["📊 Coverage<br/>{icon(run.stages.get("coverage", ""))} '
                  f'{cov_pct:.0f}%"]',
                  run.stages.get("coverage", "")))

    nodes.append(("REPORT", '["📋 This report"]', "success"))
    for src in ("LINT", "COV", "EVID"):
        edges.append((src, "REPORT"))

    verdict = "success" if run.ok() else "failure"
    nodes.append(("END", f'(["{icon(verdict)} Verdict"])', verdict))
    edges.append(("REPORT", "END"))

    lines = ["```mermaid", "flowchart LR"]
    lines += [f"    {nid}{label}" for nid, label, _ in nodes]
    lines += [f"    {a} --> {b}" for a, b in edges]
    by_class: dict[str, list[str]] = {}
    for nid, _, outcome in nodes:
        by_class.setdefault({"success": "ok", "failure": "bad"}.get(outcome, "idle"),
                            []).append(nid)
    lines += [f"    class {','.join(ids)} {cls}" for cls, ids in by_class.items()]
    lines += [
        "    classDef ok fill:#d3f9d8,stroke:#2f9e44,stroke-width:1px,color:#000",
        "    classDef bad fill:#ffc9c9,stroke:#e03131,stroke-width:3px,color:#000",
        "    classDef idle fill:#e9ecef,stroke:#adb5bd,color:#495057",
        "```",
    ]

    out = ["## 🗺️ Where it ran, and where it broke", "", "\n".join(lines), ""]
    broken = sorted(k for k, v in run.stages.items() if v == "failure")
    if broken or run.failed:
        note = "> ❌ **The red boxes are where the problem is.** "
        if broken:
            note += f"Failing stage(s): **{', '.join(broken)}**. "
        if run.failed:
            note += (f"{run.failed} individual check(s) failed — each one is "
                     "listed under *What failed*, and flagged inline on the "
                     "offending line of code.")
        out.append(note)
    else:
        out.append("> Every box is green: the code was checked, the tests ran on "
                   "every supported version of Python, coverage held, and the "
                   "published results were re-confirmed from scratch.")
    return "\n".join(out)


def section_stage_table(run: Run) -> str:
    rows = ["| | Stage | Result | Took | What this checks |",
            "|:--:|---|:--:|--:|---|"]
    for key, ic, title, desc in STAGES:
        if key == "test":
            # Wall-clock, not summed compute: the versions run in parallel, so
            # the slowest leg is how long this stage actually took.
            leg_secs = [v for k, v in run.timings.items() if k.startswith("test-")]
            secs = max(leg_secs) if leg_secs else run.duration
            if run.failed:
                result = f"❌ {run.failed} failed"
            elif run.missing_legs:
                # No failures only because nothing ran. "0 failed" here would be
                # the most dangerous sentence in the whole report.
                result = f"❌ no results from Python {', '.join(run.missing_legs)}"
            elif run.stages.get("test") in ("failure", "cancelled", "timed_out"):
                result = f"❌ {run.stages['test']}"
            else:
                result = f"✅ {run.passed} passed"
                if run.skipped:
                    result += f" · ⏭️ {run.skipped} skipped"
        else:
            outcome = run.stages.get(key, "")
            secs = run.timings.get(key, 0.0)
            result = f"{icon(outcome)} {outcome or 'not run'}"
            if key == "coverage" and run.coverage:
                pct = run.coverage["totals"]["percent_covered"]
                result = f"{icon(outcome)} {pct:.1f}%"
            elif key == "lint" and run.lint is not None:
                n = len(run.lint)
                result = f"{icon(outcome)} " + (
                    "clean" if n == 0 else f"{n} issue" + ("s" if n > 1 else ""))
        rows.append(f"| {ic} | **{title}** | {result} | {fmt_secs(secs)} | {desc} |")
    return "## 📋 The five-second version\n\n" + "\n".join(rows)


def section_coverage(run: Run) -> str:
    cov = run.coverage
    if not cov:
        return ""
    totals = cov.get("totals", {})
    pct = float(totals.get("percent_covered", 0.0))
    head = [
        "## 📊 How much of the tool is tested",
        "",
        "*Coverage is the share of the tool's own code that the tests actually "
        "run. High coverage means few untested corners where a bug could hide.*",
        "",
        f"`{bar(pct)}` **{pct:.1f}%**",
        "",
        f"> {int(totals.get('covered_lines', 0)):,} of "
        f"{int(totals.get('num_statements', 0)):,} lines of the scrubber were "
        "exercised by the tests in this run"
        + (f", across Python {', '.join(run.legs)}." if len(run.legs) > 1 else "."),
    ]

    gate = run.coverage_gate
    if gate:
        head += [
            "",
            f"Of the code the tests actually reach, **{gate['gated_percent']:.1f}%** "
            f"is covered — that is the number the build is held to "
            f"(floor {gate['floor']:.0f}%).",
        ]
        untouched = gate.get("untouched_files") or []
        if untouched:
            rows = ["| Not reached by any test | Lines |", "|---|--:|"]
            rows += [f"| `{u['path']}` | {u['statements']} |" for u in untouched]
            head += [
                "",
                f"> ⚠️ **{len(untouched)} file(s) — {gate['untouched_statements']} "
                "lines — have no test at all yet.** These are new parts still "
                "being built. They are deliberately left out of the pass mark "
                "so that starting a new feature does not fail the build, but "
                "they are listed here rather than quietly ignored.",
                "",
                details("<b>Which parts are not tested yet</b>", "\n".join(rows)),
            ]

    files = cov.get("files", {})
    if files:
        rows = ["| Part of the tool | Covered | |", "|---|--:|---|"]
        for path in sorted(files):
            p = float(files[path].get("summary", {}).get("percent_covered", 0.0))
            rows.append(f"| `{path}` | {p:.0f}% | `{bar(p, 18)}` |")
        head += ["", details("<b>Coverage file by file</b>", "\n".join(rows))]
    return "\n".join(head)


def section_areas(run: Run) -> str:
    """Results grouped into everyday-language areas, plus a per-version grid."""
    pats = {k: p for k, _, _, _, p in CATEGORIES}
    order = [(k, pats[k]) for k in _MATCH_ORDER]
    buckets: dict[str, dict] = {
        k: {"pass": 0, "fail": 0, "skip": 0, "time": 0.0, "legs": {}}
        for k, *_ in CATEGORIES}

    for c in run.cases:
        nid = c.nodeid.lower()
        placed = "other"
        for key, ps in order:
            if any(p in nid for p in ps):
                placed = key
                break
        b = buckets[placed]
        b[c.status] += 1
        b["time"] += c.time
        b["legs"].setdefault(c.leg, {"pass": 0, "fail": 0, "skip": 0})[c.status] += 1

    rows = ["| Area | Result | Checks | Took | What it means |",
            "|---|:--:|:--:|--:|---|"]
    grid = ["| Area | " + " | ".join(f"Py {leg}" for leg in run.legs) + " |",
            "|---|" + "|".join([":--:"] * len(run.legs)) + "|"]

    for key, ic, title, desc, _ in CATEGORIES:
        b = buckets[key]
        total = b["pass"] + b["fail"] + b["skip"]
        if total == 0:
            continue
        status = "✅ Pass" if b["fail"] == 0 else f"❌ {b['fail']} failed"
        counts = f"{b['pass']}/{total}"
        if b["skip"]:
            counts += f" (+{b['skip']} skipped)"
        rows.append(f"| {ic} **{title}** | {status} | {counts} | "
                    f"{fmt_secs(b['time'])} | {desc} |")
        cells = []
        for leg in run.legs:
            lb = b["legs"].get(leg)
            if not lb:
                cells.append("➖")
            elif lb["fail"]:
                cells.append(f"❌ {lb['fail']}")
            elif lb["pass"]:
                cells.append(f"✅ {lb['pass']}")
            else:
                cells.append(f"⏭️ {lb['skip']}")
        grid.append(f"| {ic} {title} | " + " | ".join(cells) + " |")

    out = ["## 🧭 What was tested, area by area", "", "\n".join(rows)]
    if run.skipped:
        out += ["", "> ⏭️ *Skipped* checks are ones that need a helper program "
                "(such as a specific audio or image encoder) that was not "
                "available on the machine running this build. They are not "
                "failures — but they are not proof either."]
    if len(run.legs) > 1:
        out += ["", details(
            "<b>The same results, broken down by Python version</b>",
            "\n".join(grid) + "\n\n*Every check runs on every version of Python "
            "the tool supports, so a future upgrade cannot silently break it.*")]
    return "\n".join(out)


def section_failures(run: Run) -> str:
    fails = run.unique_failures
    if not fails:
        return ""
    body = []
    for c in fails:
        legs = sorted({x.leg for x in run.cases
                       if x.nodeid == c.nodeid and x.status == "fail"})
        where = f"`{c.file}:{c.line}`" if c.file else f"`{c.nodeid}`"
        # Deliberately NOT html.escape: this goes inside a ``` fence, where
        # GitHub renders the bytes literally, so escaping would show readers
        # `&#x27;` where the error said `'`. Only the fence itself needs
        # neutralising so a traceback cannot break out of the block.
        msg = (c.message or "").strip().replace("```", "` ` `")
        if len(msg) > 1200:
            msg = msg[:1200] + "\n… (truncated — the full log is in the job output)"
        body += [
            f"#### ❌ {humanize(c.name)}",
            "",
            f"- **Where:** {where}",
            f"- **Affects Python:** {', '.join(legs)}",
            f"- **Internal name:** `{c.name}`",
            "",
            "```text",
            msg or "(no message captured)",
            "```",
            "",
        ]
    intro = (f"## ❌ What failed\n\n*{len(fails)} check(s) did not pass. Each one "
             "is also flagged inline on the affected line of code in the pull "
             "request, so it is easy to find.*\n\n")
    return intro + details(f"<b>Show all {len(fails)} failure(s)</b>",
                           "\n".join(body), open_=True)


def section_lint(run: Run) -> str:
    if run.lint is None:
        return ""
    if not run.lint:
        return ("## 🔍 Code quality\n\n✅ No issues found — the code matches the "
                "project's agreed style, and the automation itself is valid.")
    by_rule: dict[str, int] = {}
    for item in run.lint:
        code = item.get("code") or "?" if isinstance(item, dict) else "?"
        by_rule[code] = by_rule.get(code, 0) + 1
    rows = ["| Rule | Times |", "|---|--:|"]
    rows += [f"| `{code}` | {n} |"
             for code, n in sorted(by_rule.items(), key=lambda kv: -kv[1])]
    return (f"## 🔍 Code quality\n\n⚠️ {len(run.lint)} issue(s) found.\n\n"
            + details("<b>Breakdown by rule</b>", "\n".join(rows)))


def section_capabilities(run: Run) -> str:
    caps = load_capabilities()
    if not caps:
        return ""
    rows = ["| Format | Removes hidden data | Keeps the file identical | "
            "Makes it untraceable |", "|---|:--:|:--:|:--:|"]
    rows += [f"| **{c['label']}** | {c['removes']} | {c['keeps']} | "
             f"{c['untraceable']} |" for c in caps]

    grid = ["| Format | Snoop | Bit-preserving (F1) | Lossless (F2) | Lossy (F3) |",
            "|---|---|:--:|:--:|:--:|"]
    mark = {"pass": "✅ beaten", "fail": "❌ still traceable",
            "not_applicable": "— n/a", "not_tested": "· not measured"}
    for c in caps:
        for adv, human in (("a1", "Metadata snoop"), ("a2", "Fingerprint snoop")):
            cells = [mark.get(c[adv][f], "· —") for f in ("F1", "F2", "F3")]
            grid.append(f"| {c['label']} | {human} | " + " | ".join(cells) + " |")

    return "\n".join([
        "## 🎯 What the tool can promise today",
        "",
        "*Read straight from the measured results files in this repository. If a "
        "claim here is not backed by a real measurement, it does not appear.*",
        "",
        "\n".join(rows),
        "",
        "> **The last two columns can need different modes.** Where making a "
        "file untraceable costs quality, that is a *different* setting from the "
        "one that leaves the file identical — you pick per file, and the table "
        "names which setting delivers which promise.",
        "",
        details("<b>The full measured grid, and what the words mean</b>", "\n".join([
            "\n".join(grid),
            "",
            "**The two snoops**",
            "",
            "- **The metadata snoop** reads the hidden tags directly — GPS "
            "coordinates, camera model, timestamps, the little preview "
            "thumbnail, comments.",
            "- **The fingerprint snoop** never reads a tag. It works out which "
            "app or device made the file from *how* the file was compressed — "
            "like recognising a typewriter from the dents it leaves in paper.",
            "",
            "**The three cleaning strengths**",
            "",
            "- **F1 — bit-preserving:** delete every tag; the picture or audio "
            "data is left byte-for-byte untouched. No quality cost.",
            "- **F2 — lossless re-encode:** delete every tag *and* repack the "
            "compression. Still perfect quality.",
            "- **F3 — lossy re-encode:** re-compress through one standard "
            "encoder so every file comes out looking the same to a forensic "
            "tool. Tiny, invisible quality cost.",
        ])),
    ])


def section_limits(limits: str, residuals: str, run: Run | None = None) -> str:
    """The honest-limits section, rendered from docs/limits.md.

    This function never writes limits of its own. If the source document is
    missing it says so loudly, because a limits-free report reads as a clean
    bill of health — the single most misleading thing this report could do.
    """
    head = "## 🚧 What the tool cannot do yet — and what we do about it"
    if not limits and not residuals:
        return (f"{head}\n\n"
                "> ⚠️ **The honest-limits document is missing.** `docs/limits.md` "
                "could not be read, so this report cannot show what the tool "
                "still cannot do. **Do not read this as a clean bill of "
                "health** — treat the limits as unknown until the document is "
                "restored.")

    out = [head, "",
           "*The honest part. Each row is a limit in plain words, then what we "
           "do about it. Read straight from `docs/limits.md`, the single place "
           "these are recorded, so it cannot quietly fall out of date.*", ""]
    if limits:
        out.append(limits)
    else:
        out.append("> ⚠️ The limits table is missing from `docs/limits.md`. "
                   "**Do not read this as a clean bill of health.**")

    if run is not None and run.evidence and not run.evidence.get("ok", True):
        out += ["", "> ⚠️ **Heads up:** this run re-measured the headline results "
                "and they no longer match the published table — see *Published "
                "results* above."]

    if residuals:
        out += ["", details("🧪 <b>Where the evidence lives — the technical "
                            "residuals, for the curious</b>", residuals)]
    else:
        out += ["", "> ⚠️ The residuals list is missing from `docs/limits.md`. "
                "**Do not read this as a clean bill of health.**"]

    out += ["", "> **The principle:** we never claim a file is untraceable "
            "unless we have actually proven it. Where a limit is a law of "
            "physics, we say so rather than pretend it is solved."]
    return "\n".join(out)


def section_evidence(run: Run) -> str:
    ev = run.evidence
    if not ev:
        return ""
    if ev.get("ok"):
        return ("## 🔬 Published results re-confirmed\n\n"
                "✅ This run re-measured the tool's headline results from scratch "
                "and they still match the published table exactly. The claims in "
                "this report are not cached — they were just proven again.")
    rows = ["| Format | Situation | Published | Measured now |",
            "|---|---|:--:|:--:|"]
    rows += [f"| {d.get('format', '?').upper()} | {d.get('adversary', '?')} at "
             f"{d.get('fidelity', '?')} | {d.get('published', '?')} | "
             f"**{d.get('measured', '?')}** |"
             for d in ev.get("differences", [])]
    return ("## 🔬 Published results no longer match\n\n"
            "❌ This run re-measured the tool's headline results and they differ "
            "from the published table. Either the code changed behaviour, or the "
            "published table needs regenerating — **it must not be left claiming "
            "something that is no longer true**.\n\n" + "\n".join(rows))


def section_slowest(run: Run) -> str:
    # One row per check, not one per Python version — the same test appearing
    # four times is noise, and it crowds out the actually-slow ones.
    slowest: dict[str, Case] = {}
    for c in run.cases:
        if c.nodeid not in slowest or c.time > slowest[c.nodeid].time:
            slowest[c.nodeid] = c
    ranked = sorted(slowest.values(), key=lambda c: -c.time)[:10]
    if not ranked or ranked[0].time <= 0:
        return ""
    rows = ["| Check | Took |", "|---|--:|"]
    rows += [f"| {humanize(c.name)} | {c.time:.2f}s |" for c in ranked]
    body = ("\n".join(rows) + "\n\nTotal time spent running checks, added up "
            f"across every Python version: **{fmt_secs(run.duration)}**.")
    return "## ⏱️ Timing\n\n" + details("<b>The ten slowest checks</b>", body)


def section_flow(run: Run) -> str:
    if not run.flow:
        return ""
    return ("## 🔍 Before and after, on real files\n\n"
            + details("<b>Show what actually came off a real file — tags, "
                      "thumbnails, encoder fingerprint, before vs after</b>",
                      run.flow, open_=True))


def section_footer(run: Run) -> str:
    m = run.meta
    parts = [f"Commit <code>{(m.get('commit') or 'local')[:7]}</code>",
             f"branch <code>{m.get('branch') or 'local'}</code>"]
    if m.get("run"):
        parts.append(f"run #{m['run']}")
    parts += [f"{run.total} checks on Python {', '.join(run.legs) or 'local'}",
              "ground truth verified with ExifTool",
              "adversary model: medium-tier (A2)",
              "regenerated automatically on every change"]
    return "<sub>" + " · ".join(parts) + "</sub>"


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def render_full(run: Run) -> str:
    limits, residuals = load_limits()
    sections = [
        section_header(run),
        section_flow_diagram(run),
        section_stage_table(run),
        section_failures(run),
        section_coverage(run),
        section_areas(run),
        section_evidence(run),
        section_capabilities(run),
        section_limits(limits, residuals, run),
        section_lint(run),
        section_slowest(run),
        section_flow(run),
        section_footer(run),
    ]
    return MARKER + "\n" + "\n\n---\n\n".join(s for s in sections if s.strip()) + "\n"


def render_markdown(cats, flow_md, caps, meta, limits, residuals) -> str:
    """Render a report from pre-summarised parts, without a full CI run.

    Used by the limits tests and by anyone driving the renderer directly (the
    matrices and a category summary are enough to produce a readable page). It
    shares `section_limits` with the full report, so there is exactly one piece
    of code that renders the honest limits — the whole point of keeping them in
    `docs/limits.md`.
    """
    rows = ["| Area | Result | Checks | What it means |", "|---|:--:|:--:|---|"]
    for c in cats or []:
        status = "✅ Pass" if c["failed"] == 0 else f"❌ {c['failed']} failed"
        rows.append(f"| {c['icon']} **{c['title']}** | {status} | "
                    f"{c['passed']}/{c['total']} | {c['desc']} |")

    cap_rows = ["| Format | Removes hidden data (A1) | Keeps content identical | "
                "Makes it untraceable (A2) |", "|---|:--:|:--:|:--:|"]
    for row in caps or []:
        label, removes, keeps, untr = row
        cap_rows.append(f"| **{label}** | {removes} | {keeps} | {untr} |")

    total_pass = sum(c["passed"] for c in cats or [])
    total_fail = sum(c["failed"] for c in cats or [])
    ok = total_fail == 0
    sections = [
        "# 🛡️ Irreversible Metadata Scrubber — Quality Report",
        "",
        badge("QA", "passing" if ok else "failing",
              "brightgreen" if ok else "e03131", "for-the-badge", logo="github"),
        "",
        (f"**All {total_pass} quality checks passed.**" if ok
         else f"**{total_fail} of {total_pass + total_fail} checks failed.**"),
        "",
        "## 🧭 What was tested, area by area",
        "",
        "\n".join(rows),
    ]
    if len(cap_rows) > 2:
        sections += ["", "## 🎯 What the tool can promise today", "",
                     "\n".join(cap_rows)]
    sections += ["", section_limits(limits, residuals)]
    if flow_md:
        sections += ["", "## 🔍 Before and after, on real files", "", flow_md]
    m = meta or {}
    sections += ["", "<sub>Commit <code>"
                 f"{(m.get('commit') or 'local')[:7]}</code> · branch <code>"
                 f"{m.get('branch') or 'local'}</code></sub>"]
    return "\n".join(sections) + "\n"


def render_pr_comment(run: Run) -> str:
    """The same story, short enough to live at the bottom of a pull request."""
    sections = [
        section_header(run),
        section_flow_diagram(run),
        section_stage_table(run),
        section_failures(run),
    ]
    if run.coverage:
        pct = float(run.coverage["totals"]["percent_covered"])
        sections.append(f"**Coverage:** `{bar(pct)}` **{pct:.1f}%**")
    sections.append(
        "<sub>📋 The full report — area-by-area results, what the tool can "
        "promise for each file type, its honest limits, and before/after "
        "evidence on real files — is on the "
        f"[Actions run page]({run.meta.get('run_url', '#')}).</sub>")
    return MARKER + "\n" + "\n\n---\n\n".join(s for s in sections if s.strip()) + "\n"


def build_run(args) -> Run:
    cases, legs = parse_junit(args.junit)
    # A version that was supposed to run but produced no JUnit at all (its job
    # died before pytest started) must still appear, as a failure.
    expected = [v.strip() for v in (args.expect_legs or "").split(",") if v.strip()]
    legs = sorted(set(legs) | set(expected))
    stages = {}
    for item in args.stage or []:
        if "=" in item:
            k, v = item.split("=", 1)
            stages[k.strip()] = v.strip()

    flow_md = ""
    if not args.no_flow:
        try:
            import scripts.scrub_flow_report as flow
            flow_md = flow.build_body(args.flow_out)
        except Exception as exc:  # noqa: BLE001 - report must render regardless
            # A missing evidence section is far better than no report at all.
            flow_md = f"> Could not build the before/after sample: `{exc}`"

    run_id = os.environ.get("GITHUB_RUN_ID", "")
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    return Run(
        cases=cases,
        legs=legs,
        coverage=load_json(args.coverage_json),
        coverage_gate=load_json(args.coverage_gate_json),
        lint=load_json(args.lint_json) if args.lint_json else None,
        evidence=load_json(args.evidence_json),
        stages=stages,
        timings=load_timings(args.timing_dir),
        flow=flow_md,
        meta={
            "commit": os.environ.get("GITHUB_SHA", ""),
            "branch": os.environ.get("GITHUB_REF_NAME", ""),
            "run": os.environ.get("GITHUB_RUN_NUMBER", ""),
            "run_url": (f"{server}/{repo}/actions/runs/{run_id}"
                        if run_id and repo else "#"),
        },
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build the plain-language QA report.")
    ap.add_argument("--junit", nargs="*", default=["pytest-results.xml"],
                    help="JUnit XML files, globs, or directories to search")
    ap.add_argument("--coverage-json", default=None)
    ap.add_argument("--coverage-gate-json", default=None,
                    help="output of scripts/coverage_gate.py")
    ap.add_argument("--lint-json", default=None)
    ap.add_argument("--evidence-json", default=None)
    ap.add_argument("--stage", action="append", default=[],
                    metavar="NAME=OUTCOME", help="repeatable, e.g. lint=success")
    ap.add_argument("--timing-dir", default=None)
    ap.add_argument("--expect-legs", default="",
                    help="comma-separated Python versions that must report, "
                         "e.g. 3.11,3.12 — a silent no-show becomes a failure")
    ap.add_argument("--flow-out", default="scrub-samples")
    ap.add_argument("--no-flow", action="store_true",
                    help="skip before/after sample generation (fast, for tests)")
    ap.add_argument("--summary", default="qa-summary.md")
    ap.add_argument("--pr-comment", default=None)
    args = ap.parse_args(argv)

    run = build_run(args)

    with open(args.summary, "w", encoding="utf-8") as f:
        f.write(render_full(run))
    if args.pr_comment:
        with open(args.pr_comment, "w", encoding="utf-8") as f:
            f.write(render_pr_comment(run))

    print(f"QA report: {run.passed} passed, {run.failed} failed, "
          f"{run.skipped} skipped across Python {', '.join(run.legs) or '-'} "
          f"-> {args.summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
