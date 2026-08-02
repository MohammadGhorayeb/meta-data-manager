"""REQUIRED Phase 0 exit gate: AC1-AC3 (+ AC4 recommended, AC-matrix).

Run: pytest tests/harness/tests/test_exit_gate.py
The gate is satisfied iff AC1-AC3 pass.
"""
from __future__ import annotations

import os

from tests.harness import config
from tests.harness.contract import V
from tests.harness.corpus import synthetic
from tests.harness.oracle import fingerprint_guard, floor, leak
from tests.harness.runner import matrix
from tests.harness.runner import run as runner
from tests.harness.runner.cases import Case
from tests.harness.stubs.version_stub import VERSION_TAG

SENT = synthetic.A1_SENTINEL_FIELD


# --- AC1: leaky flagged (REQUIRED) ---
def test_ac1_leaky_flagged(toy_plugin, leaky_stub, a1_variants):
    variants = a1_variants(n_variants=3)
    cell = leak.evaluate_a1(leaky_stub, toy_plugin, variants, "F1", sentinel_field=SENT)

    assert cell.verdict is V.FAIL
    vcl = [l for l in cell.leaks if l.kind == "variant_correlated"]
    assert len(vcl) >= 1
    lk = vcl[0]
    # locus falls in the surviving record's value bytes
    assert lk.locus.space == "byte" and lk.locus.offset is not None
    assert lk.locus.annotated_as and lk.locus.annotated_as.startswith("tlv@")
    # correlates_with names the sentinel field
    assert lk.correlates_with == f"metadata_variant:{SENT}"
    # the evidence shows the distinct per-variant sentinels (A/B/C => 41/42/43)
    assert "41" in lk.evidence and "42" in lk.evidence
    # the floor is empty
    assert cell.floor.deterministic is True
    assert cell.floor.variable_loci == []


# --- AC2: clean passes (REQUIRED) ---
def test_ac2_clean_passes(toy_plugin, clean_stub, a1_variants):
    variants = a1_variants(n_variants=3)
    cell = leak.evaluate_a1(clean_stub, toy_plugin, variants, "F1", sentinel_field=SENT)

    assert cell.verdict is V.PASS
    assert cell.leaks == []
    assert cell.floor.deterministic is True
    assert cell.floor.variable_loci == []


# --- AC3: fingerprint guard catches version stub (REQUIRED) ---
def test_ac3_version_passes_a1_but_guard_fails(toy_plugin, version_stub, clean_stub,
                                               a1_variants, diverse_inputs):
    variants = a1_variants(n_variants=3)
    # per-pair oracle PASSES (signature is variant-invariant) -- proves independence
    cell = leak.evaluate_a1(version_stub, toy_plugin, variants, "F1", sentinel_field=SENT)
    assert cell.verdict is V.PASS
    assert cell.leaks == []

    # the guard FAILS it, decoding the tool signature
    verdict, sigs = fingerprint_guard.evaluate(version_stub, toy_plugin, diverse_inputs,
                                               "F1", min_len=config.MIN_SIG_LEN)
    assert verdict == "fail"
    # decodes to whatever signature the stub stamps (tag-agnostic, robust to changes)
    expected_tag = VERSION_TAG.lstrip(b"\x00").decode("latin-1")
    assert any(expected_tag in s["decoded"] for s in sigs)

    # and PASSES the clean stub
    cverdict, _ = fingerprint_guard.evaluate(clean_stub, toy_plugin, diverse_inputs,
                                             "F1", min_len=config.MIN_SIG_LEN)
    assert cverdict == "pass"


# --- AC4: non-empty floor handled (RECOMMENDED) ---
def test_ac4_noisy_floor_is_not_a_leak(toy_plugin, noisy_clean_stub, a1_variants):
    variants = a1_variants(n_variants=3, n_repeats=5)
    fr, _ = floor.measure(noisy_clean_stub, toy_plugin, variants[0][0], "F1", n=5)
    assert fr.deterministic is False
    assert fr.variable_loci  # the nonce locus

    cell = leak.evaluate_a1(noisy_clean_stub, toy_plugin, variants, "F1", sentinel_field=SENT)
    assert cell.verdict is V.PASS  # nonce classified as floor, not leak
    assert cell.leaks == []


# --- AC-matrix: assemble validates, A3 emitted as not_tested, evidence resolves ---
def test_ac_matrix_end_to_end(content, toy_plugin, clean_stub, diverse_inputs, tmp_path):
    corpus_dir = str(tmp_path / "corpus")
    results_dir = str(tmp_path / "results")
    evidence_base = str(tmp_path / "evidence")
    cases = [
        Case("toyf", "F1", "A1", clean_stub, toy_plugin,
             {"content": content, "n_variants": 3}),
        Case("toyf", "F1", "A2", clean_stub, toy_plugin,
             {"content": content, "n_sources": 2, "members_per_source": 3}),
    ]
    doc = runner.run_suite(cases, clean_stub, diverse_inputs, "toyf",
                           {"name": "clean_stub", "version": "0.1"}, corpus_dir,
                           plugin=toy_plugin, results_dir=results_dir,
                           evidence_base=evidence_base)

    matrix.validate(doc)  # raises on violation

    a3 = [c for c in doc["cells"] if c["adversary"] == "A3"]
    assert len(a3) == 3
    assert all(c["verdict"] == "not_tested" and c["reason"] == "out_of_scope_threat_model"
               for c in a3)

    assert os.path.exists(os.path.join(results_dir, "toyf_clean_stub.json"))

    # evidence pointers resolve to existing files
    for c in doc["cells"]:
        ed = c.get("evidence_dir")
        if ed:
            assert os.path.isdir(ed)
            assert os.path.exists(os.path.join(ed, "evidence.json"))
