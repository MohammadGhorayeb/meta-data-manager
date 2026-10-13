"""Video telemetry (`docs/p6_tail_plan.md` §13): position stored as track samples
and vendor boxes, by action cameras, drones, 360 cameras and Android phones."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from src.scrub import cli, folder  # noqa: E402
from src.scrub.dispatch import default_dispatcher  # noqa: E402
from src.scrub.errors import ParseError  # noqa: E402
from src.scrub.formats.mp4 import f1  # noqa: E402
from src.scrub.formats.mp4 import inspect as mp4_inspect  # noqa: E402
from src.scrub.standards import isobmff as iso  # noqa: E402
from tests.scrub import telemetry_corpus as tc  # noqa: E402

HAVE_EXIFTOOL = shutil.which("exiftool") is not None
CLEANED = ("gopro", "android", "camm")


def _exiftool_gps(path) -> dict:
    r = subprocess.run(["exiftool", "-j", "-ee", "-n", "-G1", "-GPS*", str(path)],
                       capture_output=True, text=True)
    record = json.loads(r.stdout)[0]
    return {k: v for k, v in record.items() if "GPS" in k}


@pytest.mark.skipif(not HAVE_EXIFTOOL, reason="exiftool not installed")
@pytest.mark.parametrize("shape", tc.SHAPES)
def test_the_models_are_real_enough_that_exiftool_reads_their_position(shape,
                                                                       tmp_path):
    """A model ExifTool could not read would prove nothing about real files."""
    path = tmp_path / "in.mp4"
    path.write_bytes(tc.build(1, shape=shape))
    gps = _exiftool_gps(path)
    assert any(k.endswith("GPSLatitude") or k.endswith("GPSCoordinates") for k in gps)


@pytest.mark.parametrize("shape", CLEANED)
def test_position_and_identity_are_gone(shape):
    data = tc.build(2, shape=shape)
    assert all(p in data for p in tc.planted(2, shape))
    out = f1.scrub(data)
    assert [p for p in tc.planted(2, shape) if p in out] == []
    assert f1.residuals(out) == []


@pytest.mark.skipif(not HAVE_EXIFTOOL, reason="exiftool not installed")
@pytest.mark.parametrize("shape", CLEANED)
def test_exiftool_finds_no_position_after(shape, tmp_path):
    path = tmp_path / "out.mp4"
    path.write_bytes(f1.scrub(tc.build(2, shape=shape)))
    assert _exiftool_gps(path) == {}


def test_a_gopro_file_is_cleaned_not_refused():
    """Every GoPro recording has a timecode track. Until §13 that unmodelled handler
    refused the whole file; its one sample is the time of day recording started."""
    out = f1.scrub(tc.build(1, shape="gopro"))
    moov = iso.parse(out[next(b.offset for b in iso.scan(out) if b.type == b"moov"):])[0]
    handlers = {t.handler for t in f1._tracks(moov)}
    assert handlers == {b"vide", b"soun"}
    assert moov.find(b"trak/tref") is None or b"tmcd" not in iso.serialize(
        [moov.find(b"trak/tref")])
    assert tc.tmcd_sample(1) not in out


def test_variants_differing_only_in_telemetry_scrub_to_the_same_bytes():
    for shape in CLEANED:
        assert len({f1.scrub(tc.build(v, shape=shape)) for v in (1, 2, 3)}) == 1, shape


def test_the_report_names_what_went():
    d = mp4_inspect.describe(tc.build(1, shape="gopro"))
    assert d["udta:FIRM"] == "HD8.01.01.60.00"
    assert "udta:GPMF" in d
    assert d["Timecode track 3"].startswith("'tmcd'")
    assert d["Timed metadata track 4"].startswith("'gpmd'")
    assert "Camera-motion track 3" in mp4_inspect.describe(tc.build(1, shape="camm"))


def test_a_subtitle_track_is_refused_unless_asked():
    data = tc.build(1, shape="drone")
    with pytest.raises(ParseError, match="--drop-subtitles"):
        f1.scrub(data)
    out = f1.scrub(data, drop_subtitles=True)
    assert [p for p in tc.planted(1, "drone") if p in out] == []
    assert f1.residuals(out) == []


def test_the_flag_reaches_the_handler_from_the_cli_and_folder_mode(tmp_path):
    src = tmp_path / "drone.mp4"
    src.write_bytes(tc.build(1, shape="drone"))
    assert cli.main([str(src), str(tmp_path / "a.mp4"), "--no-report"]) == 4
    assert cli.main([str(src), str(tmp_path / "b.mp4"), "--no-report",
                     "--drop-subtitles"]) == 0
    d = default_dispatcher(drop_subtitles=True)
    assert d.resolve(src.read_bytes()).drop_subtitles is True
    folder_in = tmp_path / "in"
    folder_in.mkdir()
    shutil.copy(src, folder_in / "drone.mp4")
    rows = folder.clean_folder(str(folder_in), str(tmp_path / "out"), verify=False,
                               drop_subtitles=True).rows
    assert rows[0]["outcome"] == folder.CLEANED
