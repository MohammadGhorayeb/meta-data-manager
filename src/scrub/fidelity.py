"""Fidelity tiers (CLAUDE.md / framework.md).

  F1  bit-preserving      content bitstream byte-identical (JPEG entropy bytes /
                          PNG pixel semantics untouched); only metadata regions
                          removed or normalized.
  F2  lossless re-encode  content re-encoded without loss (JPEG re-Huffman with
                          quantized coefficients intact; PNG deflate re-pack);
                          decoded pixels bit-identical to input.
  F3  lossy re-encode     content decoded and re-encoded lossily (JPEG canonical
                          re-encode); perceptual identity only.

Strings match the harness contract (`Fidelity = str`, "F1"|"F2"|"F3").
"""
from __future__ import annotations

from .errors import FidelityError

F1 = "F1"
F2 = "F2"
F3 = "F3"
ALL = (F1, F2, F3)


def validate(fidelity: str) -> str:
    if fidelity not in ALL:
        raise FidelityError(f"unknown fidelity {fidelity!r}; expected one of {ALL}")
    return fidelity
