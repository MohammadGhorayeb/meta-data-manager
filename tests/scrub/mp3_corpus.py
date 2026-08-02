"""Deterministic MP3 corpus builders for the Phase-2 tests.

Base audio comes from a fixed ffmpeg lavfi tone (deterministic); tags are injected
with mutagen. F3-related builders also use the `lame` CLI, and the cross-engine A2
peer group uses `shineenc` (a fixed-point encoder that is *not* libmp3lame).
Callers should skip when the required tool is absent
(HAVE_FFMPEG / HAVE_LAME / HAVE_SHINE).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

from mutagen.apev2 import APEv2
from mutagen.id3 import APIC, COMM, ID3, TALB, TIT2, TPE1

HAVE_FFMPEG = shutil.which("ffmpeg") is not None
HAVE_LAME = shutil.which("lame") is not None
HAVE_SHINE = shutil.which("shineenc") is not None


def _run(cmd):
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {p.stderr.decode('utf-8','replace')[:200]}")


def cover_jpeg(sentinel: bytes = b"COVER-GPS-48.8584N") -> bytes:
    """A tiny stand-in cover image carrying a GPS-like secret (the nested leak)."""
    return b"\xff\xd8\xff" + sentinel + b"\xff\xd9"


def base_mp3_ffmpeg(path: str, freq: int = 440, dur: float = 2.0, quality: int = 2,
                    rate: int = 44100):
    """A VBR MP3 via ffmpeg's libmp3lame front-end (no metadata)."""
    _run(["ffmpeg", "-y", "-f", "lavfi", "-i",
          f"sine=frequency={freq}:duration={dur}", "-ac", "2", "-ar", str(rate),
          "-c:a", "libmp3lame", "-q:a", str(quality), "-map_metadata", "-1", path])


def base_mp3_lame(path: str, freq: int = 440, dur: float = 2.0, vbr: int = 2,
                  rate: int = 44100):
    """A VBR MP3 via the LAME CLI front-end (distinct producer signature)."""
    with tempfile.TemporaryDirectory() as td:
        wav = os.path.join(td, "s.wav")
        _run(["ffmpeg", "-y", "-f", "lavfi", "-i",
              f"sine=frequency={freq}:duration={dur}", "-ac", "2", "-ar", str(rate), wav])
        _run(["lame", "--quiet", "-V", str(vbr), wav, path])


def base_mp3_shine(path: str, freq: int = 440, dur: float = 2.0, bitrate: int = 128,
                   rate: int = 44100):
    """A CBR MP3 via shineenc — a genuinely different *engine* (fixed-point, not
    libmp3lame): no Xing/Info header, no LAME tag, its own psychoacoustic/bit-
    allocation behaviour. This is the cross-engine A2 peer the matrix needs."""
    with tempfile.TemporaryDirectory() as td:
        wav = os.path.join(td, "s.wav")
        _run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
              f"sine=frequency={freq}:duration={dur}", "-ac", "2", "-ar", str(rate), wav])
        _run(["shineenc", "-q", "-b", str(bitrate), wav, path])


def _add_tags(path: str, sentinel: str):
    """ID3v2 (title/artist/album/GPS-comment + GPS-carrying cover art)."""
    t = ID3()
    t.add(TIT2(encoding=3, text=f"Title-{sentinel}"))
    t.add(TPE1(encoding=3, text=f"Artist-{sentinel}"))
    t.add(TALB(encoding=3, text=f"Album-{sentinel}"))
    t.add(COMM(encoding=3, lang="eng", desc="", text=f"GPS-{sentinel}-48.85N"))
    t.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="cover",
               data=cover_jpeg(f"COVERGPS-{sentinel}".encode())))
    t.save(path)


def torture_mp3(path: str, sentinel: str = "SECRET"):
    """Every locus at once: ID3v2 (text + GPS-cover art) + APEv2 ghost + ID3v1 +
    appended hitchhiker JPEG."""
    base_mp3_ffmpeg(path)
    _add_tags(path, sentinel)
    raw = open(path, "rb").read()
    id3v1 = (b"TAG" + f"OldTitle-{sentinel}".encode().ljust(30, b"\0")
             + b"OldArtist".ljust(30, b"\0") + b"\0" * 30 + b"2020"
             + b"\0" * 30 + b"\xff")
    open(path, "wb").write(raw + id3v1)
    ape = APEv2()
    ape["Title"] = f"ApeGhost-{sentinel}"
    ape["Comment"] = f"ape-hidden-{sentinel}"
    ape.save(path)
    with open(path, "ab") as f:
        f.write(cover_jpeg(f"HITCHHIKER-{sentinel}".encode()))
    return path


def a1_variants(tmpdir: str, n_variants: int = 3, n_repeats: int = 5):
    """Same audio (one shared base), tags differing only by a per-variant sentinel.
    A correct F1 collapses them to identical bytes -> A1 pass."""
    base = os.path.join(tmpdir, "a1_base.mp3")
    base_mp3_ffmpeg(base)
    base_bytes = open(base, "rb").read()
    groups = []
    for i in range(n_variants):
        sentinel = chr(65 + i) * 6                    # AAAAAA / BBBBBB / ...
        vpath = os.path.join(tmpdir, f"a1_v{i}.mp3")
        open(vpath, "wb").write(base_bytes)
        _add_tags(vpath, sentinel)
        # ID3v1 + APEv2 sentinels too, so multiple loci correlate with the variant
        raw = open(vpath, "rb").read()
        open(vpath, "wb").write(raw + b"TAG" + sentinel.encode().ljust(125, b"\0"))
        ape = APEv2(); ape["Title"] = f"ape-{sentinel}"; ape.save(vpath)
        variant = open(vpath, "rb").read()
        paths = []
        for r in range(n_repeats):
            p = os.path.join(tmpdir, f"a1_v{i}_r{r}.mp3")
            open(p, "wb").write(variant)
            paths.append(p)
        groups.append(paths)
    return groups


def _builders(cross_engine: bool = True):
    b = [("lame_vbr", base_mp3_lame), ("ffmpeg_vbr", base_mp3_ffmpeg)]
    if cross_engine and HAVE_SHINE:
        b.append(("shine_cbr", base_mp3_shine))
    return b


def producers(tmpdir: str, repeats: int = 3, cross_engine: bool = True,
              rate: int = 44100):
    """A2 peer set at ONE sample rate: the SAME source audio via different producers.
    Two are front-ends of one engine (LAME CLI vs ffmpeg libmp3lame); `shine_cbr` is
    a different engine entirely, which is what turns the cross-engine A2 question
    from theory into a measurement. Content held constant, producer varies."""
    sources = {}
    for name, fn in _builders(cross_engine):
        paths = []
        for r in range(repeats):
            p = os.path.join(tmpdir, f"{name}__{rate}__r{r}.mp3")
            fn(p, rate=rate)
            paths.append(p)
        sources[name] = paths
    return sources


# 44.1 kHz is MPEG-1 (our canonical 192 kbps CBR is legal); 22.05 kHz is MPEG-2,
# where 192 is NOT a legal bitrate, so F3 emits 160 instead. Mixing both is what
# makes the peer set able to see the per-sample-rate anonymity grouping at all.
RATES = (44100, 22050)


def producers_mixed(tmpdir: str, repeats: int = 2, cross_engine: bool = True,
                    rates=RATES):
    """A2 peer set spanning several sample rates.

    Every producer encodes at EVERY rate, so sample rate varies *within* each
    producer group and therefore cannot act as a producer fingerprint. That is the
    honest way to test the grouping: the question is not "do scrubbed files differ?"
    (files of different sample rates obviously do — the rate is a property of the
    recording, which F3 preserves by design) but "does anything separate the
    PRODUCERS once rate is free to vary?"
    """
    sources = {name: [] for name, _ in _builders(cross_engine)}
    for rate in rates:
        for name, paths in producers(tmpdir, repeats=repeats,
                                     cross_engine=cross_engine, rate=rate).items():
            sources[name].extend(paths)
    return sources
