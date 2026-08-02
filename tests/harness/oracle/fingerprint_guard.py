"""Scrubber-fingerprint guard (Part A §8).

Detects tool signatures: byte runs the scrubber introduces *regardless of input*,
that aren't echoed content and aren't format-mandated. Position-independent (a
trailing tag shifts with content length), so it uses common substrings rather
than offset alignment. Grouping factor = input (everything varies).
"""
from __future__ import annotations

# Longest run the extension loop will chase. Extension costs a substring search per
# byte per blob, so an unbounded run of one repeated byte -- zero padding, or a
# silent passage in a real audio file -- turns the guard into a hang. Well past any
# plausible tool signature (a producer string, a padding block header), so bounding
# it costs no detection in practice; when it does bite, the count is reported.
MAX_RUN = 4096


def common_substrings(blobs: list[bytes], min_len: int) -> set[bytes]:
    """Maximal byte runs (length >= min_len) present in EVERY blob.

    Seed-and-extend, driven by the shortest blob: for each position whose
    min_len window occurs in all blobs, extend right while the growing run still
    occurs in all blobs, and record the maximal run. This replaces the former
    O(n^2)-substring materialization (which OOM'd on non-fixture inputs) with a
    per-position scan, so the guard can run on richer, larger outputs while
    reporting the same maximal signatures the callers expect."""
    if not blobs:
        return set()
    base = min(blobs, key=len)
    n = len(base)

    def in_all(sub: bytes) -> bool:
        return all(sub in b for b in blobs)

    out: set[bytes] = set()
    i = 0
    truncated = 0
    while i <= n - min_len:
        if in_all(base[i:i + min_len]):
            end = i + min_len
            # MAX_RUN bounds the byte-at-a-time extension. Without it, a long run of
            # one repeated byte -- zero padding, or simply a silent passage in real
            # audio -- costs a full substring search per byte per blob and the guard
            # hangs rather than finishing. A run this long is already reported (it is
            # recorded below and is far past min_len, so callers still see the
            # signature); only its exact maximal length is cut short, and that is
            # counted and surfaced instead of being hidden.
            while end < n and end - i < MAX_RUN and in_all(base[i:end + 1]):
                end += 1
            if end - i >= MAX_RUN:
                truncated += 1
            out.add(base[i:end])
            # Skip past a run of one repeated byte: every start inside it yields a
            # window of the same byte, so re-scanning them re-derives runs already
            # recorded here. Only applied to constant runs, where it is exact.
            if len(set(base[i:end])) == 1:
                i = end - min_len + 1
                continue
        i += 1
    # Recorded on the function so a caller can report that a run was cut short
    # rather than discovering a silently shortened signature.
    common_substrings.truncated_runs = truncated       # type: ignore[attr-defined]
    return out


def maximal(runs: set[bytes]) -> list[bytes]:
    # drop any run that is a substring of another kept run
    srt = sorted(runs, key=len, reverse=True)
    keep = []
    for r in srt:
        if not any(r in k for k in keep): keep.append(r)
    return keep


def evaluate(scrubber, plugin, inputs: list[str], fidelity,
             min_len: int = 4) -> tuple[str, list[dict]]:
    import os
    import tempfile
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
