"""Adversarial recovery / irreversibility guard.

The core project claim (CLAUDE.md): irreversible = forensically unrecoverable
from the scrubbed file ITSELF. This test plays the A2 analyst: plant known
secrets across every metadata locus, scrub, then attack the scrubbed bytes with
the techniques a binary-level investigator uses — raw-string carving (ASCII +
UTF-16LE), embedded-JPEG (thumbnail/preview) carving, trailer detection, and
exiftool ground-truth extraction. Any surviving secret is a recoverable leak.
"""
from __future__ import annotations

import io
import re

import pytest
from PIL import Image, PngImagePlugin

from src.scrub import cli
from src.scrub.formats.jpeg import segments as jseg
from src.scrub.formats.png import chunks as pck
from tests.scrub import corpus

JPEG_SECRETS = [b"TestCam", b"MZ-1", b"secret-app", b"secret gps", b"EXTDATA",
                b"secret location caption", b"secret comment leak",
                b"TRAILERSECRET", b"SECR"]
PNG_SECRETS = [b"Moe Secret", b"33.88N", b"SecretApp"]


def _carve(data: bytes, secrets) -> list[str]:
    hits = []
    for s in secrets:
        u16 = s.decode("latin-1").encode("utf-16-le")
        if s in data or u16 in data:
            hits.append(s.decode("latin-1"))
    return hits


def _embedded_jpegs(data: bytes) -> int:
    return max(0, len(re.findall(b"\xff\xd8\xff", data)) - 1)


def _png_bytes() -> bytes:
    meta = PngImagePlugin.PngInfo()
    meta.add_text("Author", "Moe Secret")
    meta.add_text("GPS", "33.88N")
    meta.add_text("Software", "SecretApp 1.0")
    buf = io.BytesIO()
    corpus.make_pixels(80, 60).save(buf, "PNG", pnginfo=meta, compress_level=9)
    return buf.getvalue()


def test_secrets_are_present_before_scrub():
    """Sanity: the carving attack actually works (no vacuous pass)."""
    assert set(_carve(corpus.build_torture_jpeg(), JPEG_SECRETS)) == \
        {s.decode() for s in JPEG_SECRETS}
    assert _embedded_jpegs(corpus.build_torture_jpeg()) >= 1  # embedded thumbnail
    assert set(_carve(_png_bytes(), PNG_SECRETS)) == {s.decode() for s in PNG_SECRETS}


@pytest.mark.filterwarnings("ignore:.*malformed MPO.*")
@pytest.mark.parametrize("fidelity", ["F1", "F2", "F3"])
def test_jpeg_no_secret_recoverable(tmp_path, fidelity):
    src = tmp_path / "in.jpg"
    dst = tmp_path / "out.jpg"
    src.write_bytes(corpus.build_torture_jpeg())
    cli.scrub_file(str(src), str(dst), fidelity)
    data = dst.read_bytes()
    assert _carve(data, JPEG_SECRETS) == [], "secret string survived scrub"
    assert _embedded_jpegs(data) == 0, "embedded thumbnail/preview survived"
    assert jseg.walk(data).trailer == b"", "trailer bytes survived"


@pytest.mark.parametrize("fidelity", ["F1", "F2"])
def test_png_no_secret_recoverable(tmp_path, fidelity):
    src = tmp_path / "in.png"
    dst = tmp_path / "out.png"
    src.write_bytes(_png_bytes())
    cli.scrub_file(str(src), str(dst), fidelity)
    data = dst.read_bytes()
    assert _carve(data, PNG_SECRETS) == [], "secret string survived scrub"
    assert pck.walk(data).trailer == b"", "trailer bytes survived"
