"""No copy of the input outlives a killed clean (November plan N2c, `workdir.py`).

The deep and full cleans write the original file into a work folder for another
program. On an exception it is removed (the fuzz campaign checked 160,476 refusals:
nothing left); on `kill -9` nothing runs, so it is removed by the next run instead,
and until then it sits in a folder only its owner can read.
"""
from __future__ import annotations

import os
import signal
import stat
import subprocess
import sys
import tempfile

import pytest

from src.scrub import cli, workdir


@pytest.fixture
def tmp_root(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    return tmp_path


def _dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def test_work_folders_are_private_and_named_after_their_process(tmp_root):
    with workdir.workdir("pdf-f3") as path:
        base = os.path.dirname(path)
        assert stat.S_IMODE(os.stat(base).st_mode) == 0o700
        assert os.path.basename(base) == f"scrub-{os.getuid()}"
        assert os.path.basename(path).startswith(f"pid{os.getpid()}-pdf-f3-")
        assert workdir.sweep() == 0, "a live run's folder must never be swept"
    assert not os.path.exists(path)


def test_a_killed_run_leaves_its_copy_only_until_the_next_run(tmp_root):
    """A real SIGKILL inside the work folder, then the sweep every run starts with."""
    code = (f"import os, signal, tempfile; tempfile.tempdir = {str(tmp_root)!r}\n"
            "from src.scrub import workdir\n"
            "with workdir.workdir('m4a-f3') as p:\n"
            "    open(os.path.join(p, 'in.m4a'), 'wb').write(b'original, metadata and all')\n"
            "    os.kill(os.getpid(), signal.SIGKILL)\n")
    proc = subprocess.run([sys.executable, "-c", code],
                          cwd=os.path.dirname(os.path.dirname(os.path.dirname(
                              os.path.abspath(__file__)))))
    assert proc.returncode == -signal.SIGKILL
    base = tmp_root / f"scrub-{os.getuid()}"
    left = list(base.iterdir())
    assert len(left) == 1 and (left[0] / "in.m4a").exists(), "the control: a copy was left"
    assert workdir.sweep() == 1
    assert list(base.iterdir()) == []


def test_the_cli_sweeps_before_it_cleans(tmp_root, tmp_path):
    from . import corpus
    base = workdir.root()
    stale = os.path.join(base, f"pid{_dead_pid()}-pdf-f2-x")
    os.mkdir(stale)
    src, dst = tmp_path / "in.jpg", tmp_path / "out.jpg"
    src.write_bytes(corpus.make_base_jpeg())
    cli.scrub_file(str(src), str(dst), "F1")
    assert not os.path.exists(stale)


def test_a_work_root_others_can_read_is_refused(tmp_root):
    base = workdir.root()
    os.chmod(base, 0o755)
    try:
        with pytest.raises(PermissionError):
            workdir.root()
    finally:
        os.chmod(base, 0o700)
