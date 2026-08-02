"""End-to-end integration: scrub a real JPEG via the CLI subprocess, then use
ExifTool (ground truth, CLAUDE.md) to confirm no metadata survives. Skipped when
exiftool is absent."""
from __future__ import annotations

import shutil
import subprocess
import sys

import pytest

from tests.scrub import corpus

pytestmark = pytest.mark.skipif(shutil.which("exiftool") is None,
                                reason="exiftool not installed")

_REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]


def _exiftool_tags(path: str) -> dict[str, str]:
    out = subprocess.run(
        ["exiftool", "-s", "-G1", "-a", "-u", str(path)],
        capture_output=True, text=True, check=True).stdout
    tags = {}
    for line in out.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            tags[k.strip()] = v.strip()
    return tags


def test_scrubbed_jpeg_has_no_identifying_metadata(tmp_path):
    src = tmp_path / "in.jpg"
    dst = tmp_path / "out.jpg"
    src.write_bytes(corpus.build_torture_jpeg())

    rc = subprocess.run(
        [sys.executable, "-m", "src.scrub", str(src), str(dst), "--fidelity", "F1"],
        cwd=str(_REPO_ROOT), capture_output=True, text=True)
    assert rc.returncode == 0, rc.stderr

    tags = _exiftool_tags(str(dst))
    # No group that carries provenance should remain.
    forbidden_groups = ("ExifIFD", "IFD0", "IFD1", "GPS", "XMP", "IPTC",
                        "Photoshop", "ICC_Profile", "ICC-header", "APP14",
                        "MPF", "File:Comment")
    leaked = {k: v for k, v in tags.items()
              if any(k.startswith(g) or g in k for g in forbidden_groups)}
    assert not leaked, f"ExifTool still sees metadata: {leaked}"

    # And no sentinel strings from the injected metadata survive anywhere.
    raw = dst.read_bytes()
    for sentinel in (b"secret", b"TestCam", b"SECR", b"TRAILERSECRET"):
        assert sentinel not in raw, f"sentinel {sentinel!r} survived in bytes"


@pytest.mark.filterwarnings("ignore:.*malformed MPO.*")
def test_scrubbed_jpeg_still_decodes_identically(tmp_path):
    import io

    from PIL import Image

    src = tmp_path / "in.jpg"
    dst = tmp_path / "out.jpg"
    torture = corpus.build_torture_jpeg()
    src.write_bytes(torture)

    subprocess.run(
        [sys.executable, "-m", "src.scrub", str(src), str(dst), "--fidelity", "F1"],
        cwd=str(_REPO_ROOT), capture_output=True, text=True, check=True)

    before = Image.open(io.BytesIO(torture)).convert("RGB").tobytes()
    after = Image.open(dst).convert("RGB").tobytes()
    assert before == after
