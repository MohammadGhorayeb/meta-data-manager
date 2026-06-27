"""Byte-diff -> offset-aligned features + hex context (Part B §5.2).

Generic, format-agnostic localization only: byte-range (offset, length) plus a
hex-context window. Naming a structure is plugin work (annotate); Phase 0
reports offsets + hex only (Part A §9).

Feature-id vocabulary (shared by floor/leak/peerset/tests):
  "byte@{offset}:{length}"     -- a maximal run where the cohort disagrees
  "byte_tail@{first_div}"      -- coarse feature when blobs differ in length
  "length_divergence"          -- marker feature (per-blob value = len(blob))
"""
from __future__ import annotations

from ..config import HEX_CONTEXT
from ..contract import Locus

FeatureMap = dict[str, object]


def align_features(blobs: list[bytes]) -> dict[str, list[bytes]]:
    """Positional byte-range features over a same-cohort set of outputs.

    Equal length: walk offsets, group maximal runs where the blobs disagree,
    emit one feature per run with feature_id "byte@{offset}:{length}" and value
    per blob = the run bytes. Equal runs contribute no feature.

    Differing length: emit one coarse "byte_tail@{first_divergence}" (the tail
    from first divergence) plus a "length_divergence" marker.

    Returns feature_id -> list of per-blob values, aligned to the input order.
    """
    if not blobs:
        return {}
    lengths = {len(b) for b in blobs}
    if len(lengths) != 1:
        first_div = _first_divergence(blobs)
        return {
            f"byte_tail@{first_div}": [b[first_div:] for b in blobs],
            "length_divergence": [len(b) for b in blobs],
        }

    L = lengths.pop()
    out: dict[str, list[bytes]] = {}
    i = 0
    while i < L:
        if _all_agree_at(blobs, i):
            i += 1
            continue
        start = i
        while i < L and not _all_agree_at(blobs, i):
            i += 1
        length = i - start
        out[f"byte@{start}:{length}"] = [b[start:start + length] for b in blobs]
    return out


def _all_agree_at(blobs: list[bytes], i: int) -> bool:
    first = blobs[0][i]
    return all(b[i] == first for b in blobs)


def _first_divergence(blobs: list[bytes]) -> int:
    m = min(len(b) for b in blobs)
    for i in range(m):
        if not _all_agree_at(blobs, i):
            return i
    return m  # agree up to the shorter length; divergence is the length itself


def hex_context(blob: bytes, offset: int, length: int, ctx: int = HEX_CONTEXT) -> str:
    """Hex dump of [offset-ctx, offset+length+ctx)."""
    lo = max(0, offset - ctx)
    hi = min(len(blob), offset + (length or 0) + ctx)
    return blob[lo:hi].hex()


def locus_from(fid: str, blob: bytes | None = None, ctx: int = HEX_CONTEXT) -> Locus:
    """Parse a feature_id into a Locus, optionally attaching hex context."""
    if fid.startswith("byte@"):
        body = fid[len("byte@"):]
        off_s, len_s = body.split(":")
        offset, length = int(off_s), int(len_s)
        hc = hex_context(blob, offset, length, ctx) if blob is not None else None
        return Locus(space="byte", offset=offset, length=length, feature_id=fid, hex_context=hc)
    if fid.startswith("byte_tail@"):
        offset = int(fid[len("byte_tail@"):])
        hc = hex_context(blob, offset, 0, ctx) if blob is not None else None
        return Locus(space="byte", offset=offset, length=None, feature_id=fid, hex_context=hc)
    if fid == "length_divergence":
        return Locus(space="structural", feature_id=fid)
    if fid.startswith("field:"):
        return Locus(space="field", feature_id=fid)
    if fid.startswith("struct:"):
        return Locus(space="structural", feature_id=fid)
    return Locus(space="byte", feature_id=fid)


def to_feature_maps(grid_blobs: dict[tuple[str, int], bytes]) -> dict[tuple[str, int], FeatureMap]:
    """Turn a (group, repeat) -> bytes grid into (group, repeat) -> FeatureMap by
    running align_features across the whole grid (so feature positions are shared)."""
    keys = list(grid_blobs.keys())
    blobs = [grid_blobs[k] for k in keys]
    aligned = align_features(blobs)
    cells: dict[tuple[str, int], FeatureMap] = {k: {} for k in keys}
    for fid, values in aligned.items():
        for idx, k in enumerate(keys):
            cells[k][fid] = values[idx]
    return cells
