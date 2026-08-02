"""Deterministic FLAC corpus builders for the Phase-2 tests.

Base audio comes from a fixed ffmpeg lavfi signal; tags go in with mutagen, which
writes real Vorbis comments and real PICTURE blocks (so the cover-art locus is a
genuine embedded image, not a stand-in blob).

`flac`/`metaflac` are NOT required: ffmpeg has a native FLAC encoder, so the whole
corpus and the F2 tier need only the tools the project already depends on.
"""
from __future__ import annotations

import os
import shutil
import subprocess

from mutagen.flac import FLAC, Picture

HAVE_FFMPEG = shutil.which("ffmpeg") is not None

# A tiny stand-in cover image carrying a GPS-like secret: cover art is a nested
# metadata container (real art carries its own EXIF/GPS), so the corpus must prove
# the scrub reaches inside it rather than just deleting named tags.
COVER_SENTINEL = b"COVERGPS-48.8584N"


def _run(cmd):
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {p.stderr.decode('utf-8','replace')[:300]}")


def cover_jpeg(sentinel: bytes = COVER_SENTINEL) -> bytes:
    return b"\xff\xd8\xff" + sentinel + b"\xff\xd9"


def base_flac(path: str, freq: int = 440, dur: float = 2.0, rate: int = 44100,
              level: int = 5):
    """A clean FLAC via ffmpeg's native encoder, no metadata.

    `level` is the compression level — it changes the encoder's structural choices
    (block size, residual partitioning), which is exactly the A2 producer signal the
    peer set needs to vary.
    """
    _run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
          f"sine=frequency={freq}:duration={dur}", "-ac", "2", "-ar", str(rate),
          "-c:a", "flac", "-compression_level", str(level),
          "-map_metadata", "-1", path])


def _add_tags(path: str, sentinel: str):
    f = FLAC(path)
    f["title"] = f"Title-{sentinel}"
    f["artist"] = f"Artist-{sentinel}"
    f["album"] = f"Album-{sentinel}"
    f["comment"] = f"GPS-{sentinel}-48.85N"
    pic = Picture()
    pic.type = 3
    pic.mime = "image/jpeg"
    pic.desc = "cover"
    pic.data = cover_jpeg(f"COVERGPS-{sentinel}".encode())
    f.add_picture(pic)
    f.save()


def torture_flac(path: str, sentinel: str = "SECRET") -> str:
    """Every locus at once: Vorbis comments, a GPS-carrying PICTURE block, an
    APPLICATION block, oversized padding, and a leading ID3v2 tag (out of spec for
    FLAC, but taggers write it and a scrubber that starts at 'fLaC' never sees it)."""
    base_flac(path)
    _add_tags(path, sentinel)

    raw = open(path, "rb").read()
    # APPLICATION block (type 2) with a third-party payload, spliced in after
    # STREAMINFO -- mutagen has no API for arbitrary blocks, so this is hand-built.
    from src.scrub.formats.flac import walker as w
    layout = w.walk(raw)
    payload = b"MOEX" + f"app-hidden-{sentinel}".encode()
    block = bytes([w.APPLICATION]) + len(payload).to_bytes(3, "big") + payload
    after_streaminfo = layout.blocks[0].offset + layout.blocks[0].total
    raw = raw[:after_streaminfo] + block + raw[after_streaminfo:]

    id3 = b"ID3\x03\x00\x00" + bytes([0, 0, 0, 40]) + b"TIT2" + b"\x00" * 36
    open(path, "wb").write(id3 + raw)
    return path


def a1_variants(tmpdir: str, n_variants: int = 3, n_repeats: int = 5):
    """Same audio, tags differing only by a per-variant sentinel. A correct F1
    collapses them to identical bytes -> A1 pass."""
    base = os.path.join(tmpdir, "a1_base.flac")
    base_flac(base)
    base_bytes = open(base, "rb").read()
    groups = []
    for i in range(n_variants):
        sentinel = chr(65 + i) * 6
        vpath = os.path.join(tmpdir, f"a1_v{i}.flac")
        open(vpath, "wb").write(base_bytes)
        _add_tags(vpath, sentinel)
        variant = open(vpath, "rb").read()
        paths = []
        for r in range(n_repeats):
            p = os.path.join(tmpdir, f"a1_v{i}_r{r}.flac")
            open(p, "wb").write(variant)
            paths.append(p)
        groups.append(paths)
    return groups


def producers(tmpdir: str, repeats: int = 3, rate: int = 44100):
    """A2 peer set: the SAME audio encoded with different compression levels.

    Level is the honest stand-in for "different encoder" in FLAC: it drives block
    size and residual partitioning, which is what a peer-corpus adversary reads.
    Content is held constant, producer varies.
    """
    sources = {}
    for name, level in (("flac_l0", 0), ("flac_l5", 5), ("flac_l8", 8)):
        paths = []
        for r in range(repeats):
            p = os.path.join(tmpdir, f"{name}__{rate}__r{r}.flac")
            base_flac(p, rate=rate, level=level)
            paths.append(p)
        sources[name] = paths
    return sources
