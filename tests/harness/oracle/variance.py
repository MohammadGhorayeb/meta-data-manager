"""THE engine: between-group vs within-group variance decomposition.

One decomposition powers floor / A1 / A2 / fingerprint-guard -- only the
grouping factor changes (Part A §0):

  grouping = repeat   -> within IS the floor (defines the yardstick)
  grouping = variant  -> between = metadata leak       (A1)
  grouping = source   -> between = source fingerprint  (A2)
  grouping = input    -> invariant feature = tool signature (guard)

Grid model: cells[(group, repeat)] = FeatureMap = {feature_id: value}, value a
hashable token (bytes in byte/field space). Categorical (Phase 0).
"""
from __future__ import annotations

from collections.abc import Hashable
from dataclasses import dataclass

FeatureMap = dict[str, Hashable]


@dataclass
class FeatureVerdict:
    feature_id: str
    floor: bool          # varies within at least one group (part of the noise floor)
    leak: bool           # separates groups beyond within-group variation
    n_between: int       # distinct representative values across groups
    max_within: int      # max distinct values within any single group


def _rep(values_by_repeat: list[Hashable]) -> Hashable:
    # representative value for a group: the mode (most common across repeats)
    from collections import Counter
    return Counter(values_by_repeat).most_common(1)[0][0]


def decompose(cells: dict[tuple[str, int], FeatureMap],
              groups: list[str], repeats: list[int]) -> dict[str, FeatureVerdict]:
    feats: set[str] = set()
    for fm in cells.values(): feats |= set(fm.keys())
    out: dict[str, FeatureVerdict] = {}
    SENTINEL = object()
    for fid in feats:
        max_within = 0
        reps = []
        for g in groups:
            vals = [cells.get((g, r), {}).get(fid, SENTINEL) for r in repeats]
            present = [v for v in vals if v is not SENTINEL]
            if not present:
                continue
            max_within = max(max_within, len(set(present)))
            reps.append(_rep(present))
        n_between = len(set(reps)) if reps else 0
        floor = max_within > 1
        # PRIVACY-CONSERVATIVE rule: flag if group-separation exceeds within-group noise.
        # categorical operationalization: distinct-across-groups > worst-case distinct-within-group.
        leak = n_between > max(max_within, 1)
        out[fid] = FeatureVerdict(fid, floor, leak, n_between, max_within)
    return out
