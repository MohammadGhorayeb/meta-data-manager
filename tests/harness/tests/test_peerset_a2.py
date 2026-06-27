"""OPTIONAL gate: AC5 -- A2 peer-set source fingerprinting.

Run: pytest tests/harness/tests/test_peerset_a2.py
"""
from __future__ import annotations

from tests.harness.contract import V
from tests.harness.oracle import peerset


def test_ac5_per_source_stub_leaks(toy_plugin, per_source_stub, a2_corpus):
    sources = a2_corpus(n_sources=2, members_per_source=3)
    cell = peerset.evaluate_a2(per_source_stub, toy_plugin, sources, "F1")

    assert cell.verdict is V.FAIL
    sf = [l for l in cell.leaks if l.kind == "source_fingerprint"]
    assert len(sf) >= 1
    assert sf[0].correlates_with.startswith("source:")


def test_ac5_clean_stub_passes_a2(toy_plugin, clean_stub, a2_corpus):
    sources = a2_corpus(n_sources=2, members_per_source=3)
    cell = peerset.evaluate_a2(clean_stub, toy_plugin, sources, "F1")

    assert cell.verdict is V.PASS
    assert cell.leaks == []
    assert cell.floor.deterministic is True
