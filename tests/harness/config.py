"""Harness-wide defaults and paths.

Phase 0 differential-testing harness. See README.md and the Part A/Part B
design docs. Nothing here is format-specific.
"""
from __future__ import annotations

from pathlib import Path

# --- engine defaults ---
N_REPEATS = 5          # self-determinism / floor repeats per (format, fidelity, scrubber)
MIN_SIG_LEN = 4        # shortest byte run the fingerprint guard treats as a signature
HEX_CONTEXT = 16       # bytes of context on each side of a byte-space diff

# --- corpus defaults ---
N_VARIANTS = 3         # A1 needs >=3 variants so correlation separates from the floor
N_SOURCES = 2          # A2 minimum: a 2-source peer set
MEMBERS_PER_SOURCE = 3

# --- versioning ---
HARNESS_VERSION = "0.1.0"

# --- paths (all under tests/harness/) ---
HARNESS_ROOT = Path(__file__).resolve().parent
SCHEMA_PATH = HARNESS_ROOT / "schemas" / "pareto_matrix.schema.json"
RESULTS_DIR = HARNESS_ROOT / "results"
EVIDENCE_DIR = RESULTS_DIR / "evidence"

# Real-seed originals live outside the repo and are absent in CI / this env.
# Guard every use with an existence check + pytest.skip.
REAL_SEED_DIR = Path.home() / "metadata-research" / "step2" / "originals"
