"""P4 M8–M11 — MP4 / MOV F1.

The acceptance test for this format is two things at once, because each alone has
already been shown to lie:

  * **decode identity** through ffmpeg, which shares no code with us — a wrong
    offset table still parses (the M4A lesson);
  * **no bytes of mdat left unreferenced** — ExifTool reads a file as GPS-free while
    the exact coordinates sit in a stale copy inside `mdat` (the M7 lesson).

The hand-built corpus runs everywhere. Real iPhone and Mac videos run only where
they exist (symlinked, git-ignored), because they are personal files.
"""
from __future__ import annotations

import ast
import re
import shutil
import struct
import subprocess

import pytest

from src.scrub.dispatch import default_dispatcher
from src.scrub.errors import ParseError, UnsupportedFormatError
from src.scrub.formats.mp4 import f1
from src.scrub.formats.mp4.handler import Mp4Handler
from src.scrub.standards import isobmff as iso

from . import mp4_corpus as vc

needs_ffmpeg = pytest.mark.skipif(not vc.HAVE_FFMPEG, reason="ffmpeg not installed")
REAL = vc.real_samples()
needs_real = pytest.mark.skipif(not REAL, reason="no real videos here (p4 plan §5)")
_ISO6709 = re.compile(rb"[+-]\d{2}\.\d{3,}[+-]\d{3}\.\d{3,}(?:[+-][\d.]+)?/")


@pytest.fixture(scope="module")
def src(tmp_path_factory):
    if not vc.HAVE_FFMPEG:
        pytest.skip("ffmpeg not installed")
    return vc._encode(str(tmp_path_factory.mktemp("mp4src") / "src.mp4"))


VARIANTS = [(False, False), (False, True), (True, False), (True, True)]
IDS = ["moov-last", "moov-last-co64", "moov-first", "moov-first-co64"]


@pytest.fixture(params=VARIANTS, ids=IDS)
def torture(request, tmp_path, src) -> str:
    moov_first, co64 = request.param
    return vc.build(str(tmp_path / "t.mov"), moov_first=moov_first, co64=co64, src=src)


def _scrub_to(path: str, tmp_path) -> tuple[bytes, bytes, str]:
    data = open(path, "rb").read()
    out = f1.scrub(data)
    out_path = str(tmp_path / "out.mov")
    open(out_path, "wb").write(out)
    return data, out, out_path


# --------------------------------------------------------------------------- #
# The corpus is independent of what it tests
# --------------------------------------------------------------------------- #
def test_the_corpus_imports_nothing_from_the_scrubber():
    tree = ast.parse(open(vc.__file__).read())
    names = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    names += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
              for a in n.names]
    assert not [n for n in names if n.startswith("src")], names


@needs_ffmpeg
def test_the_corpus_carries_every_locus_it_claims(torture):
    """Controls: a test that a secret is gone means nothing unless it was there."""
    data = open(torture, "rb").read()
    for secret in vc.SECRETS:
        assert secret in data, f"control: {secret!r} missing from the corpus"
    assert vc.unreferenced(data) > 0, "control: the corpus has no stale bytes in mdat"
    assert {t["handler"] for t in vc.read_tracks(data).values()} == \
        {b"vide", b"soun", b"meta"}


# --------------------------------------------------------------------------- #
# Content preservation — decoded, and sample for sample
# --------------------------------------------------------------------------- #
@needs_ffmpeg
def test_the_movie_decodes_identically(torture, tmp_path):
    _, _, out_path = _scrub_to(torture, tmp_path)
    assert vc.decoded(out_path) == vc.decoded(torture)


@needs_ffmpeg
def test_every_kept_sample_is_the_same_bytes(torture, tmp_path):
    """Read back with the corpus's own reader, not the scrubber's resolver."""
    data, out, _ = _scrub_to(torture, tmp_path)
    before, after = vc.read_tracks(data), vc.read_tracks(out)
    assert sorted(after) == [1, 2]
    for track_id in after:
        assert after[track_id]["samples"] == before[track_id]["samples"]


# --------------------------------------------------------------------------- #
# The metadata is gone — including where no reader looks
# --------------------------------------------------------------------------- #
@needs_ffmpeg
def test_every_planted_secret_is_gone(torture, tmp_path):
    _, out, _ = _scrub_to(torture, tmp_path)
    assert [s for s in vc.SECRETS if s in out] == []
    assert f1.residuals(out) == []


@needs_ffmpeg
def test_no_byte_of_mdat_is_left_unreferenced(torture, tmp_path):
    """THE locus. Copying mdat whole — what M4A F1 and ExifTool both do — keeps the
    stale copy, and with it the planted GPS."""
    data, out, _ = _scrub_to(torture, tmp_path)
    mdat = next(b for b in iso.scan(data) if b.type == b"mdat")
    assert vc.STALE in data[mdat.offset:mdat.end], "control: stale copy is in mdat"
    assert vc.unreferenced(out) == 0


@needs_ffmpeg
def test_the_timed_metadata_track_and_references_to_it_go(torture, tmp_path):
    _, out, _ = _scrub_to(torture, tmp_path)
    assert b"mebx" not in out
    assert b"cdep" not in out, "a kept track still names the dropped one"
    assert b"sync" in out, "the reference to a KEPT track must survive"


@needs_ffmpeg
def test_the_structural_timestamps_are_zeroed(torture, tmp_path):
    _, out, _ = _scrub_to(torture, tmp_path)
    assert vc.movie_header(out)[4:12] == b"\x00" * 8
    assert struct.pack(">I", vc.STAMP) not in out


@needs_ffmpeg
def test_f1_is_a_fixed_point(torture, tmp_path):
    _, out, _ = _scrub_to(torture, tmp_path)
    assert f1.scrub(out) == out


@pytest.mark.skipif(not vc.have_encoder("libx264"), reason="no libx264")
def test_an_encoder_that_signs_the_stream_is_reported_as_kept(tmp_path):
    """F1 keeps every sample bit for bit, so x264's settings string -- inside the
    coded video, as in every WhatsApp video measured -- survives. The report must
    say so rather than let the reader assume it went."""
    src = vc._encode(str(tmp_path / "x.mp4"), video="libx264")
    data = open(vc.build(str(tmp_path / "t.mov"), src=src), "rb").read()
    out = f1.scrub(data)
    assert b"x264 - core" in out
    kept = Mp4Handler().kept(out, "F1")
    assert kept and "x264" in kept[0]


# --------------------------------------------------------------------------- #
# Identification
# --------------------------------------------------------------------------- #
@needs_ffmpeg
def test_a_quicktime_movie_routes_here(torture):
    assert default_dispatcher().resolve(open(torture, "rb").read()).format_id == "mp4"


@needs_ffmpeg
def test_an_audio_only_m4a_still_routes_to_m4a(tmp_path):
    from . import m4a_corpus as mc
    data = open(mc.torture_m4a(str(tmp_path / "a.m4a")), "rb").read()
    assert default_dispatcher().resolve(data).format_id == "m4a"


@needs_ffmpeg
@pytest.mark.parametrize("brand", [b"crx ", b"abcd"], ids=["canon-cr3", "unknown"])
def test_a_video_track_under_the_wrong_brand_is_declined(torture, brand):
    """CR3 is ISOBMFF with `vide` tracks. The brand list is a keep-list."""
    data = bytearray(open(torture, "rb").read())
    data[8:12] = brand
    data[16:20] = brand
    assert not Mp4Handler().claims(bytes(data))
    with pytest.raises(UnsupportedFormatError):
        default_dispatcher().resolve(bytes(data))


# --------------------------------------------------------------------------- #
# Fail closed on what is not modelled
# --------------------------------------------------------------------------- #
def _mutate(path: str, old: bytes, new: bytes, count: int = 1) -> bytes:
    data = open(path, "rb").read()
    assert data.count(old) >= count, f"control: {old!r} not in corpus"
    return data.replace(old, new, count)


@needs_ffmpeg
@pytest.mark.parametrize("how", ["fragmented", "two-mdat", "external-data",
                                 "subtitle-track", "compact-sizes"])
def test_refuses_what_it_does_not_model(torture, how):
    data = open(torture, "rb").read()
    if how == "fragmented":
        data += vc.box(b"moof", vc.box(b"mfhd", b"\x00" * 8))
    elif how == "two-mdat":
        data += vc.box(b"mdat", b"x")
    elif how == "external-data":
        data = _mutate(torture, b"alis\x00\x00\x00\x01", b"alis\x00\x00\x00\x00", 3)
    elif how == "subtitle-track":
        data = _mutate(torture, b"mhlrmeta", b"mhlrsbtl")
    elif how == "compact-sizes":
        data = _mutate(torture, b"stsz", b"stz2")
    with pytest.raises(ParseError):
        f1.scrub(data)


@needs_ffmpeg
def test_a_sample_count_the_file_cannot_hold_is_refused_not_allocated(torture):
    """A fixed-size `stsz` states its count as a bare number. Damaged to ~4 billion,
    the resolver once built a list that long before noticing -- a hang, not a
    refusal. The count must be checked against the file first."""
    data = bytearray(open(torture, "rb").read())
    at = data.index(b"stsz") + 4 + 4                   # past type, version/flags
    data[at:at + 8] = (1).to_bytes(4, "big") + (0xFFFFFFF0).to_bytes(4, "big")
    with pytest.raises(ParseError, match="more than"):
        f1.scrub(bytes(data))


@needs_ffmpeg
def test_refuses_a_sample_outside_mdat(torture):
    data = bytearray(open(torture, "rb").read())
    moov = next(b for b in iso.scan(bytes(data)) if b.type == b"moov")
    table = next(b for b in iso.parse(bytes(data[moov.offset:moov.end]))[0].walk()
                 if b.type in (b"stco", b"co64"))
    first = moov.offset + table.offset + table.header_len + 8
    width = 4 if table.type == b"stco" else 8
    data[first:first + width] = (len(data) + 1000).to_bytes(width, "big")
    with pytest.raises(ParseError):
        f1.scrub(bytes(data))


# --------------------------------------------------------------------------- #
# The shared resolver, checked against the corpus's independent reader
# --------------------------------------------------------------------------- #
@needs_ffmpeg
def test_the_shared_resolver_agrees_with_an_independent_reader(torture):
    data = open(torture, "rb").read()
    moov = next(b for b in iso.scan(data) if b.type == b"moov")
    root = iso.parse(data[moov.offset:moov.end])[0]
    theirs = vc.read_tracks(data)
    for trak, track_id in zip((c for c in root.children if c.type == b"trak"),
                              sorted(theirs), strict=True):
        ours = []
        for chunk in iso.chunks(trak.find(b"mdia/minf/stbl")):
            at = chunk.offset
            for size in chunk.sample_sizes:
                ours.append(data[at:at + size])
                at += size
        assert ours == theirs[track_id]["samples"]


# --------------------------------------------------------------------------- #
# Real videos — local only
# --------------------------------------------------------------------------- #
@needs_real
@needs_ffmpeg
@pytest.mark.parametrize("path", REAL, ids=[p.split("/")[-1] for p in REAL])
def test_real_videos_decode_identically_and_lose_every_stale_copy(path, tmp_path):
    data = open(path, "rb").read()
    out = f1.scrub(data)
    out_path = str(tmp_path / "out.mov")
    open(out_path, "wb").write(out)
    assert vc.decoded(out_path, seconds=20) == vc.decoded(path, seconds=20)
    assert f1.residuals(out) == []
    assert vc.unreferenced(out) == 0
    moov = next(b for b in iso.scan(data) if b.type == b"moov")
    gps = _ISO6709.search(data[moov.offset:moov.end])
    if gps:
        assert gps.group() not in out, "the recording's coordinates survived"


@needs_real
@pytest.mark.skipif(shutil.which("exiftool") is None, reason="exiftool absent")
def test_exiftool_leaves_the_stale_gps_it_cannot_see(tmp_path):
    """The benchmark claim in p4 plan §5.5, re-measured rather than repeated: after
    `exiftool -all=`, ExifTool lists no GPS while the coordinates are in the bytes."""
    path = next((p for p in REAL if "IMG_" in p), None)
    if path is None:
        pytest.skip("no iPhone clip here")
    data = open(path, "rb").read()
    moov = next(b for b in iso.scan(data) if b.type == b"moov")
    gps = _ISO6709.search(data[moov.offset:moov.end])
    if gps is None:
        pytest.skip("clip recorded without location")
    copy = tmp_path / "e.mov"
    copy.write_bytes(data)
    subprocess.run(["exiftool", "-q", "-all=", "-overwrite_original", str(copy)],
                   check=True)
    listed = subprocess.run(["exiftool", "-a", "-G1", "-ee", str(copy)],
                            capture_output=True).stdout.decode("utf-8", "replace")
    assert "GPS" not in listed, "ExifTool now sees it; update the plan and limits"
    assert gps.group() in copy.read_bytes(), "ExifTool now removes it; update docs"
