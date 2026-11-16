"""The plain-language limits page is generated from docs/limits.md, and kept honest.

The page (`docs/what_we_cant_do_yet.html`) used to be written by hand. Two weeks
after it was written it still said a 4 GB video needed 10 GB of memory (streaming
had made it 44 MB per GB) and that Excel had not been compared across programs (it
had, and failed by name). Every claim on it now comes from the SHORT block of
`docs/limits.md`, and these tests fail when:

  * a limit that is not solved has no point on the page (a reader of the page
    alone would never learn it);
  * the page cites a row that does not exist;
  * a "can they tell" verdict disagrees with the published results;
  * the committed page is not what the generator makes from the current file;
  * a row promises future work the README's *After 1.0* list does not carry.
"""
from __future__ import annotations

import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import limits_page as lp  # noqa: E402
import qa_report  # noqa: E402

TEXT = open(lp.LIMITS, encoding="utf-8").read()
ROWS = lp.limit_rows(TEXT)
SHORT = lp.block(TEXT, "SHORT")
PAGE = lp.parse(SHORT)


def test_the_table_and_the_block_parse():
    assert len(ROWS) >= 88 and min(ROWS) == 1
    assert sorted(ROWS) == list(range(1, max(ROWS) + 1)), "a row number is missing"
    assert PAGE["updated"] and PAGE["standfirst"] and PAGE["strengths"]
    assert len(PAGE["sections"]) >= 5 and all(s["items"] or s["rows"]
                                              for s in PAGE["sections"])


def test_every_unsolved_limit_is_on_the_page():
    unsolved = {n for n, status in ROWS.items() if not status.startswith("✅")}
    missing = sorted(unsolved - lp.citations(SHORT))
    assert not missing, (f"limits {missing} are not solved and the short version "
                         "never mentions them: add a point that cites each one")


def test_every_citation_names_a_row():
    assert lp.citations(SHORT) <= set(ROWS), sorted(lp.citations(SHORT) - set(ROWS))


def test_can_they_tell_agrees_with_the_published_results():
    rows = [r for s in PAGE["sections"] for r in s["rows"]]
    ids = [r[0] for r in rows]
    assert len(ids) == len(set(ids))
    assert set(ids) == set(qa_report.published_matrices()), \
        "the table must have one row per published results file, and no other"
    caps = {c["fmt"]: c for c in qa_report.load_capabilities()}
    for fmt, label, verdict, _ in rows:
        a2 = set(caps[fmt]["a2"].values()) - {"not_applicable"}
        if "pass" in a2:
            expected = {"No"}
        elif a2 <= {"not_tested"}:
            expected = {"Not tested"}
        else:
            expected = {"Barely", "Partly", "Yes"}
        assert verdict in expected, (f"{label}: the page says {verdict!r}, the "
                                     f"published results say {sorted(a2)}")


def test_the_committed_page_is_current():
    with open(lp.OUT, encoding="utf-8") as f:
        committed = f.read()
    assert committed == lp.render(TEXT), \
        "docs/what_we_cant_do_yet.html is stale: run scripts/limits_page.py --pdf"


def test_the_short_version_speaks_plainly():
    jargon = re.findall(r"\b(?:[FA][123]|fidelity|adversary|Pareto|matrix|matrices)\b",
                        SHORT.replace("<!--", "").replace("-->", ""), re.I)
    assert not jargon, f"project jargon on a page for non-specialists: {jargon}"


def test_no_row_promises_work_by_status():
    body = lp.block(TEXT, "LIMITS")
    assert not re.findall(r"🔜|\bplanned\b|\broadmap\b", body, re.I), \
        "say 'on the list for after version 1.0' and put it on the README's list"


def test_every_after_1_0_row_is_on_the_readme_list():
    readme = open(os.path.join(REPO, "README.md"), encoding="utf-8").read()
    m = re.search(r"### After 1\.0\n(.*?)\n### ", readme, re.S)
    assert m, "README has no 'After 1.0' list"
    listed = {int(n) for n in re.findall(r"\[#(\d+)\]", m.group(1))}
    promised = {int(n) for n, line in
                ((int(re.match(r"\| \*\*(\d+)", ln).group(1)), ln)
                 for ln in lp.block(TEXT, "LIMITS").splitlines()
                 if re.match(r"\| \*\*\d+\*\* \|", ln))
                if "after version 1.0" in line.lower()}
    assert promised, "no row is on the after-1.0 list: check the phrase"
    assert promised <= listed, (f"limits {sorted(promised - listed)} say they are on "
                                "the list for after version 1.0, but the README's list "
                                "does not name them")
    assert listed <= set(ROWS)
