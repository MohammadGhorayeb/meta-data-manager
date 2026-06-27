"""A2 verdict: variance over (source x repeat) (Part B §5.5).

Reuses the §5.1 engine with `source` as the grouping factor. A surviving feature
is a source-fingerprint iff it is (near-)constant within each same-source group
AND differs across sources -- the two-pronged anonymity-set rule (Part A §7),
which is exactly `floor == False and n_between > 1`, i.e. `leak == True`.

Synthetic A2 holds content byte-identical across all members and sources, so
byte-space features are not confounded by content. For real peers (content
differs) operate in feature space only via fields.extract.
"""
from __future__ import annotations

import os
import tempfile

from ..contract import Cell, Leak, V
from . import variance, diff, floor as floor_mod
from .leak import _scrub, _evidence


def evaluate_a2(scrubber, plugin, sources: dict[str, list[str]], fidelity: str,
                n: int | None = None) -> Cell:
    source_ids = list(sources.keys())
    members = min(len(v) for v in sources.values())
    nn = members if n is None else min(n, members)
    repeats = list(range(nn))
    tmpdir = tempfile.mkdtemp(prefix="a2_")

    floor_report, _ = floor_mod.measure(scrubber, plugin, sources[source_ids[0]][0],
                                        fidelity, n=nn)

    use_fields = (fidelity == "F3")
    if use_fields:
        from . import fields
    grid_blobs: dict[tuple[str, int], bytes] = {}
    grid_paths: dict[tuple[str, int], str] = {}
    grid_maps: dict[tuple[str, int], dict] = {}
    for sid in source_ids:
        for r in repeats:
            op = os.path.join(tmpdir, f"{sid}_r{r}")
            b = _scrub(scrubber, sources[sid][r], op, fidelity)
            grid_paths[(sid, r)] = op
            if use_fields:
                grid_maps[(sid, r)] = fields.extract(op, plugin, fidelity)
            else:
                grid_blobs[(sid, r)] = b

    cells = grid_maps if use_fields else diff.to_feature_maps(grid_blobs)
    verdicts = variance.decompose(cells, source_ids, repeats)

    rep_blob = next(iter(grid_blobs.values())) if grid_blobs else None
    rep_path = grid_paths[(source_ids[0], 0)]

    leaks: list[Leak] = []
    for fid, fv in verdicts.items():
        if not fv.leak:
            continue
        loc = diff.locus_from(fid, rep_blob)
        if loc.offset is not None:
            try:
                loc.annotated_as = plugin.annotate(rep_path, loc.offset)
            except Exception:
                loc.annotated_as = None
        correlates = f"source:{loc.annotated_as or fid}"
        leaks.append(Leak(kind="source_fingerprint", locus=loc,
                          correlates_with=correlates,
                          evidence=_evidence(cells, fid, source_ids, repeats)))

    verdict = V.FAIL if leaks else V.PASS
    reason = "source_fingerprint" if leaks else None
    return Cell(adversary="A2", fidelity=fidelity, verdict=verdict,
                floor=floor_report, leaks=leaks, perceptual=None, reason=reason)
