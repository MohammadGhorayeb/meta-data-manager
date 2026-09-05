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


def _require_scrub(result, in_path: str, fidelity: str) -> None:
    """Turn a failed scrub into a clear error instead of a mystery further down.

    `InProcessScrubber.run` deliberately catches exceptions and returns
    `ScrubResult(ok=False, ...)` so a broken scrub cannot crash the oracle. Both
    call sites then opened the output path regardless, so a scrub that never wrote a
    file surfaced as `FileNotFoundError` inside the harness with no mention of which
    format, which tier, or why.

    That was found when a DOCX F3 run collided with another LibreOffice process — F3
    re-typesets through an engine that serialises on its own profile lock, so it is
    the first tier in this project that can fail for a purely environmental reason.
    The bug was older than that tier; the tier is only what made it happen.
    """
    if result is not None and getattr(result, "ok", True) is False:
        raise RuntimeError(
            f"scrub failed for {in_path} at {fidelity}: "
            f"{getattr(result, 'stderr', '') or 'no reason given'}")


def _scrub_n(scrubber, input_path: str, fidelity: str, n: int) -> tuple[list[str], list[bytes]]:
    tmpdir = tempfile.mkdtemp(prefix="floor_")
    out_paths, blobs = [], []
    for r in range(n):
        op = os.path.join(tmpdir, f"out_{r}")
        _require_scrub(scrubber.run(input_path, op, fidelity), input_path, fidelity)
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
