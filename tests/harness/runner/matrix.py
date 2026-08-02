"""Pareto-matrix assembly + JSON write/validate (Part B §8.2).

Emits A3 cells (verdict not_tested / out_of_scope_threat_model) for shape parity
with the research matrices, without testing A3.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

import jsonschema

from .. import config
from ..contract import Cell, FloorReport, Leak, Locus, PerceptualReport, V

FIDELITIES = ["F1", "F2", "F3"]


def _verdict_str(v) -> str:
    return v.value if isinstance(v, V) else v


def _locus_to_dict(loc: Locus) -> dict:
    return {
        "space": loc.space,
        "offset": loc.offset,
        "length": loc.length,
        "feature_id": loc.feature_id,
        "annotated_as": loc.annotated_as,
        "hex_context": loc.hex_context,
    }


def _leak_to_dict(lk: Leak) -> dict:
    return {
        "kind": lk.kind,
        "locus": _locus_to_dict(lk.locus),
        "correlates_with": lk.correlates_with,
        "evidence": lk.evidence,
    }


def _floor_to_dict(fr: FloorReport) -> dict:
    return {
        "deterministic": fr.deterministic,
        "repeats": fr.repeats,
        "variable_loci": [_locus_to_dict(l) for l in fr.variable_loci],
    }


def _perceptual_to_dict(pr: PerceptualReport) -> dict:
    return {
        "checked": pr.checked,
        "metric": pr.metric,
        "distance": pr.distance,
        "threshold": pr.threshold,
        "preserved": pr.preserved,
    }


def cell_to_dict(cell: Cell) -> dict:
    d: dict = {
        "adversary": cell.adversary,
        "fidelity": cell.fidelity,
        "verdict": _verdict_str(cell.verdict),
    }
    if cell.reason is not None:
        d["reason"] = cell.reason
    if cell.floor is not None:
        d["noise_floor"] = _floor_to_dict(cell.floor)
    if cell.leaks:
        d["leaks"] = [_leak_to_dict(l) for l in cell.leaks]
    if cell.perceptual is not None:
        d["perceptual"] = _perceptual_to_dict(cell.perceptual)
    if cell.evidence_dir is not None:
        d["evidence_dir"] = cell.evidence_dir
    return d


def fingerprint_block(verdict: str, signatures: list[dict] | None = None,
                      mandatory_constants_excluded: list[dict] | None = None,
                      checked: bool = True) -> dict:
    return {
        "checked": checked,
        "verdict": verdict,
        "mandatory_constants_excluded": mandatory_constants_excluded or [],
        "signatures": signatures or [],
    }


def assemble(format_id: str, tool: dict, cells: list[Cell], fingerprint: dict,
             generated_at: str | None = None) -> dict:
    if generated_at is None:
        generated_at = datetime.now(UTC).isoformat()
    cell_dicts = [cell_to_dict(c) for c in cells]
    # A3 cells: emitted but never tested.
    for f in FIDELITIES:
        cell_dicts.append({
            "adversary": "A3", "fidelity": f,
            "verdict": "not_tested", "reason": "out_of_scope_threat_model",
        })
    return {
        "format": format_id,
        "tool": tool,
        "generated_at": generated_at,
        "harness_version": config.HARNESS_VERSION,
        "cells": cell_dicts,
        "scrubber_fingerprint": fingerprint,
    }


def _schema() -> dict:
    with open(config.SCHEMA_PATH) as f:
        return json.load(f)


def validate(doc: dict) -> None:
    """Raise on schema violation."""
    jsonschema.validate(doc, _schema())


def write(doc: dict, path) -> None:
    import os
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as f:
        json.dump(doc, f, indent=2)
    validate(doc)
