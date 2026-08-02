"""Orchestration: corpus -> floor -> extract -> adversary -> verdict (Part B §7.3)."""
from __future__ import annotations

import hashlib
import json
import os

from .. import config
from ..contract import Cell
from ..corpus import content_identity, synthetic
from ..oracle import fingerprint_guard, leak, peerset
from . import matrix
from .cases import Case


def _evidence_dir(base, format_id, adversary, fidelity) -> str:
    d = os.path.join(base, f"{format_id}_{adversary}_{fidelity}")
    os.makedirs(d, exist_ok=True)
    return d


def _sha(path) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def run_case(case: Case, tmpdir: str, evidence_base: str | None = None) -> Cell:
    """Build corpus -> assert content-identity -> evaluate adversary -> write
    evidence JSON -> return the Cell (with evidence_dir set)."""
    evidence_base = evidence_base or str(config.EVIDENCE_DIR)
    os.makedirs(tmpdir, exist_ok=True)
    content = case.corpus["content"]
    plugin = case.plugin

    if case.adversary == "A1":
        variants = synthetic.make_a1_variants(
            content,
            n_variants=case.corpus.get("n_variants", config.N_VARIANTS),
            n_repeats=case.n_repeats, tmpdir=tmpdir,
        )
        # content-identity self-check across variant representatives
        flat = [g[0] for g in variants]
        for p in flat[1:]:
            assert content_identity.same_content(flat[0], p, "bytes", plugin), \
                "A1 corpus failed content-identity"
        cell = leak.evaluate_a1(
            case.scrubber, plugin, variants, case.fidelity,
            n=case.n_repeats, sentinel_field=synthetic.A1_SENTINEL_FIELD,
            modality=case.modality,
        )
        table = {f"v{i}": [os.path.basename(p) for p in g] for i, g in enumerate(variants)}
        repeat_hashes = {f"v{i}": _sha(g[0]) for i, g in enumerate(variants)}
    elif case.adversary == "A2":
        sources = synthetic.make_a2_corpus(
            content,
            n_sources=case.corpus.get("n_sources", config.N_SOURCES),
            members_per_source=case.corpus.get("members_per_source", config.MEMBERS_PER_SOURCE),
            n_repeats=case.n_repeats, tmpdir=tmpdir,
        )
        cell = peerset.evaluate_a2(case.scrubber, plugin, sources, case.fidelity)
        table = {sid: [os.path.basename(p) for p in ps] for sid, ps in sources.items()}
        repeat_hashes = {sid: _sha(ps[0]) for sid, ps in sources.items()}
    else:
        raise ValueError(f"unsupported adversary {case.adversary!r}")

    ed = _evidence_dir(evidence_base, case.format_id, case.adversary, case.fidelity)
    evidence = {
        "variant_or_source_table": table,
        "repeat_output_hashes": repeat_hashes,
        "leaks": [
            {"kind": l.kind, "correlates_with": l.correlates_with,
             "feature_id": l.locus.feature_id, "hex_context": l.locus.hex_context,
             "evidence": l.evidence}
            for l in cell.leaks
        ],
        "noise_floor": {
            "deterministic": cell.floor.deterministic if cell.floor else None,
            "variable_loci": [l.feature_id for l in (cell.floor.variable_loci if cell.floor else [])],
        },
        "verdict": cell.verdict.value if hasattr(cell.verdict, "value") else cell.verdict,
    }
    with open(os.path.join(ed, "evidence.json"), "w") as f:
        json.dump(evidence, f, indent=2)
    cell.evidence_dir = ed
    return cell


def run_suite(cases: list[Case], scrubber_for_guard, diverse_inputs: list[str],
              format_id: str, tool: dict, tmpdir: str, plugin=None,
              results_dir: str | None = None, evidence_base: str | None = None) -> dict:
    """Run all cells, run the fingerprint guard once over a diverse input set,
    assemble + write + validate the Pareto matrix."""
    results_dir = results_dir or str(config.RESULTS_DIR)
    cells = [run_case(c, tmpdir, evidence_base=evidence_base) for c in cases]

    guard_plugin = plugin or (cases[0].plugin if cases else None)
    gv, gsig = fingerprint_guard.evaluate(scrubber_for_guard, guard_plugin,
                                          diverse_inputs, "F1",
                                          min_len=config.MIN_SIG_LEN)
    excluded = [{"bytes_hex": c.hex(), "decoded": c.decode("latin-1", "replace")}
                for c in (guard_plugin.mandatory_constants() if guard_plugin else [])]
    fp = matrix.fingerprint_block(gv, gsig, excluded)

    doc = matrix.assemble(format_id, tool, cells, fp)
    out_path = os.path.join(results_dir, f"{format_id}_{tool['name']}.json")
    matrix.write(doc, out_path)
    return doc
