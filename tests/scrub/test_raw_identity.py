"""Phase 4 M16 — the shared TIFF-IFD writer, and identity removal from camera RAW.

Raw is the format where nothing may move: the sensor data sits at offsets the file's
own tables record, and the maker note -- where the serials live -- also carries what
the decoder needs. So every write here is size-preserving and confined to the bytes
it names, and the acceptance is two decodes, not a parse: LibRaw's sensor data
bit-identical AND a render with the camera's own white balance pixel-identical
(the survey showed a Canon passing the first with its colour gone).

CI runs on the hand-built corpus (`raw_corpus.py`); the eight real files from
`tests/corpus/raw/manifest.txt` run where they exist.
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import struct
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from src.scrub.errors import ParseError  # noqa: E402
from src.scrub.formats.raw import identity  # noqa: E402
from src.scrub.standards import tiff_ifd as t  # noqa: E402
from tests.scrub import raw_corpus as rc  # noqa: E402

rawpy = pytest.importorskip("rawpy")
np = pytest.importorskip("numpy")
HAVE_EXIFTOOL = shutil.which("exiftool") is not None
VARIANTS = [(n, o) for n in rc.NOTES if n != "unknown" for o in ("<", ">")]
IDS = [f"{n}-{'II' if o == '<' else 'MM'}" for n, o in VARIANTS]


def _decode(data: bytes, tmp_path, name: str):
    path = tmp_path / name
    path.write_bytes(data)
    with rawpy.imread(str(path)) as r:
        return (r.raw_image.copy(),
                r.postprocess(use_camera_wb=True, no_auto_bright=True))


# --------------------------------------------------------------------------- #
# The corpus
# --------------------------------------------------------------------------- #
def test_the_corpus_imports_nothing_from_the_scrubber():
    tree = ast.parse(open(os.path.join(os.path.dirname(__file__), "raw_corpus.py"),
                          encoding="utf-8").read())
    imported = [n.module for n in ast.walk(tree)
                if isinstance(n, ast.ImportFrom) and n.module]
    imported += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
                 for a in n.names]
    assert not [m for m in imported if m.split(".")[0] in ("src", "scrub")]


@pytest.mark.parametrize("note,order", VARIANTS, ids=IDS)
def test_every_fixture_is_a_raw_libraw_decodes(note, order, tmp_path):
    sensor, render = _decode(rc.build(note, order), tmp_path, "f.dng")
    assert sensor.shape == (rc.H, rc.W) and len(np.unique(sensor)) > 100
    assert render.shape == (rc.H, rc.W, 3)


@pytest.mark.skipif(not HAVE_EXIFTOOL, reason="exiftool not installed")
@pytest.mark.parametrize("note", ["canon", "nikon", "olympus"])
def test_an_independent_reader_finds_what_the_fixture_plants(note, tmp_path):
    """Otherwise every "it is gone" below passes vacuously."""
    path = tmp_path / "f.dng"
    path.write_bytes(rc.build(note))
    out = subprocess.run(["exiftool", "-a", "-G1", "-s", str(path)],
                         capture_output=True, text=True).stdout
    for value in (rc.ARTIST, rc.BODY_SERIAL, rc.OWNER):
        assert value.decode() in out
    assert rc.MN_SERIAL.decode() in out or rc.MN_INTERNAL.decode() in out


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #
def test_raw_magics_are_opt_in():
    """An EXIF block inside a JPEG claiming `IIRO` is malformed, not a camera file."""
    olympus = rc.build("olympus", magic=0x4F52)
    with pytest.raises(ParseError, match="magic"):
        t.parse(olympus, strict=True)
    assert t.parse(olympus, strict=True, magics=t.RAW_MAGICS).magic == 0x4F52


def test_subifds_are_walked_to_the_raw_image():
    tree = t.parse(rc.build(), strict=True)
    raw = tree.ifd("SubIFD0")
    assert raw is not None and raw.get(0x0106).raw_value & 0xFFFF == 32803  # CFA


@pytest.mark.parametrize("note,order", VARIANTS, ids=IDS)
def test_each_maker_note_layout_is_located(note, order):
    data = rc.build(note, order)
    mn = t.makernote(data, t.parse(data, strict=True))
    assert mn.vendor == note
    names = [i.name for i in mn.ifds]
    assert names[0] == "MakerNote"
    if note == "olympus":
        assert "MakerNote/Equipment" in names


def test_an_unmodelled_maker_note_is_located_but_not_guessed_at():
    data = rc.build("unknown")
    mn = t.makernote(data, t.parse(data, strict=True))
    assert mn.vendor == "unknown" and mn.ifds == [] and mn.length > 0


# --------------------------------------------------------------------------- #
# Size-preserving writes
# --------------------------------------------------------------------------- #
def _changed(a: bytes, b) -> list[int]:
    return [i for i, (x, y) in enumerate(zip(a, b, strict=True)) if x != y]


def test_blank_value_touches_only_the_value():
    data = rc.build()
    tree = t.parse(data, strict=True)
    entry = tree.ifd("IFD0").get(0x013B)                         # Artist
    buf = bytearray(data)
    t.blank_value(buf, entry)
    changed = _changed(data, buf)
    assert changed and all(entry.data_offset <= i < entry.data_offset
                           + entry.data_length for i in changed)
    assert rc.ARTIST not in buf


def test_remove_entry_shrinks_the_directory_in_place_and_moves_nothing_else():
    data = rc.build("sony")
    tree = t.parse(data, strict=True)
    exif = tree.ifd("ExifIFD")
    note = exif.get(t.TAG_MAKERNOTE)
    dir_start, dir_len = exif.offset, 2 + 12 * len(exif.entries) + 4
    buf = bytearray(data)
    assert t.remove_entry(buf, exif, t.TAG_MAKERNOTE)
    assert len(buf) == len(data)
    for i in _changed(data, buf):
        assert (dir_start <= i < dir_start + dir_len
                or note.data_offset <= i < note.data_offset + note.data_length), i
    after = t.parse(bytes(buf), strict=True).ifd("ExifIFD")
    assert after.get(t.TAG_MAKERNOTE) is None
    assert [e.tag for e in after.entries] == sorted(e.tag for e in after.entries)
    assert len(after.entries) == len(exif.entries)
    assert buf[dir_start + dir_len - 12:dir_start + dir_len] == bytes(12)
    assert bytes(buf[note.data_offset:note.data_offset + note.data_length]) == \
        bytes(note.data_length)


def test_a_span_outside_its_value_is_refused():
    data = rc.build()
    mn = t.makernote(data, t.parse(data, strict=True))
    lens_info = mn.ifds[0].get(0x4019)
    with pytest.raises(ParseError, match="outside"):
        t.blank_span(bytearray(data), lens_info, 28, 5)


# --------------------------------------------------------------------------- #
# Identity removal
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("note,order", VARIANTS, ids=IDS)
def test_every_identity_value_is_gone_from_the_bytes(note, order):
    data = rc.build(note, order)
    buf = bytearray(data)
    removed = identity.blank_identity(buf)
    assert len(buf) == len(data) and removed
    for secret in rc.SECRETS:
        assert secret not in buf, f"{secret!r} survived in a {note} file"
    if note == "nikon":
        assert struct.pack("<I", rc.SHUTTER_COUNT) not in buf
    assert t.parse(bytes(buf), strict=True).ifd("SubIFD0") is not None


def test_a_hidden_copy_in_a_binary_block_is_found_by_its_value():
    """The Canon 80D finding: a second copy of the owner's name inside CameraInfo,
    at an offset no table names. The fixture plants one; ExifTool cannot see it."""
    data = rc.build("canon")
    assert data.count(rc.OWNER) == 3                  # EXIF, MakerNote, CameraInfo
    buf = bytearray(data)
    removed = identity.blank_identity(buf)
    assert rc.OWNER not in buf
    assert any("further cop" in r for r in removed)


@pytest.mark.parametrize("note,order", VARIANTS, ids=IDS)
def test_what_the_decoder_needs_is_untouched(note, order, tmp_path):
    """Sensor data bit-identical, camera-white-balance render pixel-identical, and
    the colour blocks the maker notes carry beside the serials byte-identical."""
    data = rc.build(note, order)
    buf = bytearray(data)
    identity.blank_identity(buf)
    a, b = _decode(data, tmp_path, "a.dng"), _decode(bytes(buf), tmp_path, "b.dng")
    assert np.array_equal(a[0], b[0]), "sensor data changed"
    assert np.array_equal(a[1], b[1]), "the camera-white-balance render changed"
    if note in ("canon", "nikon"):
        keep = bytes(range(200)) if note == "canon" else bytes(range(140))
        assert keep in buf, "the colour block beside the serials was touched"


def test_an_unmodelled_maker_note_is_refused_not_half_cleaned():
    with pytest.raises(ParseError, match="unmodelled"):
        identity.blank_identity(bytearray(rc.build("unknown")))


# --------------------------------------------------------------------------- #
# The real files, where they exist
# --------------------------------------------------------------------------- #
RAW_DIR = os.path.expanduser(os.environ.get("RAW_SAMPLES", "~/metadata-research/raw"))
REAL = ["apple_iphone12pro.DNG", "canon_80d.CR2", "nikon_d750.NEF",
        "sony_a7m3.ARW", "olympus_em10m4.ORF"]
_IDENTITY_TAGS = ["Artist", "Copyright", "OwnerName", "SerialNumber",
                  "InternalSerialNumber", "LensSerialNumber", "ShutterCount",
                  "ImageUniqueID", "ExtenderSerialNumber", "FlashSerialNumber"]


def _identity_values(path: str) -> dict:
    out = subprocess.run(["exiftool", "-j", "-a", "-G1", "-u"]
                         + [f"-{n}" for n in _IDENTITY_TAGS] + [path],
                         capture_output=True, text=True).stdout
    found = json.loads(out)[0]
    found.pop("SourceFile", None)
    return {k: str(v).strip() for k, v in found.items()
            if str(v).strip().strip("0 .")}


@pytest.mark.skipif(not HAVE_EXIFTOOL, reason="exiftool not installed")
@pytest.mark.parametrize("name", REAL)
def test_real_raw_files_lose_every_identity_value_and_decode_identically(name,
                                                                         tmp_path):
    src = os.path.join(RAW_DIR, name)
    if not os.path.exists(src):
        pytest.skip(f"{name} not on this machine (see tests/corpus/raw/manifest.txt)")
    data = open(src, "rb").read()
    buf = bytearray(data)
    identity.blank_identity(buf)
    out = tmp_path / name
    out.write_bytes(buf)
    assert len(buf) == len(data)

    before, after = _identity_values(src), _identity_values(str(out))
    assert not {k for k, v in after.items() if before.get(k) == v}, \
        "ExifTool still reads an identity value"
    for key, value in before.items():
        if len(value) >= 4:
            assert value.encode() not in buf, f"{key} is still in the bytes"

    a, b = _decode(data, tmp_path, "a" + name), _decode(bytes(buf), tmp_path,
                                                          "b" + name)
    assert np.array_equal(a[0], b[0]), "sensor data changed"
    assert np.array_equal(a[1], b[1]), "the camera-white-balance render changed"
