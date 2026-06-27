"""Ground-truth field + structural feature extraction (Part A §5; Part B §5.8).

Two channels feed the F3 feature vector that the §5.1 variance engine consumes:

  * ExifTool fields  -- ExifTool is the ground-truth metadata extractor. Run as a
    subprocess (license-clean: never linked), parse its JSON, flatten to
    ``Group:Tag -> str(value)``.
  * Structural features -- generic, format-agnostic facts (file size, and, when a
    real plugin offers it later, a segment/atom/chunk inventory). This is the
    channel that makes "all duplicate loci" real for F3 rather than "all named
    tags" (Part A §5 (b)).

``extract`` merges the two into a single ``FeatureMap`` (prefixed ``field:`` /
``struct:``) so the engine treats them uniformly.

Phase-0 boundary: no real-format parsing here. ExifTool is an external subprocess
oracle only; the structural channel is generic (size) plus whatever hooks the
plugin chooses to expose. ToyFormatPlugin exposes none, so Phase 0 yields just
``struct:size``.
"""
from __future__ import annotations

import json
import os
import subprocess
from typing import Hashable

from .variance import FeatureMap


def exiftool_fields(path: str) -> dict[str, str]:
    """Extract all metadata fields via ExifTool, flattened to ``Group:Tag -> str``.

    Invokes ``exiftool -j -G -n -a -u <path>``:
      -j  JSON output
      -G  group names (so keys are ``Group:Tag``)
      -n  numeric values (no human-readable conversions -> stable tokens)
      -a  allow duplicate tags
      -u  extract unknown/unsupported tags too

    ExifTool is the ground-truth extractor (Part A §5). Returns ``{}`` gracefully
    if ExifTool is missing, errors, or yields nothing -- never raises.
    """
    try:
        proc = subprocess.run(
            ["exiftool", "-j", "-G", "-n", "-a", "-u", path],
            capture_output=True,
        )
    except (FileNotFoundError, OSError):
        return {}
    if proc.returncode != 0 or not proc.stdout:
        return {}
    try:
        data = json.loads(proc.stdout.decode("utf-8", "replace"))
    except (json.JSONDecodeError, ValueError):
        return {}
    if not data or not isinstance(data, list):
        return {}

    record = data[0]
    if not isinstance(record, dict):
        return {}

    fields: dict[str, str] = {}
    for key, value in record.items():
        # ExifTool injects a "SourceFile" housekeeping key (the input path); drop
        # it so corpus features do not vary on tmpfile names.
        if key == "SourceFile":
            continue
        fields[key] = str(value)
    return fields


def structural_features(path: str, plugin) -> dict[str, Hashable]:
    """Generic, format-agnostic structural facts about ``path``.

    Phase 0 returns ``{"size": <file size in bytes>}`` plus whatever the plugin
    chooses to expose via an optional ``structural_features(path)`` hook. The
    Phase-0 plugins (Generic, ToyFormat) do not offer that hook, so only ``size``
    is returned. Real plugins enrich this later with a segment/atom/chunk
    inventory: ordered ``(type, length)`` pairs, padding sizes, per-type counts.
    """
    feats: dict[str, Hashable] = {"size": os.path.getsize(path)}

    # Forward-looking: a real plugin may expose extra structural hooks. Only call
    # it if it actually offers one (Generic/ToyFormat do not).
    hook = getattr(plugin, "structural_features", None)
    if callable(hook):
        try:
            extra = hook(path)
        except NotImplementedError:
            extra = None
        if extra:
            for key, value in extra.items():
                feats[str(key)] = value
    return feats


def extract(path: str, plugin, fidelity: str) -> FeatureMap:
    """Build the feature map for ``path`` at the given ``fidelity``.

    F3  => merge ``exiftool_fields`` (keys prefixed ``field:``) with
           ``structural_features`` (keys prefixed ``struct:``). This is the full
           F3 feature vector the §5.1 engine decomposes.

    F1/F2 => byte features come from ``oracle.diff`` elsewhere; ``extract`` still
           contributes the ``struct:``/``field:`` channels as an additional axis
           (a redundant copy in any locus is then caught by either channel).

    All values are kept Hashable (str / int / bytes) so they slot straight into
    the variance grid.
    """
    feats: FeatureMap = {}
    for key, value in structural_features(path, plugin).items():
        feats[f"struct:{key}"] = value
    for key, value in exiftool_fields(path).items():
        feats[f"field:{key}"] = value
    return feats
