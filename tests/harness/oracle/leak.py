"""A1 verdict: variance over (variant x repeat) (Part B §5.4).

Lay out a grid indexed by (variant, repeat). For each feature position the
within-variant (repeat) variation is the floor; any feature that separates
variants beyond that floor is a metadata leak. Because content is byte-identical
across variants by construction, every diff is already content-independent --
the only question is variant-correlation vs floor.
"""
from __future__ import annotations

import os
import tempfile

from .. import config
from ..contract import Cell, Leak, V
from . import variance, diff, floor as floor_mod


def _scrub(scrubber, in_path, out_path, fidelity):
    scrubber.run(in_path, out_path, fidelity)
    with open(out_path, "rb") as f:
        return f.read()


def _hexify(v) -> str:
    if isinstance(v, (bytes, bytearray)):
        return bytes(v).hex()
    return repr(v)


def _evidence(cells, fid, groups, repeats) -> str:
    # one representative value per group, for the evidence pointer
    parts = []
    for g in groups:
        for r in repeats:
            if (g, r) in cells and fid in cells[(g, r)]:
                parts.append(f"{g}={_hexify(cells[(g, r)][fid])}")
                break
    return "; ".join(parts)


def evaluate_a1(scrubber, plugin, variants: list[list[str]], fidelity: str,
                n: int = config.N_REPEATS, sentinel_field: str | None = None,
                modality: str = "bytes", baseline_path: str | None = None,
                perceptual_threshold: float | None = None) -> Cell:
    variant_ids = [f"v{i}" for i in range(len(variants))]
    nn = min(n, min(len(g) for g in variants))
    repeats = list(range(nn))
    tmpdir = tempfile.mkdtemp(prefix="a1_")

    # floor: scrub one content-identical input n times (within-group axis).
    floor_report, _ = floor_mod.measure(scrubber, plugin, variants[0][0], fidelity, n=nn)

    use_fields = (fidelity == "F3")
    if use_fields:
        from . import fields
    grid_blobs: dict[tuple[str, int], bytes] = {}
    grid_paths: dict[tuple[str, int], str] = {}
    grid_maps: dict[tuple[str, int], dict] = {}
    for vi, vid in enumerate(variant_ids):
        for r in repeats:
            op = os.path.join(tmpdir, f"{vid}_r{r}")
            b = _scrub(scrubber, variants[vi][r], op, fidelity)
            grid_paths[(vid, r)] = op
            if use_fields:
                grid_maps[(vid, r)] = fields.extract(op, plugin, fidelity)
            else:
                grid_blobs[(vid, r)] = b

    cells = grid_maps if use_fields else diff.to_feature_maps(grid_blobs)
    verdicts = variance.decompose(cells, variant_ids, repeats)

    rep_blob = next(iter(grid_blobs.values())) if grid_blobs else None
    rep_path = grid_paths[(variant_ids[0], 0)]

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
        if sentinel_field:
            correlates = f"metadata_variant:{sentinel_field}"
        else:
            correlates = f"metadata_variant:{loc.annotated_as or fid}"
        leaks.append(Leak(kind="variant_correlated", locus=loc,
                          correlates_with=correlates,
                          evidence=_evidence(cells, fid, variant_ids, repeats)))

    # F3 only: content-preservation is an independent hard constraint.
    perc = None
    perc_ok = True
    if fidelity == "F3" and modality in ("image", "audio", "video"):
        from . import perceptual as perceptual_mod
        base = baseline_path or variants[0][0]
        perc = perceptual_mod.check_preserved(modality, base, rep_path, perceptual_threshold)
        perc_ok = bool(perc.preserved)

    if not perc_ok:
        verdict, reason = V.FAIL, "content_not_preserved"
    elif leaks:
        verdict, reason = V.FAIL, "variant_correlated_leak"
    else:
        verdict, reason = V.PASS, None

    return Cell(adversary="A1", fidelity=fidelity, verdict=verdict,
                floor=floor_report, leaks=leaks, perceptual=perc, reason=reason)
