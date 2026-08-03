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
import re
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


def test_badge_urls_cannot_break_out_of_a_markdown_link():
    """A bare ')' in the URL would end the link destination early. Balanced
    parens happen to survive CommonMark; an unbalanced one would not."""
    markdown = qr.badge("threat model", "medium-tier (A2)", "5f3dc4")
    destination = markdown.split("](", 1)[1].removesuffix(")")
    assert "(" not in destination and ")" not in destination
    assert "%28A2%29" in destination


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
        "Full rebuild")
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
    lossless = ("Light clean", "Deep clean")
    for c in qr.load_capabilities():
        # Each promise must name the mode that delivers it, never a bare "yes".
        assert c["keeps"].startswith("❌") or any(m in c["keeps"] for m in lossless), c
        if c["untraceable"].startswith("✅"):
            assert any(m in c["untraceable"]
                       for m in (*lossless, "Full rebuild")), c
            # If untraceability costs quality it must be the rebuild, which is
            # by definition not one of the modes that leave the file identical.
            if "tiny" in c["untraceable"]:
                assert "Full rebuild" in c["untraceable"], c
                assert "Full rebuild" not in c["keeps"], c
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


def test_untested_files_are_named_in_the_report_not_just_excluded():
    """Scaffolding is excluded from the pass mark so that starting a feature
    does not fail the build — but excluded must never mean invisible."""
    run = _run(
        coverage={"totals": {"percent_covered": 70.2, "covered_lines": 1318,
                             "num_statements": 1771}, "files": {}},
        coverage_gate={"gated_percent": 84.8, "overall_percent": 70.2,
                       "floor": 82, "passed": True, "exercised_files": 26,
                       "untouched_statements": 131,
                       "untouched_files": [
                           {"path": "src/scrub/standards/isobmff.py",
                            "statements": 131}]})
    md = qr.section_coverage(run)
    assert "84.8%" in md and "floor 82%" in md
    assert "src/scrub/standards/isobmff.py" in md
    assert "131" in md
    assert "have no test at all yet" in md


def test_per_format_story_comes_first_and_build_detail_after():
    """The report leads with what the tool does per file type; how the build
    itself went is secondary. Order is the whole point of this section."""
    md = qr.render_full(_run(legs=["3.14"]))
    formats = md.index("What the tool does with each kind of file")
    build = md.index("How this build went")
    assert formats < build
    # Images first, then each audio format, then the roadmap.
    order = [md.index(h) for h in ("🖼️ Images", "🎵 MP3 audio", "🎶 FLAC audio",
                                   "🎬 M4A audio", "What is coming next")]
    assert order == sorted(order), "format sections are out of build order"


def test_each_format_states_what_works_and_what_does_not():
    md = qr.section_formats(_run())
    assert "**What works**" in md
    assert "**What does not, yet**" in md
    # JPEG needs the lossy mode; the report must name which one and its cost.
    assert "erased by **Full rebuild**, for a tiny invisible amount" in md
    # PNG gets there for free — the distinction readers actually care about.
    assert "erased by **Deep clean**, at no cost to quality at all" in md


def test_the_reader_facing_part_never_says_f1_f2_or_f3():
    """"F1/F2/F3" is internal shorthand. A non-technical reader should never
    have to learn it, so it appears in exactly one place: the glossary row that
    maps the friendly names back to the codes for anyone reading the docs."""
    reader_facing = "\n".join([
        qr.section_formats(_run()),
        qr.section_limits(*qr.load_limits()).split("Where the evidence lives")[0],
    ])
    assert not re.search(r"\bF[123]\b", reader_facing), \
        "internal tier codes leaked into the plain-language part of the report"


def test_the_technical_glossary_still_maps_the_codes():
    """Jargon-free for readers, but someone cross-referencing the code or the
    docs must still be able to line the two vocabularies up."""
    md = qr.section_capabilities(_run())
    assert "🟢 Light clean | `F1`" in md
    assert "🔵 Deep clean | `F2`" in md
    assert "🟠 Full rebuild | `F3`" in md


def test_a_format_with_no_measured_win_is_not_dressed_up():
    """M4A is not untraceable yet. The report must say so plainly rather than
    letting the tag-removal pass imply the whole job is done."""
    caps = {c["fmt"]: c for c in qr.load_capabilities()}
    if "m4a" not in caps:
        return
    works, open_, solved = qr._verdict_lines(caps["m4a"])
    assert solved == ""
    assert any("not untraceable yet" in o for o in open_)
    assert not any("Untraceable" in w for w in works)


def test_every_published_format_has_a_plain_language_story():
    """A format that publishes results but has no docs/formats.md block would
    render measurements with no explanation — the report must flag that."""
    stories = qr.load_format_stories()
    published = {c["fmt"] for c in qr.load_capabilities()}
    missing = published - set(stories)
    assert not missing, (
        f"no FORMAT block in docs/formats.md for: {', '.join(sorted(missing))}")


def test_a_missing_format_story_is_reported_not_silently_skipped(monkeypatch):
    monkeypatch.setattr(qr, "load_format_stories", dict)
    md = qr.section_formats(_run())
    assert "No plain-language explanation" in md
    assert "docs/formats.md" in md


def test_empty_run_still_renders_a_report():
    md = qr.render_full(_run())
    assert qr.MARKER in md
    assert "{{" not in md
