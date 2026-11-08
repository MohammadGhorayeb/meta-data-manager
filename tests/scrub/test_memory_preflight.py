"""The memory preflight (limit #38): refuse a file the machine cannot hold, up front.

Two things are worth pinning. The check must refuse BEFORE any scrubbing, with the
numbers in the message, and never leave an output behind. And the factors it relies
on are measurements, so a test re-measures them: a declared factor below the real
peak would wave through files that then exhaust memory, and one far above it would
refuse files the machine handles fine, which is the failure the user chose this
check to avoid.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from src.scrub import cli, resources  # noqa: E402
from src.scrub.dispatch import default_dispatcher  # noqa: E402
from src.scrub.errors import ResourceError  # noqa: E402
from src.scrub.formats.m4a.handler import M4aHandler  # noqa: E402
from src.scrub.formats.mp4.handler import Mp4Handler  # noqa: E402
from tests.scrub import mp4_iso_corpus as c  # noqa: E402

AUDIO_ONLY = ({"track_id": 1, "handler": b"soun", "name": "SoundHandler",
               "fmt": b"mp4a"},)


def _write(tmp_path, name: str, data: bytes) -> str:
    path = str(tmp_path / name)
    with open(path, "wb") as f:
        f.write(data)
    return path


def test_a_file_the_machine_cannot_hold_is_refused_before_any_scrub(tmp_path,
                                                                     monkeypatch):
    src = _write(tmp_path, "v.mp4", c.handbuilt())
    out = str(tmp_path / "out.mp4")
    # One byte: a streamed video needs little (STREAM_MEMORY_FACTOR), and the
    # handbuilt clip is small, so 1000 bytes no longer refuses it.
    monkeypatch.setattr(resources, "available_memory", lambda: 1)

    def never(*_a, **_k):
        raise AssertionError("the scrub ran although the preflight should refuse")
    monkeypatch.setattr(Mp4Handler, "scrub_f1", never)
    monkeypatch.setattr(Mp4Handler, "scrub_to", never)

    with pytest.raises(ResourceError) as exc:
        cli.scrub_file(src, out, "F1")
    message = str(exc.value)
    assert "needs about" in message and "available" in message
    assert "--skip-memory-check" in message
    assert not os.path.exists(out)


def test_the_cli_exits_7_and_the_override_flag_scrubs_anyway(tmp_path, monkeypatch):
    src = _write(tmp_path, "v.mp4", c.handbuilt())
    out = str(tmp_path / "out.mp4")
    monkeypatch.setattr(resources, "available_memory", lambda: 1)
    assert cli.main([src, out, "--no-report"]) == 7
    assert not os.path.exists(out)
    assert cli.main([src, out, "--no-report", "--skip-memory-check"]) == 0
    assert os.path.exists(out)


def test_unknown_available_memory_does_not_block_a_scrub(tmp_path, monkeypatch):
    """The check guards against a predictable failure, not a leak: a platform we
    cannot measure must still be able to scrub."""
    src = _write(tmp_path, "v.mp4", c.handbuilt())
    monkeypatch.setattr(resources, "available_memory", lambda: None)
    cli.scrub_file(src, str(tmp_path / "out.mp4"), "F1")


def test_a_video_is_priced_as_a_video_not_as_the_audio_that_shares_its_prefix(
        tmp_path):
    """Every ISOBMFF file starts `....ftyp`, so the prefix alone would price a video
    at M4A's larger factor and refuse the very files this check exists for."""
    d = default_dispatcher()
    video = _write(tmp_path, "v.mp4", c.handbuilt())
    audio = _write(tmp_path, "a.m4a", c.handbuilt(tracks=AUDIO_ONLY))
    assert d.memory_factor(open(video, "rb").read(16)) == M4aHandler.memory_factor
    assert resources.factor_for(video, d) == Mp4Handler.memory_factor
    assert resources.factor_for(audio, d) == M4aHandler.memory_factor


def test_formats_that_declare_no_factor_are_never_checked(tmp_path):
    from tests.scrub import corpus as imgc
    jpeg = _write(tmp_path, "p.jpg", imgc.build_torture_jpeg())
    assert resources.factor_for(jpeg, default_dispatcher()) is None


def test_identification_reads_the_index_and_never_the_media(tmp_path):
    data = c.handbuilt(moov_first=True)
    assert c.SENTINEL in data
    skeleton = resources._isobmff_skeleton(_write(tmp_path, "v.mp4", data))
    assert skeleton and b"moov" in skeleton
    assert c.SENTINEL not in skeleton, "the preflight read the media"


# On Linux `ru_maxrss` cannot be used: the kernel folds the PARENT's high-water mark
# into it across fork+exec, so a child of a large pytest process starts at pytest's
# peak and a 100 MB scrub never moves it (measured in a container: 413 -> 413 MB,
# while the process's own VmHWM went 7 -> 108). The first CI run read 0.00x from
# exactly that. VmHWM is the peak of this process's own address space.
_PEAK = r"""
import os, resource, sys
sys.path.insert(0, os.environ["REPO"])
from src.scrub import cli
from src.scrub.dispatch import default_dispatcher

def peak():
    if sys.platform.startswith("linux"):
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) * 1024
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss     # bytes on macOS

d = default_dispatcher()
base = peak()
# The IN-MEMORY route (`handler.scrub`), which `memory_factor` prices: video inside
# an archive still takes it. The CLI streams a video file (STREAM below).
data = open(sys.argv[1], "rb").read()
out = d.resolve(data).scrub(data, "F1")
open(sys.argv[2], "wb").write(out)
print(peak() - base)
"""

# What a STREAMED clean cannot give back: macOS's physical footprint, or on Linux the
# anonymous resident memory, sampled (there is no high-water mark for it alone).
_STREAM = r"""
import ctypes, os, sys, threading, time
sys.path.insert(0, os.environ["REPO"])
from src.scrub import cli
from src.scrub.dispatch import default_dispatcher

def anon():
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("RssAnon:"):
                return int(line.split()[1]) * 1024
    return 0

if sys.platform == "darwin":
    class RU(ctypes.Structure):
        _fields_ = [("uuid", ctypes.c_uint8 * 16)] + [(f"f{i}", ctypes.c_uint64)
                                                      for i in range(37)]
    lib = ctypes.CDLL("/usr/lib/libproc.dylib")
    def peak():
        ru = RU()
        lib.proc_pid_rusage(os.getpid(), 4, ctypes.byref(ru))
        return ru.f28                     # lifetime_max_phys_footprint
    stop = None
else:
    top = [0]
    stop = threading.Event()
    def sample():
        while not stop.is_set():
            top[0] = max(top[0], anon())
            time.sleep(0.002)
    def peak():
        return max(top[0], anon())

default_dispatcher()
base = peak()
if stop is not None:
    threading.Thread(target=sample, daemon=True).start()
cli.scrub_file(sys.argv[1], sys.argv[2], "F1", check_memory=False)
if stop is not None:
    stop.set()
print(peak() - base)
"""


@pytest.mark.parametrize("label,handler,tracks", [
    ("video", Mp4Handler, None),
    ("audio", M4aHandler, AUDIO_ONLY),
])
def test_the_declared_factor_is_what_a_large_scrub_actually_uses(label, handler,
                                                                  tracks, tmp_path):
    """Measured in a fresh process, since peak RSS is a high-water mark: the peak
    over the interpreter's baseline, divided by the file size, must sit at or under
    the declared factor -- and not so far under that the check refuses files the
    machine could hold."""
    media = bytes(range(256)) * (40 * 4096)                  # 40 MB of media
    kwargs = {"media": media}
    if tracks:
        kwargs["tracks"] = tracks
    src = _write(tmp_path, f"big_{label}.mp4", c.handbuilt(**kwargs))
    out = str(tmp_path / f"out_{label}.mp4")
    r = subprocess.run([sys.executable, "-c", _PEAK, src, out],
                       env=dict(os.environ, REPO=REPO), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    measured = int(r.stdout.strip()) / os.path.getsize(src)
    declared = handler.memory_factor
    assert measured <= declared, \
        f"{label}: measured {measured:.2f}x exceeds the declared {declared}x"
    assert declared <= measured * 1.5, \
        f"{label}: declared {declared}x is far above the measured {measured:.2f}x"


@pytest.mark.skipif(not (sys.platform == "darwin" or sys.platform.startswith("linux")),
                    reason="no way to read unreclaimable memory here")
def test_a_streamed_video_needs_memory_that_does_not_grow_with_it(tmp_path):
    """The CLI streams a video's light clean (November plan N3b). Its peak memory
    that cannot be reclaimed must not grow with the file: the difference between a
    200 MB and a 40 MB clip, per byte, stays under the declared streaming factor --
    which is what the preflight multiplies the file size by."""
    peaks = {}
    for mb in (40, 200):
        src = _write(tmp_path, f"v{mb}.mp4",
                     c.handbuilt(media=bytes(range(256)) * (mb * 4096)))
        r = subprocess.run([sys.executable, "-c", _STREAM, src,
                            str(tmp_path / f"out{mb}.mp4")],
                           env=dict(os.environ, REPO=REPO), capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        peaks[mb] = int(r.stdout.strip())
        os.unlink(src)
        os.unlink(tmp_path / f"out{mb}.mp4")
    growth = (peaks[200] - peaks[40]) / (160 * 1024 * 1024)
    assert growth <= Mp4Handler.stream_memory_factor, peaks
    assert peaks[200] <= Mp4Handler.stream_memory_factor * 200 * 1024 * 1024, peaks


def test_a_format_can_price_its_levels_apart(tmp_path):
    """The per-level table: PDF's full clean costs more than its other levels."""
    from src.scrub.formats.pdf.handler import PdfHandler
    d = default_dispatcher()
    head = b"%PDF-1.7\n" + bytes(7)
    assert d.memory_factor(head, "F1") == PdfHandler.memory_factor
    assert d.memory_factor(head, "F3") == PdfHandler.memory_factors["F3"]


_PICTURE_PEAK = r"""
import os, resource, sys
sys.path.insert(0, os.environ["REPO"])
from src.scrub.dispatch import default_dispatcher

def peak():
    if sys.platform.startswith("linux"):
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) * 1024
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

d = default_dispatcher()
data = open(sys.argv[1], "rb").read()
base = peak()
d.resolve(data).scrub(data, sys.argv[2])
print(peak() - base)
"""


def _picture(kind: str, w: int, h: int):
    import numpy as np
    from PIL import Image
    if kind == "flat":
        return Image.new("RGB", (w, h), (90, 140, 200))
    rng = np.random.default_rng(1)
    return Image.fromarray(rng.integers(0, 256, (h, w, 3), dtype="uint8"))


@pytest.mark.parametrize("fmt,ext,level,kind", [
    ("JPEG", "jpg", "F2", "flat"), ("JPEG", "jpg", "F2", "noisy"),
    ("JPEG", "jpg", "F3", "flat"), ("JPEG", "jpg", "F3", "noisy"),
    ("PNG", "png", "F2", "flat"), ("PNG", "png", "F2", "noisy"),
])
def test_a_decoding_level_costs_what_its_pixels_cost(fmt, ext, level, kind, tmp_path):
    """Memory follows the picture, not the file: measured in a fresh process, a flat
    and a noisy picture of the same size must both fit the declared bytes per pixel.
    The worst of three runs is compared, because the allocator's high-water mark
    moves between runs."""
    from src.scrub.formats.jpeg.handler import JpegHandler
    from src.scrub.formats.png.handler import PngHandler
    w, h = 3000, 2000
    src = str(tmp_path / f"p.{ext}")
    _picture(kind, w, h).save(src, fmt, **({"quality": 92} if fmt == "JPEG" else {}))
    worst = 0
    for _ in range(3):
        r = subprocess.run([sys.executable, "-c", _PICTURE_PEAK, src, level],
                           env=dict(os.environ, REPO=REPO), capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        worst = max(worst, int(r.stdout.strip()))
    declared = (JpegHandler if fmt == "JPEG" else PngHandler).bytes_per_pixel[level]
    assert worst / (w * h) <= declared, f"{worst / (w * h):.1f} over {declared} bytes a pixel"


@pytest.mark.parametrize("fmt,ext,level", [("JPEG", "jpg", "F3"), ("PNG", "png", "F2")])
def test_a_small_file_with_many_pixels_is_priced_by_its_pixels(fmt, ext, level, tmp_path,
                                                                monkeypatch):
    """A flat 12-megapixel picture is a tiny file: a factor of its SIZE would wave it
    through on a machine that cannot decode it."""
    src = str(tmp_path / f"flat.{ext}")
    _picture("flat", 4000, 3000).save(src, fmt)
    assert os.path.getsize(src) < 300_000
    out = str(tmp_path / f"out.{ext}")
    monkeypatch.setattr(resources, "available_memory", lambda: 50_000_000)
    with pytest.raises(ResourceError, match="at this level"):
        cli.scrub_file(src, out, level)
    assert not os.path.exists(out)
    cli.scrub_file(src, out, "F1")                 # the light clean decodes nothing
    os.unlink(out)
    cli.scrub_file(src, out, level, check_memory=False)
    assert os.path.exists(out)


@pytest.mark.parametrize("ext,codec,level", [
    ("mp3", "libmp3lame", "F3"), ("m4a", "aac", "F3"), ("m4a", "aac", "F2")])
def test_audio_is_priced_by_its_length_not_its_bitrate(ext, codec, level, tmp_path):
    import shutil
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg absent")
    needs = []
    for rate in ("64k", "192k"):
        src = tmp_path / f"{rate}.{ext}"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=20",
                        "-ac", "2", "-c:a", codec, "-b:a", rate, str(src)], check=True)
        data = src.read_bytes()
        needs.append(default_dispatcher().resolve(data).memory_need(data, level))
    assert all(needs) and abs(needs[0] - needs[1]) < 2_000_000, needs

