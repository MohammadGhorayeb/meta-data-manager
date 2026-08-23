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
from typing import Protocol, runtime_checkable

Fidelity = str   # "F1" | "F2" | "F3"
Adversary = str  # "A1" | "A2"  (A3 never a target)


class V(str, Enum):  # noqa: UP042 — see below
    # Deliberately (str, Enum) and NOT StrEnum: these values are serialised into
    # the schema-validated Pareto matrices, and the two differ in what str()
    # yields ("V.PASS" vs "pass"). Switching would be a silent output change
    # dressed up as a modernisation.
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
    def annotate(self, in_path: str, offset: int) -> str | None: ...
    def canonical_content(self, path: str) -> bytes: ...          # for content-identity in feature space
    def mandatory_constants(self) -> list[bytes]: ...             # excluded from the fingerprint guard


# --- feature & verdict structures used by the engine ---
@dataclass
class Locus:
    # "byte" | "field" | "structural" | "pixel" | "signal"
    #
    # The last two are content-domain: the leak is in the decoded artifact rather
    # than anywhere in the file's construction. PDF F3 is the case that forced the
    # distinction — rasterising closes every structural channel and the typesetter is
    # still identifiable from the rendered page, so recording that as "structural"
    # would have filed a pixel-domain residual under the very space the tier closed.
    space: str
    offset: int | None = None
    length: int | None = None
    feature_id: str | None = None
    annotated_as: str | None = None
    hex_context: str | None = None


@dataclass
class Leak:
    kind: str                  # "variant_correlated" (A1) | "source_fingerprint" (A2)
    locus: Locus
    correlates_with: str       # e.g. "metadata_variant:record_type_7" or "source:cam_B"
    evidence: str | None = None


@dataclass
class FloorReport:
    deterministic: bool
    repeats: int
    variable_loci: list[Locus] = field(default_factory=list)


@dataclass
class PerceptualReport:
    checked: bool = False
    metric: str | None = None
    distance: float | None = None
    threshold: float | None = None
    preserved: bool | None = None


@dataclass
class Cell:
    adversary: Adversary
    fidelity: Fidelity
    verdict: V
    floor: FloorReport | None = None
    leaks: list[Leak] = field(default_factory=list)
    perceptual: PerceptualReport | None = None
    reason: str | None = None
    evidence_dir: str | None = None


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
