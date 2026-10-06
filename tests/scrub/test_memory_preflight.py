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
    monkeypatch.setattr(resources, "available_memory", lambda: 1000)

    def never(*_a, **_k):
        raise AssertionError("the scrub ran although the preflight should refuse")
    monkeypatch.setattr(Mp4Handler, "scrub_f1", never)

    with pytest.raises(ResourceError) as exc:
        cli.scrub_file(src, out, "F1")
    message = str(exc.value)
    assert "needs about" in message and "available" in message
    assert "--skip-memory-check" in message
    assert not os.path.exists(out)


def test_the_cli_exits_7_and_the_override_flag_scrubs_anyway(tmp_path, monkeypatch):
    src = _write(tmp_path, "v.mp4", c.handbuilt())
    out = str(tmp_path / "out.mp4")
    monkeypatch.setattr(resources, "available_memory", lambda: 1000)
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

default_dispatcher()
base = peak()
cli.scrub_file(sys.argv[1], sys.argv[2], "F1", check_memory=False)
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
