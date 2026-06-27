"""Scrubber-fingerprint guard (Part A §8).

Detects tool signatures: byte runs the scrubber introduces *regardless of input*,
that aren't echoed content and aren't format-mandated. Position-independent (a
trailing tag shifts with content length), so it uses common substrings rather
than offset alignment. Grouping factor = input (everything varies).
"""
from __future__ import annotations


def _substrings_geq(s: bytes, k: int) -> set[bytes]:
    out = set()
    for i in range(len(s) - k + 1):
        for j in range(i + k, len(s) + 1):
            out.add(s[i:j])
    return out  # fixture-scale only; for large real outputs swap in a suffix-automaton LCS


def common_substrings(blobs: list[bytes], min_len: int) -> set[bytes]:
    if not blobs: return set()
    base = min(blobs, key=len)
    cand = _substrings_geq(base, min_len)
    for b in blobs:
        cand = {s for s in cand if s in b}
        if not cand: break
    return cand


def maximal(runs: set[bytes]) -> list[bytes]:
    # drop any run that is a substring of another kept run
    srt = sorted(runs, key=len, reverse=True)
    keep = []
    for r in srt:
        if not any(r in k for k in keep): keep.append(r)
    return keep


def evaluate(scrubber, plugin, inputs: list[str], fidelity,
             min_len: int = 4) -> tuple[str, list[dict]]:
    import tempfile, os
    outs, in_bytes = [], []
    for ip in inputs:
        with open(ip, "rb") as f: in_bytes.append(f.read())
        op = tempfile.mktemp()
        scrubber.run(ip, op, fidelity)
        with open(op, "rb") as f: outs.append(f.read())
        os.unlink(op)
    common = common_substrings(outs, min_len)
    introduced = {s for s in common if all(s not in ib for ib in in_bytes)}  # not echoed content
    mand = set()
    for c in plugin.mandatory_constants():
        for L in range(min_len, len(c) + 1):
            for i in range(len(c) - L + 1): mand.add(c[i:i + L])
    sigs = maximal(introduced - mand)
    verdict = "fail" if sigs else "pass"
    detail = [{"space": "byte", "bytes_hex": s.hex(),
               "decoded": s.decode("latin-1", "replace")} for s in sigs]
    return verdict, detail
