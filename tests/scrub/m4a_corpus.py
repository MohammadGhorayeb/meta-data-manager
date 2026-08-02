"""Deterministic M4A corpus builders for the Phase-2 tests.

Audio comes from a fixed ffmpeg lavfi signal encoded to AAC or ALAC; tags go in with
mutagen (real iTunes atoms and a real `covr` cover-art atom, so the nested-image
locus is genuine rather than a stand-in blob).

MAT2 refuses this format outright, which is why it is here: the benchmark row is a
gap to beat rather than match.
"""
from __future__ import annotations

import os
import shutil
import subprocess

from mutagen.mp4 import MP4, MP4Cover

HAVE_FFMPEG = shutil.which("ffmpeg") is not None

COVER_SENTINEL = b"COVERGPS-48.8584N"


def _run(cmd):
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {p.stderr.decode('utf-8','replace')[:300]}")


def cover_jpeg(sentinel: bytes = COVER_SENTINEL) -> bytes:
    return b"\xff\xd8\xff" + sentinel + b"\xff\xd9"


def base_m4a(path: str, freq: int = 440, dur: float = 2.0, rate: int = 44100,
             codec: str = "aac", bitrate: str = "128k", faststart: bool = False):
    """A clean M4A, no metadata.

    `faststart` moves `moov` ahead of `mdat` — a muxer choice that changes the box
    layout without changing a single audio sample, and the exact kind of structural
    tell the A2 peer set needs to vary. It also exercises the hard path in F1: with
    `moov` first, removing metadata slides the audio and every chunk offset has to be
    patched.
    """
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
           f"sine=frequency={freq}:duration={dur}", "-ac", "2", "-ar", str(rate),
           "-c:a", codec, "-map_metadata", "-1"]
    if codec == "aac":
        cmd += ["-b:a", bitrate]
    if faststart:
        cmd += ["-movflags", "+faststart"]
    _run(cmd + [path])


def _add_tags(path: str, sentinel: str):
    f = MP4(path)
    f["\xa9nam"] = [f"Title-{sentinel}"]
    f["\xa9ART"] = [f"Artist-{sentinel}"]
    f["\xa9alb"] = [f"Album-{sentinel}"]
    f["\xa9cmt"] = [f"GPS-{sentinel}-48.85N"]
    f["\xa9too"] = [f"Encoder-{sentinel}"]          # names the producing app
    f["covr"] = [MP4Cover(cover_jpeg(f"COVERGPS-{sentinel}".encode()),
                          imageformat=MP4Cover.FORMAT_JPEG)]
    f.save()


# Seconds since 1904-01-01, the ISOBMFF epoch. Fixed so the corpus is deterministic.
# 3_600_000_000 lands in 2018 — a plausible "when this file was made".
STAMP = 3_600_000_000


def stamp_timestamps(path: str, value: int = STAMP) -> str:
    """Write a real creation/modification time into mvhd/tkhd/mdhd.

    ffmpeg zeroes these, so a corpus built purely with ffmpeg never exercises the
    timestamp-stripping path — the tests would pass without the scrubber doing
    anything. Files from phones and from iTunes carry them, and they are the easiest
    locus to miss because they are structural fields rather than tags.
    """
    from src.scrub.formats.m4a import f1 as m4a_f1
    from src.scrub.standards import isobmff as iso

    data = open(path, "rb").read()
    boxes = iso.parse(data)
    for root in boxes:
        for box in root.walk():
            if box.type in m4a_f1.TIMESTAMP_BOXES:
                width = 8 if box.payload[0] == 1 else 4
                box.payload = (box.payload[:4] + value.to_bytes(width, "big") * 2
                               + box.payload[4 + width * 2:])
    open(path, "wb").write(iso.serialize(boxes))
    return path


def torture_m4a(path: str, sentinel: str = "SECRET", faststart: bool = True) -> str:
    """Every locus at once: iTunes atoms, GPS-carrying cover art, the encoder atom,
    and the creation/modification timestamps every muxer writes into mvhd/tkhd/mdhd
    (structural fields a tag-oriented scrubber leaves behind)."""
    base_m4a(path, faststart=faststart)
    _add_tags(path, sentinel)
    stamp_timestamps(path)
    return path


def a1_variants(tmpdir: str, n_variants: int = 3, n_repeats: int = 5):
    """Same audio, tags differing only by a per-variant sentinel."""
    base = os.path.join(tmpdir, "a1_base.m4a")
    base_m4a(base, faststart=True)
    base_bytes = open(base, "rb").read()
    groups = []
    for i in range(n_variants):
        sentinel = chr(65 + i) * 6
        vpath = os.path.join(tmpdir, f"a1_v{i}.m4a")
        open(vpath, "wb").write(base_bytes)
        _add_tags(vpath, sentinel)
        variant = open(vpath, "rb").read()
        paths = []
        for r in range(n_repeats):
            p = os.path.join(tmpdir, f"a1_v{i}_r{r}.m4a")
            open(p, "wb").write(variant)
            paths.append(p)
        groups.append(paths)
    return groups


def producers(tmpdir: str, repeats: int = 3):
    """A2 peer set: the same audio through different MUXER layouts.

    faststart-vs-not is a real, content-independent muxer choice (where `moov` sits),
    and bitrate changes the coded stream. Both are producer traits; content is held
    constant across them.
    """
    specs = {
        "mux_plain": dict(faststart=False, bitrate="128k"),
        "mux_faststart": dict(faststart=True, bitrate="128k"),
        "mux_faststart_192": dict(faststart=True, bitrate="192k"),
    }
    sources = {}
    for name, kw in specs.items():
        paths = []
        for r in range(repeats):
            p = os.path.join(tmpdir, f"{name}__r{r}.m4a")
            base_m4a(p, **kw)
            paths.append(p)
        sources[name] = paths
    return sources
