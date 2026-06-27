"""Synthetic content-identical / metadata-variant corpora (Part B §6.1).

Content-identity is a theorem here, not a measurement: the TOYF codec writes
byte-identical content into every member; only the metadata region varies.

A1 variants carry a known sentinel per variant (AAAA.. / BBBB.. / CCCC..) so a
variant-correlated diff is decidable and self-localizing (Part A §4).
A2 sources carry a per-source structural marker plus per-member incidental
metadata, content held identical across all members and sources (Part A §7).
"""
from __future__ import annotations

import os

from . import toy_format

# A1: the sentinel lives in this TLV record type.
A1_SENTINEL_TYPE = 7
A1_SENTINEL_FIELD = f"record_type_{A1_SENTINEL_TYPE}"
# A2: per-source marker vs per-member incidental record types.
A2_MARKER_TYPE = 0xA0
A2_INCIDENTAL_TYPE = 0x10


def _sentinel(i: int, length: int = 16) -> bytes:
    return bytes([65 + i]) * length


def make_a1_variants(content: bytes, n_variants: int = 3, n_repeats: int = 5,
                     tmpdir: str | None = None,
                     record_type: int = A1_SENTINEL_TYPE) -> list[list[str]]:
    """Returns variant-groups of repeat-input paths. Every file shares identical
    `content`; variant i carries one record (record_type, sentinel_i). The same
    bytes are written n_repeats times so the runner can scrub each independently
    and measure the floor."""
    if tmpdir is None:
        raise ValueError("tmpdir is required")
    groups: list[list[str]] = []
    for i in range(n_variants):
        rec = (record_type, _sentinel(i))
        paths: list[str] = []
        for r in range(n_repeats):
            p = os.path.join(tmpdir, f"a1_v{i}_r{r}.toyf")
            toy_format.write(p, content, [rec])
            paths.append(p)
        groups.append(paths)
    _assert_content_identity([p for g in groups for p in g], content)
    return groups


def make_a2_corpus(content: bytes, n_sources: int = 2, members_per_source: int = 3,
                   n_repeats: int = 5, tmpdir: str | None = None) -> dict[str, list[str]]:
    """Returns source_id -> member-input paths. Content is byte-identical across
    all members and all sources; source s carries marker (0xA0, b"SRC{s}") plus a
    per-member incidental record (0x10, random). The members are the within-source
    axis the variance engine treats as the floor (Part A §7)."""
    if tmpdir is None:
        raise ValueError("tmpdir is required")
    sources: dict[str, list[str]] = {}
    for s in range(n_sources):
        sid = f"src{s}"
        marker = (A2_MARKER_TYPE, f"SRC{s}".encode())
        paths: list[str] = []
        for m in range(members_per_source):
            incidental = (A2_INCIDENTAL_TYPE, os.urandom(8))  # per-member, distinct
            p = os.path.join(tmpdir, f"a2_{sid}_m{m}.toyf")
            toy_format.write(p, content, [marker, incidental])
            paths.append(p)
        sources[sid] = paths
    _assert_content_identity([p for ps in sources.values() for p in ps], content)
    return sources


def _assert_content_identity(paths: list[str], content: bytes) -> None:
    # decode + compare -- the self-check the spec mandates for every generator.
    for p in paths:
        c, _ = toy_format.read(p)
        if c != content:
            raise AssertionError(f"content drift in synthetic corpus: {p}")
