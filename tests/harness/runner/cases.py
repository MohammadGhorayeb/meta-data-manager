"""Case model (Part B §7.2)."""
from __future__ import annotations

from dataclasses import dataclass

from ..contract import Adversary, Fidelity, FormatPlugin, Scrubber


@dataclass
class Case:
    format_id: str
    fidelity: Fidelity                 # "F1" | "F2" | "F3"
    adversary: Adversary               # "A1" | "A2"
    scrubber: Scrubber
    plugin: FormatPlugin
    corpus: dict                       # spec consumed by synthetic.py / inject.py
    modality: str = "bytes"            # bytes|image|audio (drives perceptual/content-identity)
    n_repeats: int = 5
