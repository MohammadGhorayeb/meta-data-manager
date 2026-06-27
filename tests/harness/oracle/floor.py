"""Self-determinism baseline -- the noise floor (Part A §5, Part B §5.3).

Scrub one representative input N times. The floor is the set of feature
positions whose value differs across the N repeats. F1/deterministic-F2 =>
floor empty. The floor is literally the within-group axis of variance.decompose,
so measure() returns the per-repeat outputs/maps for callers to fold into the
bigger grid.
"""
from __future__ import annotations

import os
import tempfile

from .. import config
from ..contract import FloorReport
from . import diff


def _scrub_n(scrubber, input_path: str, fidelity: str, n: int) -> tuple[list[str], list[bytes]]:
    tmpdir = tempfile.mkdtemp(prefix="floor_")
    out_paths, blobs = [], []
    for r in range(n):
        op = os.path.join(tmpdir, f"out_{r}")
        scrubber.run(input_path, op, fidelity)
        out_paths.append(op)
        with open(op, "rb") as f:
            blobs.append(f.read())
    return out_paths, blobs


def measure(scrubber, plugin, input_path: str, fidelity: str,
            n: int = config.N_REPEATS):
    """Returns (FloorReport, repeats) where `repeats` is the list of per-repeat
    byte blobs (F1/F2) or FeatureMaps (F3), so callers can reuse them as the
    within-group axis of decompose()."""
    out_paths, blobs = _scrub_n(scrubber, input_path, fidelity, n)

    if fidelity == "F3":
        from . import fields  # lazy: F3 ground-truth needs exiftool/structural
        maps = [fields.extract(p, plugin, fidelity) for p in out_paths]
        variable_loci = []
        feats: set[str] = set()
        for m in maps:
            feats |= set(m.keys())
        for fid in sorted(feats):
            values = [m.get(fid) for m in maps]
            if len({repr(v) for v in values}) > 1:
                variable_loci.append(diff.locus_from(fid))
        report = FloorReport(deterministic=not variable_loci, repeats=n,
                             variable_loci=variable_loci)
        return report, maps

    # F1 / F2: byte space
    aligned = diff.align_features(blobs)
    variable_loci = []
    for fid, values in aligned.items():
        if len({bytes(v) if isinstance(v, (bytes, bytearray)) else v for v in values}) > 1:
            variable_loci.append(diff.locus_from(fid, blobs[0]))
    report = FloorReport(deterministic=not variable_loci, repeats=n,
                         variable_loci=variable_loci)
    return report, blobs
