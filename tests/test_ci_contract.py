"""The contract between the code and the CI workflow that runs it.

This file exists because of a real failure, not a hypothetical one. The whole PDF
phase — walker, serializer, three tiers, two experiments — landed while
`.github/workflows/ci.yml` still installed only the Phase 1 and Phase 2 tools. On the
runner that meant `pdftotext` was absent, so four tests died with `FileNotFoundError`
and, worse, every published PDF verdict was reported by `check_evidence.py` as
"left unchallenged" while the build reported nothing had drifted. A tool the code
depends on going missing is not a loud failure by default; it is a quiet loss of
coverage, and this is what makes it loud.

Two properties are asserted:

1. **Every external binary the code shells out to is accounted for.** Each is either
   installed by the workflow, or listed here as deliberately optional with the reason
   — so adding a new dependency and forgetting the workflow fails a test instead of
   silently reducing what CI measures.
2. **The tools that gate a *published claim* are actually installed.** An optional
   tool degrades a cell to `not_tested`, which `check_evidence.py` correctly refuses
   to treat as a mismatch; that is right behaviour and exactly why it is dangerous.
   A verdict nothing re-measures is a verdict nobody is checking.

These are static checks over the workflow file. They do not need a runner, so they
fail on the contributor's machine, in the pull request, before the tool goes missing.
"""
from __future__ import annotations

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"

# Binary -> the apt package that provides it on the runner.
_PACKAGE_FOR = {
    "exiftool": "libimage-exiftool-perl",
    "jpegtran": "libjpeg-turbo-progs",
    "ffmpeg": "ffmpeg",
    "lame": "lame",
    "pdftotext": "poppler-utils",
    "pdftoppm": "poppler-utils",
}

# Binaries that are genuinely optional, with the reason each is allowed to be absent.
# A tool belongs here only when its absence makes a cell say *not measured* — never
# when its absence would let a cell say *clean* (limit #12).
_OPTIONAL = {
    "shineenc": "built from source in a dedicated workflow step, not an apt package",
    "mat2": "a benchmark competitor, not a dependency of our own scrubber",
    "soffice": "one A2 peer producer; absent means a smaller peer set, never a pass",
    "cupsfilter": "macOS-only A2 peer producer; absent means a smaller peer set",
    "google-chrome": "one A2 peer producer; absent means a smaller peer set",
    "google-chrome-stable": "alias of google-chrome",
    "chromium-browser": "alias of google-chrome",
    "chromium": "alias of google-chrome",
    "python": "the interpreter running this suite",
    "python3": "the interpreter running this suite",
    "fpcalc": "chromaprint audio fingerprint; the harness is documented to degrade "
              "cleanly without it and the audio path skips rather than passing",
}

# Tools whose absence would leave a *published* verdict un-re-measured. These must be
# installed, not merely accounted for.
_REQUIRED_FOR_PUBLISHED_CLAIMS = {
    "exiftool": "the measuring stick every A1 claim is checked against",
    "ffmpeg": "MP3 and M4A F3 re-encode through it; without it those tiers cannot run",
    "lame": "the MP3 encoder behind the published cross-engine A2 evidence",
    "jpegtran": "JPEG F2's lossless re-encode",
    "pdftotext": "how a PDF scrub's content preservation is verified — without it no "
                 "PDF cell can claim anything",
    "pdftoppm": "the whole of PDF F3, and the renderer E-PDF-RASTER attacks",
}


def _source_files() -> list[pathlib.Path]:
    roots = [REPO / "src", REPO / "tests", REPO / "scripts"]
    out: list[pathlib.Path] = []
    for root in roots:
        if root.exists():
            out.extend(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)
    return out


def invoked_binaries() -> set[str]:
    """Every external binary the code names, found by reading the code.

    Discovered rather than listed, for the same reason `check_evidence.py` discovers
    its generators: a hardcoded list is one more place to forget, and forgetting it
    reintroduces exactly the failure this file exists to catch.
    """
    which = re.compile(r'shutil\.which\(\s*["\']([\w.-]+)["\']')
    run = re.compile(r'subprocess\.(?:run|Popen|check_output)\(\s*\[\s*["\']([\w.-]+)["\']')
    found: set[str] = set()
    for path in _source_files():
        if path.name == pathlib.Path(__file__).name:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        found |= set(which.findall(text)) | set(run.findall(text))
    return found


def install_steps() -> list[set[str]]:
    """The package set of every `apt-get install` in the workflow, one set per step.

    Scanned line by line rather than with one regex over the file: a `run:` block is
    shell, its install line continues with a trailing backslash, and it is followed by
    comments that a greedy pattern happily swallows. The first version of this
    function did exactly that and reported `#` and `(E-LAME,` as installed packages.
    """
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    noise = {"sudo", "apt-get", "install", "y", "update", "&&"}
    steps: list[set[str]] = []
    i = 0
    while i < len(lines):
        if "apt-get install" not in lines[i]:
            i += 1
            continue
        collected: list[str] = []
        while i < len(lines):
            line = lines[i].strip()
            collected.append(line.rstrip("\\").strip())
            if not line.endswith("\\"):
                break
            i += 1
        i += 1
        packages = {token for token in re.split(r"\s+", " ".join(collected))
                    if token and not token.startswith("-") and token not in noise}
        steps.append(packages)
    return steps


def installed_packages() -> set[str]:
    """Every package the workflow installs, across all jobs."""
    return set().union(*install_steps()) if install_steps() else set()


def test_the_workflow_file_exists():
    assert WORKFLOW.exists(), f"no CI workflow at {WORKFLOW}"


def test_every_external_binary_is_accounted_for():
    """A new dependency must reach the workflow, or be declared optional here."""
    unknown = sorted(b for b in invoked_binaries()
                     if b not in _PACKAGE_FOR and b not in _OPTIONAL)
    assert not unknown, (
        f"these binaries are invoked by the code but appear in neither the CI package "
        f"map nor the optional list: {unknown}. Add the package to "
        f".github/workflows/ci.yml and to _PACKAGE_FOR, or record why it is optional "
        f"in _OPTIONAL — silently leaving it out costs CI coverage without failing.")


@pytest.mark.parametrize("binary,why", sorted(_REQUIRED_FOR_PUBLISHED_CLAIMS.items()))
def test_tools_behind_published_claims_are_installed_in_ci(binary, why):
    """Without these, a published verdict goes un-re-measured while CI stays green.

    `check_evidence.py` reports an unmeasurable cell as "left unchallenged" and does
    not fail — which is correct, because a missing optional tool is not a regression.
    The consequence is that the only thing standing between a missing tool and an
    unverified claim is this test.
    """
    package = _PACKAGE_FOR[binary]
    assert package in installed_packages(), (
        f"CI does not install {package}, which provides `{binary}` — {why}. Every "
        f"claim depending on it would be re-measured as `not_tested` and pass "
        f"unchallenged.")


def test_every_job_that_runs_measurements_installs_the_same_tools():
    """Three jobs run the suite or the experiments: tests, evidence, and the QA
    report. They must install the same set — a tool present in one and missing in
    another produces two different answers for the same commit, and the difference
    shows up as an unexplained `not_tested` in whichever job is short."""
    sets = install_steps()
    assert len(sets) >= 3, (
        f"expected an install step in each measuring job, found {len(sets)}")
    first = sets[0]
    for i, other in enumerate(sets[1:], start=2):
        assert other == first, (
            f"install step 1 and step {i} differ: only in 1 {sorted(first - other)}, "
            f"only in {i} {sorted(other - first)}")


def test_poppler_specifically_because_this_is_how_it_was_missed():
    """A named regression test for the actual bug.

    The generic checks above would have caught it, but they were written after the
    fact. This one states the specific failure so it reads as what it is: the whole
    PDF phase shipped against a workflow that had never heard of poppler.
    """
    assert "poppler-utils" in installed_packages(), (
        "poppler-utils is missing from the CI workflow. PDF F3 renders with pdftoppm "
        "and every PDF content check reads the document back with pdftotext, so "
        "without it the PDF tests skip and all six PDF verdicts go unchallenged.")
