"""The QA report builder itself.

The report is the thing non-technical readers actually see, so a silent
rendering bug is expensive: it either hides a failure or invents a success.
These tests pin the behaviours that would be embarrassing to get wrong —
a failing run that renders as green, a Python version that vanishes because
its job died, an unescaped badge URL, jargon leaking into the summary.

Companion to test_qa_report_limits.py, which guards the honest-limits section.
"""
from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import qa_report as qr  # noqa: E402

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="3">
  <testcase classname="tests.scrub.test_f1" name="test_f1_removes_gps"
            file="tests/scrub/test_f1.py" line="10" time="0.50"/>
  <testcase classname="tests.scrub.test_mp3" name="test_f3_normalizes_dqt"
            file="tests/scrub/test_mp3.py" line="42" time="1.25">
    <failure message="assert 1 == 2">boom</failure>
  </testcase>
  <testcase classname="tests.scrub.test_f2" name="test_f2_lossless"
            file="tests/scrub/test_f2.py" line="7" time="0.10">
    <skipped message="jpegtran not installed"/>
  </testcase>
</testsuite></testsuites>
"""


def _write(tmp_path, leg: str, body: str = JUNIT):
    d = tmp_path / f"junit-{leg}"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"pytest-results-{leg}.xml"
    p.write_text(body, encoding="utf-8")
    return p


def _run(**kw) -> qr.Run:
    base = {"cases": [], "legs": [], "stages": {}}
    base.update(kw)
    return qr.Run(**base)


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def test_parse_junit_reads_pass_fail_and_skip(tmp_path):
    _write(tmp_path, "3.14")
    cases, legs = qr.parse_junit([str(tmp_path)])
    assert legs == ["3.14"]
    assert {c.status for c in cases} == {"pass", "fail", "skip"}
    failure = next(c for c in cases if c.status == "fail")
    assert failure.message == "assert 1 == 2"
    assert failure.file == "tests/scrub/test_mp3.py" and failure.line == "42"


def test_parse_junit_merges_every_python_version(tmp_path):
    for leg in ("3.11", "3.12", "3.13", "3.14"):
        _write(tmp_path, leg)
    cases, legs = qr.parse_junit([str(tmp_path)])
    assert legs == ["3.11", "3.12", "3.13", "3.14"]
    assert len(cases) == 12                      # 3 tests x 4 versions
    # A failure present on every version is still ONE failing test to a reader.
    run = _run(cases=cases, legs=legs)
    assert run.failed == 4
    assert len(run.unique_failures) == 1


def test_missing_or_unreadable_inputs_do_not_crash(tmp_path):
    assert qr.parse_junit([str(tmp_path / "nope")]) == ([], [])
    bad = tmp_path / "junit-3.14"
    bad.mkdir()
    (bad / "broken.xml").write_text("<not-xml", encoding="utf-8")
    cases, _ = qr.parse_junit([str(tmp_path)])
    assert cases == []
    assert qr.load_json(str(tmp_path / "absent.json")) is None


def test_leg_is_recovered_from_the_artifact_path():
    assert qr._leg_from_path("artifacts/junit-3.12/pytest-results-3.12.xml") == "3.12"
    assert qr._leg_from_path("pytest-results.xml") == "local"


# --------------------------------------------------------------------------- #
# A silent no-show must never read as success
# --------------------------------------------------------------------------- #
def test_a_version_that_reported_nothing_is_a_failure(tmp_path):
    _write(tmp_path, "3.14")
    cases, legs = qr.parse_junit([str(tmp_path)])
    run = _run(cases=cases, legs=sorted({*legs, "3.11"}))
    assert run.missing_legs == ["3.11"]
    assert run.ok() is False
    md = qr.render_full(run)
    assert "no results" in md
    assert "3.11" in md


def test_stage_table_never_reads_green_when_nothing_ran():
    """"0 failed" is only good news if something actually ran. A leg whose job
    died before pytest started has no failures — and must not look passing."""
    table = qr.section_stage_table(_run(legs=["3.11"]))
    assert "✅ 0 passed" not in table
    assert "no results from Python 3.11" in table
    # Same for a test job that failed outside pytest entirely.
    assert "❌ failure" in qr.section_stage_table(_run(stages={"test": "failure"}))


def test_a_failing_stage_makes_the_verdict_red_even_with_no_test_failures():
    run = _run(stages={"evidence": "failure"})
    assert run.ok() is False
    assert "Something needs attention" in qr.render_full(run)


def test_a_clean_run_reads_as_green(tmp_path):
    _write(tmp_path, "3.14", JUNIT.replace(
        '<failure message="assert 1 == 2">boom</failure>', ""))
    cases, legs = qr.parse_junit([str(tmp_path)])
    run = _run(cases=cases, legs=legs,
               stages={"lint": "success", "test": "success"})
    assert run.ok() is True
    md = qr.render_full(run)
    assert "Everything passed" in md
    assert "## ❌ What failed" not in md


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def test_report_never_ships_an_unfilled_placeholder(tmp_path):
    _write(tmp_path, "3.14")
    cases, legs = qr.parse_junit([str(tmp_path)])
    md = qr.render_full(_run(cases=cases, legs=legs))
    assert "{{" not in md and "}}" not in md
    assert md.startswith(qr.MARKER)


def test_failure_text_is_readable_not_html_escaped(tmp_path):
    _write(tmp_path, "3.14", JUNIT.replace(
        'message="assert 1 == 2"', "message=\"assert 'a' == 'b'\""))
    cases, legs = qr.parse_junit([str(tmp_path)])
    md = qr.render_full(_run(cases=cases, legs=legs))
    # Inside a ``` fence GitHub renders bytes literally, so entities would show
    # up as literal &#x27; to the reader.
    assert "&#x27;" not in md
    assert "assert 'a' == 'b'" in md


def test_a_traceback_cannot_break_out_of_its_code_fence(tmp_path):
    _write(tmp_path, "3.14", JUNIT.replace(
        'message="assert 1 == 2"', 'message="see ``` here"'))
    cases, legs = qr.parse_junit([str(tmp_path)])
    md = qr.render_full(_run(cases=cases, legs=legs))
    assert "see ``` here" not in md          # neutralised
    assert "` ` `" in md


def test_the_flow_diagram_marks_the_broken_stage_red():
    run = _run(legs=["3.14"], stages={"lint": "failure", "test": "success"})
    diagram = qr.section_flow_diagram(run)
    assert "```mermaid" in diagram
    bad_line = next(ln for ln in diagram.splitlines()
                    if ln.strip().startswith("class ") and ln.endswith(" bad"))
    assert "LINT" in bad_line
    assert "classDef bad" in diagram


def test_badge_urls_escape_the_characters_shields_io_reserves():
    assert "86%25" in qr.badge("coverage", "86%", "green")
    assert "medium--tier" in qr.badge("threat model", "medium-tier", "grey")
    assert "threat_model" in qr.badge("threat model", "x", "grey")


def test_coverage_bar_is_proportional_and_fixed_width():
    assert qr.bar(0, 10) == "░" * 10
    assert qr.bar(100, 10) == "█" * 10
    assert qr.bar(50, 10).count("█") == 5
    assert len(qr.bar(37.4, 30)) == 30


def test_coverage_section_reports_the_measured_total():
    run = _run(coverage={"totals": {"percent_covered": 85.7, "covered_lines": 1054,
                                    "num_statements": 1230},
                         "files": {"src/scrub/cli.py": {
                             "summary": {"percent_covered": 77.0}}}})
    md = qr.section_coverage(run)
    assert "85.7%" in md and "1,054" in md and "src/scrub/cli.py" in md


def test_test_stage_duration_is_wall_clock_not_summed_across_versions():
    run = _run(legs=["3.11", "3.14"],
               timings={"test-3.11": 40.0, "test-3.14": 90.0})
    # The versions run in parallel: 1m 30s, not 2m 10s.
    assert "1m 30s" in qr.section_stage_table(run)


def test_jargon_is_translated_for_a_non_technical_reader():
    assert qr.humanize("test_f3_normalizes_encoder_fingerprint").startswith(
        "F3 (lossy re-encode)")
    assert qr.humanize("test_a1_leak") == "The metadata snoop leak"
    assert qr.humanize("test_removes_gps[F1-case2]").endswith("GPS location")


def test_lint_count_is_grammatical():
    assert "1 issue |" in qr.section_stage_table(_run(lint=[{"code": "F401"}]))
    assert "2 issues" in qr.section_stage_table(
        _run(lint=[{"code": "F401"}, {"code": "I001"}]))
    assert "clean" in qr.section_stage_table(_run(lint=[]))


def test_pr_comment_is_shorter_than_the_full_report(tmp_path):
    _write(tmp_path, "3.14")
    cases, legs = qr.parse_junit([str(tmp_path)])
    run = _run(cases=cases, legs=legs, coverage={"totals": {
        "percent_covered": 85.7, "covered_lines": 1, "num_statements": 2}})
    short, full = qr.render_pr_comment(run), qr.render_full(run)
    assert len(short) < len(full)
    assert short.startswith(qr.MARKER)          # so it can be updated in place
    assert "What failed" in short               # failures must survive the cut


def test_capabilities_come_from_the_measured_matrices_not_a_hardcoded_list():
    caps = qr.load_capabilities()
    assert caps, "no Pareto matrices found under tests/harness/results/"
    fmts = {c["fmt"] for c in caps}
    assert {"jpeg", "png", "mp3"} <= fmts
    # Nothing may be claimed for a format with no matrix on disk.
    assert "pdf" not in fmts
    md = qr.section_capabilities(_run())
    assert "MP3" in md


def test_capability_table_never_promises_identical_and_untraceable_at_once():
    """A blanket "keeps the file identical: yes" next to "untraceable: yes"
    would read as *both, in one pass* — which is false wherever untraceability
    needs the lossy mode. Each promise must name the mode that delivers it."""
    for c in qr.load_capabilities():
        assert "in F" in c["keeps"] or c["keeps"].startswith("❌"), c
        if c["untraceable"].startswith("✅"):
            assert "in F" in c["untraceable"], c
            # If untraceability costs quality, it cannot be one of the modes
            # that leave the file byte-identical.
            if "tiny" in c["untraceable"]:
                assert "F3" in c["untraceable"] and "F3" not in c["keeps"], c
    md = qr.section_capabilities(_run())
    assert "can need different modes" in md


def test_a_drifted_published_claim_is_shown_not_buried():
    """The one failure this project cares most about: the tool's behaviour
    changed but the published results table still says the old thing."""
    run = _run(stages={"evidence": "failure"}, evidence={
        "ok": False,
        "differences": [{"format": "mp3", "adversary": "A2", "fidelity": "F3",
                         "published": "pass", "measured": "fail"}]})
    md = qr.render_full(run)
    assert "no longer true" in md
    assert "| MP3 | A2 at F3 | pass | **fail** |" in md
    # And the limits section must point at it, not read as business as usual.
    assert "Heads up" in md


def test_a_confirmed_evidence_run_says_the_claims_were_re_proven():
    md = qr.render_full(_run(evidence={"ok": True, "confirmed": 27}))
    assert "re-confirmed" in md
    assert "not cached" in md


def test_slowest_checks_lists_each_check_once_not_once_per_version(tmp_path):
    for leg in ("3.11", "3.12", "3.13", "3.14"):
        _write(tmp_path, leg)
    cases, legs = qr.parse_junit([str(tmp_path)])
    md = qr.section_slowest(_run(cases=cases, legs=legs))
    # 3 distinct tests ran on 4 versions; the table must show 3 rows, not 12.
    assert md.count("|\n") - 2 == 3 or md.count("s |") == 3


def test_empty_run_still_renders_a_report():
    md = qr.render_full(_run())
    assert qr.MARKER in md
    assert "{{" not in md
