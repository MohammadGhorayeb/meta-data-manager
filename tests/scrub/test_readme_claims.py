"""The numbers README.md states about this project, checked against the project.

README is the first thing anyone reads and the only place several figures are
published. A figure maintained by hand drifts silently: the test count sat at
"610 tests passing" while the suite collected 605, which is wrong twice over --
inflated, and counting skipped tests as passes. Nothing failed, because nothing
was looking.

The rule these tests encode is the project's own: a published number is a claim,
and a claim needs something that fails when it stops being true.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _readme() -> str:
    with open(os.path.join(REPO, "README.md"), encoding="utf-8") as f:
        return f.read()


def test_the_stated_test_count_is_what_pytest_collects():
    """Collected, deliberately -- not passed.

    How many pass depends on the machine: 20 tests skip here for want of a real
    Word sample or an HEIC corpus, and a different box skips a different set. A
    count of passes would therefore be a claim about one laptop. What is
    reproducible is how many tests exist, so that is what README states.

    Collection itself is machine-dependent too: the real-file tests parametrize
    over the samples a machine has, one test per file. The first CI run after
    Phase 4 found README stating this laptop's 901 against CI's 888. So the count
    is taken with real-sample discovery switched off -- the tests that exist on
    every machine, which is what a published number can promise.
    """
    m = re.search(r"\*\*(\d[\d,]*) tests\*\*", _readme())
    assert m, "README no longer states a test count in the form **N tests**"
    claimed = int(m.group(1).replace(",", ""))

    out = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--collect-only",
         "-p", "no:randomly", "-p", "no:cacheprovider"],
        cwd=REPO, capture_output=True, text=True, timeout=300,
        env=dict(os.environ, SCRUB_IGNORE_REAL_SAMPLES="1")).stdout
    found = re.search(r"(\d+) tests? collected", out)
    assert found, f"could not read a collection count from pytest:\n{out[-2000:]}"
    actual = int(found.group(1))

    assert claimed == actual, (
        f"README says **{claimed} tests**; pytest collects {actual}. Update the "
        "README line -- it is a published figure, not a note to self.")


def test_readme_does_not_claim_more_tests_pass_than_exist():
    """The specific wording that went stale, kept out rather than just fixed.

    "N tests passing" reads as a guarantee about every environment and silently
    counts skips. If someone reintroduces the phrasing, this fails and says why.
    """
    assert not re.search(r"\d[\d,]* tests passing", _readme()), (
        'README states "N tests passing". Say "**N tests**" instead: how many '
        "pass is environment-dependent (samples and corpora are optional), so "
        "the passing count is a claim about one machine, not about the project.")


def test_every_format_readme_claims_has_a_published_matrix():
    """README's badge lists the formats we support. Each must be measured.

    Same defect as the DOCX report gap in a different file: a format named in a
    badge but absent from tests/harness/results/ is a claim with no measurement
    behind it, and the badge is the most-read claim in the repository.
    """
    sys.path.insert(0, os.path.join(REPO, "scripts"))
    import qa_report as qr

    m = re.search(r"badge/formats-([A-Za-z0-9_·\.]+)-", _readme())
    assert m, "README no longer carries a formats badge"
    named = {p.strip().lower().replace(".", "")
             for p in m.group(1).replace("_·_", "|").replace("_", "|").split("|")
             if p.strip()}
    measured = set(qr.published_matrices())
    missing = named - measured
    assert not missing, (
        "README's formats badge names "
        f"{', '.join(sorted(missing))} with no Pareto matrix on disk")
