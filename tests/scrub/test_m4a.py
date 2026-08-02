"""M4A handler: ISOBMFF box surgery, F1/F2/F3.

MAT2 refuses this format outright, so these are capabilities the benchmark's best
alternative does not have at all.

The distinctive risk here is not tag removal — it is that `stco`/`co64` hold ABSOLUTE
file offsets of the audio chunks, so deleting any box ahead of `mdat` slides the
audio and leaves a file that still parses, still reports the right duration, and
decodes to garbage. Several tests below exist purely to make that failure loud.
"""
from __future__ import annotations

import hashlib
import subprocess

import pytest

from src.scrub import cli
from src.scrub.dispatch import default_dispatcher
from src.scrub.errors import ParseError, ScrubError
from src.scrub.formats.m4a import f1, f3
from src.scrub.standards import isobmff as iso
from tests.scrub import m4a_corpus as mc

pytestmark = pytest.mark.skipif(not mc.HAVE_FFMPEG, reason="ffmpeg not installed")

_SECRETS = [b"Title-", b"Artist-", b"Album-", b"GPS-", b"COVERGPS", b"Encoder-"]


def _pcm(path: str) -> str:
    r = subprocess.run(["ffmpeg", "-i", path, "-map", "0:a", "-vn", "-f", "s16le",
                        "-loglevel", "error", "pipe:1"], capture_output=True)
    return hashlib.sha1(r.stdout).hexdigest()


# --------------------------------------------------------------------------- #
# Shared ISOBMFF walker (Phase 4 reuses this module for MP4 / HEIC)
# --------------------------------------------------------------------------- #
def test_walker_round_trips_unmodified(tmp_path):
    """serialize(parse(x)) == x. If the walker cannot reproduce a file it did not
    edit, nothing it produces after an edit can be trusted either."""
    p = str(tmp_path / "a.m4a")
    mc.base_m4a(p)
    data = open(p, "rb").read()
    assert iso.serialize(iso.parse(data)) == data


def test_walker_parses_the_metadata_tree(tmp_path):
    p = mc.torture_m4a(str(tmp_path / "t.m4a"))
    boxes = iso.parse(open(p, "rb").read())
    moov = next(b for b in boxes if b.type == b"moov")
    assert moov.find(b"udta/meta/ilst") is not None, "corpus should plant iTunes tags"


def test_walker_rejects_a_box_that_overruns(tmp_path):
    p = str(tmp_path / "a.m4a")
    mc.base_m4a(p)
    data = bytearray(open(p, "rb").read())
    data[0:4] = (len(data) * 4).to_bytes(4, "big")     # ftyp claims far too much
    with pytest.raises(ParseError):
        iso.parse(bytes(data))


def test_chunk_offset_patching_is_reversible(tmp_path):
    """Shifting by +N then -N must return the original table."""
    p = str(tmp_path / "a.m4a")
    mc.base_m4a(p, faststart=True)
    boxes = iso.parse(open(p, "rb").read())
    before = iso.serialize(boxes)
    assert iso.shift_chunk_offsets(boxes, 1234) > 0, "no stco/co64 found to patch"
    assert iso.serialize(boxes) != before
    iso.shift_chunk_offsets(boxes, -1234)
    assert iso.serialize(boxes) == before


# --------------------------------------------------------------------------- #
# F1 — bit-preserving
# --------------------------------------------------------------------------- #
def test_f1_removes_every_secret(tmp_path):
    p = mc.torture_m4a(str(tmp_path / "t.m4a"))
    out = f1.scrub(open(p, "rb").read())
    for s in _SECRETS:
        assert s not in out, f"{s!r} survived F1"


def test_f1_keeps_audio_playable_after_moving_it(tmp_path):
    """THE test for this format. With faststart, `moov` precedes `mdat`, so removing
    metadata slides the audio and every chunk offset must be patched. A file whose
    offsets were not patched still parses and still has the right byte count — only
    decoding reveals the damage, which is why this asserts on decoded audio."""
    p = mc.torture_m4a(str(tmp_path / "t.m4a"), faststart=True)
    src_pcm = _pcm(p)
    out_path = str(tmp_path / "o.m4a")
    cli.scrub_file(p, out_path, "F1")
    assert _pcm(out_path) == src_pcm, "audio no longer decodes identically"
    assert _pcm(out_path) != hashlib.sha1(b"").hexdigest(), "audio decoded to nothing"


def test_f1_keeps_the_audio_samples_byte_identical(tmp_path):
    p = mc.torture_m4a(str(tmp_path / "t.m4a"))
    data = open(p, "rb").read()
    src_mdat = next(b for b in iso.parse(data) if b.type == b"mdat").payload
    out_mdat = next(b for b in iso.parse(f1.scrub(data)) if b.type == b"mdat").payload
    assert out_mdat == src_mdat


def test_f1_zeroes_the_structural_timestamps(tmp_path):
    """Creation/modification times live in mvhd/tkhd/mdhd as structural fields, not
    tags — so a tag-oriented scrubber leaves them and the file still says when it was
    made. Assert the source really had them, or the test proves nothing."""
    p = mc.torture_m4a(str(tmp_path / "t.m4a"))
    data = open(p, "rb").read()

    def stamps(buf):
        out = []
        for root in iso.parse(buf):
            for box in root.walk():
                if box.type in f1.TIMESTAMP_BOXES:
                    width = 8 if box.payload[0] == 1 else 4
                    out.append(box.payload[4:4 + width * 2])
        return out

    assert any(s.strip(b"\x00") for s in stamps(data)), "source had no timestamps"
    assert all(not s.strip(b"\x00") for s in stamps(f1.scrub(data)))


def test_f1_is_deterministic(tmp_path):
    p = mc.torture_m4a(str(tmp_path / "t.m4a"))
    data = open(p, "rb").read()
    assert f1.scrub(data) == f1.scrub(data)


def test_f1_residuals_clean_and_able_to_see_a_leak(tmp_path):
    p = mc.torture_m4a(str(tmp_path / "t.m4a"))
    data = open(p, "rb").read()
    assert f1.residuals(data) != [], "the check cannot see a dirty file"
    assert f1.residuals(f1.scrub(data)) == []


def test_f1_refuses_a_file_it_cannot_keep_consistent(tmp_path):
    """Fail-closed: if the audio moves and there is no chunk table to patch, the
    sample tables may now point anywhere — refuse rather than emit it."""
    p = str(tmp_path / "a.m4a")
    mc.base_m4a(p, faststart=True)
    boxes = iso.parse(open(p, "rb").read())
    for root in boxes:                       # strip every chunk-offset table
        for box in root.walk():
            box.children = [c for c in box.children if c.type not in (b"stco", b"co64")]
    # Add metadata so the strip has something to remove and the audio must move.
    moov = next(b for b in boxes if b.type == b"moov")
    moov.children.append(iso.Box(type=b"udta", offset=0, size=8 + 16,
                                 header_len=8, payload=b"x" * 16))
    with pytest.raises(ScrubError):
        f1.scrub(iso.serialize(boxes))


# --------------------------------------------------------------------------- #
# F2 / F3
# --------------------------------------------------------------------------- #
def test_f2_is_lossless_and_normalizes_the_layout(tmp_path):
    """F2 copies the coded stream, so the decoded audio must be identical, and the
    muxer's layout choice (moov before or after mdat) must stop being visible."""
    layouts = set()
    digests = set()
    for name, fast in (("plain", False), ("fast", True)):
        src = str(tmp_path / f"{name}.m4a")
        mc.base_m4a(src, faststart=fast)
        out = str(tmp_path / f"{name}_o.m4a")
        cli.scrub_file(src, out, "F2")
        assert _pcm(out) == _pcm(src), "F2 must be lossless"
        boxes = iso.parse(open(out, "rb").read())
        layouts.add(tuple(b.type for b in boxes))
        digests.add(next(b for b in boxes if b.type == b"mdat").payload[:64])
    assert len(layouts) == 1, f"muxer layout still differs after F2: {layouts}"
    assert len(digests) == 1, "same audio should give the same coded stream"


def test_f3_re_encodes_and_stays_perceptually_faithful(tmp_path):
    p = mc.torture_m4a(str(tmp_path / "t.m4a"))
    out = str(tmp_path / "o.m4a")
    cli.scrub_file(p, out, "F3")               # raises if it fails the gate
    for s in _SECRETS:
        assert s not in open(out, "rb").read()


def test_f3_gate_rejects_unrelated_audio(tmp_path):
    """The gate must not be a rubber stamp — and this is also the regression for
    decoding via a temp file rather than a pipe: MP4 needs seeking, so a piped decode
    silently yields no samples and scores every file at 0.0000."""
    from src.scrub.errors import ContentError
    from src.scrub.standards import perceptual
    a = str(tmp_path / "a.m4a")
    b = str(tmp_path / "b.m4a")
    mc.base_m4a(a, freq=440, faststart=False)   # moov AFTER mdat: needs seeking
    mc.base_m4a(b, freq=1900, faststart=False)
    same = perceptual.check(open(a, "rb").read(), open(a, "rb").read(), 44100)
    assert same > 0.99, "identical audio must score high (pipe-decode regression)"
    with pytest.raises(ContentError):
        perceptual.check(open(a, "rb").read(), open(b, "rb").read(), 44100)


def test_source_rate_is_parsed_from_the_sample_description(tmp_path):
    """The gate's band depends on this; a misparse once returned 0 and made the gate
    compare a 0 Hz band, which nothing can match."""
    for rate in (44100, 22050):
        p = str(tmp_path / f"r{rate}.m4a")
        mc.base_m4a(p, rate=rate)
        assert f3._source_rate(open(p, "rb").read()) == rate


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #
def test_dispatch_claims_m4a_audio(tmp_path):
    p = str(tmp_path / "a.m4a")
    mc.base_m4a(p)
    assert default_dispatcher().resolve(open(p, "rb").read()).format_id == "m4a"


def test_dispatch_refuses_mp4_video(tmp_path):
    """MP4 video is the same container but belongs to Phase 4. Claiming it here would
    strip it with audio-shaped assumptions, so the handler must decline and dispatch
    must fail closed rather than guess."""
    from src.scrub.errors import UnsupportedFormatError
    p = str(tmp_path / "v.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc=duration=1:size=64x64:rate=10", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=1", "-c:v", "libx264", "-c:a",
                    "aac", "-shortest", p], check=True)
    with pytest.raises(UnsupportedFormatError):
        default_dispatcher().resolve(open(p, "rb").read())
