"""The CI report must publish the honest limits, from docs/limits.md.

Why this is a test and not a convention: the limits table used to live as prose
inside scripts/qa_report.py, and it went stale the moment a phase shipped — it still
told readers "JPEG and PNG only" after MP3 had landed. A limits section that silently
understates what the tool cannot do is worse than no section, because readers take it
as complete. These tests fail loudly if the single source of truth goes missing or
stops reaching the report.
"""
from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import qa_report  # noqa: E402


def test_limits_doc_exists_and_is_parseable():
    limits, residuals = qa_report.load_limits()
    assert limits, "docs/limits.md missing or has no LIMITS block"
    assert residuals, "docs/limits.md missing or has no RESIDUALS block"
    assert limits.count("\n") >= 4, "limits table looks truncated"


def test_report_embeds_the_limits_rather_than_its_own_copy():
    limits, residuals = qa_report.load_limits()
    cats = [{"key": "k", "icon": "", "title": "T", "desc": "d", "passed": 1,
             "failed": 0, "skipped": 0, "total": 1, "fails": []}]
    md = qa_report.render_markdown(cats, "flow", [], {}, limits, residuals)
    assert "{{LIMITS}}" not in md and "{{RESIDUALS}}" not in md
    first_row = limits.splitlines()[2]          # header, separator, then row 1
    assert first_row in md, "the rendered report does not contain the limits table"


def test_missing_limits_doc_is_reported_not_hidden():
    """If the doc vanishes, the report must say so — never render a limits-free
    page that reads as a clean bill of health."""
    cats = [{"key": "k", "icon": "", "title": "T", "desc": "d", "passed": 1,
             "failed": 0, "skipped": 0, "total": 1, "fails": []}]
    md = qa_report.render_markdown(cats, "flow", [], {}, "", "")
    assert "missing" in md.lower() and "clean bill of health" in md.lower()


def test_known_open_limits_are_actually_documented():
    """Guards specific limits we measured but could otherwise forget to publish.
    Each keyword ties to a real finding; delete a row only when the limit is fixed."""
    limits, residuals = qa_report.load_limits()
    text = (limits + residuals).lower()
    for phrase, why in [
        ("group", "MP3 F3 anonymity is per sample-rate group, not universal"),
        ("refuse", "Layer II / free-format inputs are rejected outright"),
        ("microphone", "the audio analogue of PRNU (mic/room/mains hum)"),
        ("mdct", "deeper encoder-internal classifiers are untested"),
        ("m4a", "M4A is unsupported and MAT2 refuses it — a stated gap"),
    ]:
        assert phrase in text, f"undocumented limit: {why}"
