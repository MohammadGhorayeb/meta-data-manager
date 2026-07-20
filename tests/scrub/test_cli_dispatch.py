"""Dispatch + CLI: routing, fail-closed behavior, exit codes, atomic output."""
from __future__ import annotations

import os

import pytest

from src.scrub import cli
from src.scrub.dispatch import Dispatcher, default_dispatcher
from src.scrub.errors import UnsupportedFormatError
from src.scrub.formats.jpeg.handler import JpegHandler
from src.scrub.formats.jpeg import segments as seg
from tests.scrub import corpus


def test_dispatch_resolves_jpeg_by_magic():
    d = default_dispatcher()
    h = d.resolve(corpus.make_base_jpeg())
    assert isinstance(h, JpegHandler)


def test_dispatch_unsupported_raises():
    d = default_dispatcher()
    with pytest.raises(UnsupportedFormatError):
        d.resolve(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)


def test_cli_scrub_file_end_to_end(tmp_path):
    src = tmp_path / "in.jpg"
    dst = tmp_path / "out.jpg"
    src.write_bytes(corpus.build_torture_jpeg())
    cli.scrub_file(str(src), str(dst), "F1")
    assert dst.exists()
    out = dst.read_bytes()
    kinds = {sg.kind for sg in seg.walk(out).segments}
    assert "app1_exif" not in kinds and "com" not in kinds


def test_cli_main_unsupported_format_exit3(tmp_path, capsys):
    src = tmp_path / "in.bin"
    dst = tmp_path / "out.bin"
    src.write_bytes(b"\x00\x01\x02\x03not a known format" * 4)
    rc = cli.main([str(src), str(dst)])
    assert rc == 3
    assert not dst.exists(), "no output on failure (fail closed)"


def test_cli_main_unimplemented_fidelity_exit5(tmp_path):
    # F3 is the still-unimplemented JPEG tier (F1/F2 are built); it must fail
    # closed with exit 5 and write no output.
    src = tmp_path / "in.jpg"
    dst = tmp_path / "out.jpg"
    src.write_bytes(corpus.make_base_jpeg())
    rc = cli.main([str(src), str(dst), "--fidelity", "F3"])
    assert rc == 5
    assert not dst.exists()


def test_cli_main_success_exit0(tmp_path):
    src = tmp_path / "in.jpg"
    dst = tmp_path / "out.jpg"
    src.write_bytes(corpus.make_base_jpeg())
    rc = cli.main([str(src), str(dst), "--fidelity", "F1"])
    assert rc == 0
    assert dst.exists()


def test_verify_blocks_leaky_handler(tmp_path):
    """If a (hypothetical) handler returned unscrubbed bytes, the CLI's post-scrub
    verify must catch it and refuse to write output."""
    class LeakyHandler(JpegHandler):
        def scrub_f1(self, data):     # returns input unchanged -> metadata intact
            return data

    d = Dispatcher()
    d.register(LeakyHandler())
    src = tmp_path / "in.jpg"
    dst = tmp_path / "out.jpg"
    src.write_bytes(corpus.build_torture_jpeg())
    with pytest.raises(Exception):
        cli.scrub_file(str(src), str(dst), "F1", dispatcher=d)
    assert not dst.exists()
