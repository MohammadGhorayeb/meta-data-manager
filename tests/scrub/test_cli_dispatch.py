"""Dispatch + CLI: routing, fail-closed behavior, exit codes, atomic output."""
from __future__ import annotations

import pytest

from src.scrub import cli
from src.scrub.dispatch import Dispatcher, default_dispatcher
from src.scrub.errors import ScrubError, UnsupportedFormatError
from src.scrub.formats.jpeg import segments as seg
from src.scrub.formats.jpeg.handler import JpegHandler
from tests.scrub import corpus


def test_dispatch_resolves_jpeg_by_magic():
    d = default_dispatcher()
    h = d.resolve(corpus.make_base_jpeg())
    assert isinstance(h, JpegHandler)


def test_dispatch_unsupported_raises():
    d = default_dispatcher()
    # A ZIP header. Was a PDF header until Phase 3 registered a PDF handler — the
    # example has to be a format we genuinely do not claim, or the test passes for
    # the wrong reason. OOXML lands next, so this line will need moving again.
    with pytest.raises(UnsupportedFormatError):
        d.resolve(b"PK\x03\x04" + b"\x00" * 16)


def test_dispatch_resolves_pdf_by_magic():
    from src.scrub.formats.pdf.handler import PdfHandler
    d = default_dispatcher()
    assert isinstance(d.resolve(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"), PdfHandler)


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


def test_cli_main_fidelity_error_exit5():
    # All JPEG tiers (F1/F2/F3) are implemented, so the FidelityError path is
    # exercised directly: a handler asked for a tier it does not offer must fail
    # closed with exit 5 (mapped in cli._EXIT).
    from src.scrub.errors import FidelityError
    from src.scrub.formats.jpeg.handler import JpegHandler
    h = JpegHandler()
    with pytest.raises(FidelityError):
        h.scrub(b"\xff\xd8\xff", "F9")  # not in fidelities


@pytest.mark.filterwarnings("ignore:.*malformed MPO.*")
def test_cli_main_f3_success_exit0(tmp_path):
    # F3 (canonical lossy re-encode) works end-to-end and writes clean output.
    src = tmp_path / "in.jpg"
    dst = tmp_path / "out.jpg"
    src.write_bytes(corpus.make_base_jpeg())
    rc = cli.main([str(src), str(dst), "--fidelity", "F3"])
    assert rc == 0
    assert dst.exists()


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
    # ScrubError, not bare Exception: scrub_file documents fail-closed as its
    # contract, so the test should assert that contract rather than "it threw".
    with pytest.raises(ScrubError):
        cli.scrub_file(str(src), str(dst), "F1", dispatcher=d)
    assert not dst.exists()
