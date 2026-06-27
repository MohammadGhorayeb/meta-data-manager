"""Contracts & data models for the Phase 0 harness.

Two protocols, deliberately separate (Part A §2):
  - Scrubber     -- the thing under test (black box; subprocess or Python stub).
  - FormatPlugin -- harness-side format knowledge used to verify.

Plus the dataclasses the engine and matrix exchange.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Protocol, runtime_checkable

Fidelity = str   # "F1" | "F2" | "F3"
Adversary = str  # "A1" | "A2"  (A3 never a target)


class V(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"
    NOT_TESTED = "not_tested"


@dataclass
class ScrubResult:
    ok: bool
    out_path: str
    fidelity: Fidelity
    returncode: int = 0
    stderr: str = ""
    duration_s: float = 0.0


@runtime_checkable
class Scrubber(Protocol):
    """The thing under test. Black box. May be a subprocess or a Python stub."""
    name: str
    version: str
    def run(self, in_path: str, out_path: str, fidelity: Fidelity) -> ScrubResult: ...


@runtime_checkable
class FormatPlugin(Protocol):
    """Harness-side format knowledge. NOT the scrubber. Real impls arrive in later phases."""
    format_id: str
    def matches(self, header: bytes, path: str) -> bool: ...
    # forward-looking hooks; Phase 0 generic impls return None / [] / raise NotImplemented
    def annotate(self, in_path: str, offset: int) -> Optional[str]: ...
    def canonical_content(self, path: str) -> bytes: ...          # for content-identity in feature space
    def mandatory_constants(self) -> list[bytes]: ...             # excluded from the fingerprint guard


# --- feature & verdict structures used by the engine ---
@dataclass
class Locus:
    space: str                 # "byte" | "field" | "structural"
    offset: Optional[int] = None
    length: Optional[int] = None
    feature_id: Optional[str] = None
    annotated_as: Optional[str] = None
    hex_context: Optional[str] = None


@dataclass
class Leak:
    kind: str                  # "variant_correlated" (A1) | "source_fingerprint" (A2)
    locus: Locus
    correlates_with: str       # e.g. "metadata_variant:record_type_7" or "source:cam_B"
    evidence: Optional[str] = None


@dataclass
class FloorReport:
    deterministic: bool
    repeats: int
    variable_loci: list[Locus] = field(default_factory=list)


@dataclass
class PerceptualReport:
    checked: bool = False
    metric: Optional[str] = None
    distance: Optional[float] = None
    threshold: Optional[float] = None
    preserved: Optional[bool] = None


@dataclass
class Cell:
    adversary: Adversary
    fidelity: Fidelity
    verdict: V
    floor: Optional[FloorReport] = None
    leaks: list[Leak] = field(default_factory=list)
    perceptual: Optional[PerceptualReport] = None
    reason: Optional[str] = None
    evidence_dir: Optional[str] = None


class SubprocessScrubber:
    """Wraps a real black-box tool. cmd_template uses {in} {out} {fidelity}."""

    def __init__(self, name: str, version: str, cmd_template: str):
        self.name, self.version, self.tmpl = name, version, cmd_template

    def run(self, in_path: str, out_path: str, fidelity: Fidelity) -> ScrubResult:
        cmd = [a.format(**{"in": in_path, "out": out_path, "fidelity": fidelity})
               for a in shlex.split(self.tmpl)]
        t0 = time.time()
        p = subprocess.run(cmd, capture_output=True)
        return ScrubResult(
            ok=(p.returncode == 0 and os.path.exists(out_path)),
            out_path=out_path, fidelity=fidelity,
            returncode=p.returncode,
            stderr=p.stderr.decode("utf-8", "replace"),
            duration_s=time.time() - t0,
        )
