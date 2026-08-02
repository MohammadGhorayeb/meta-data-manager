"""FLAC handler: walker, F1 (bit-preserving block strip), F2 (canonical re-encode).

FLAC is the audio counterpart of PNG — lossless, with metadata cleanly outside the
audio frames — so the bar here is higher than for MP3: content preservation means
BIT-IDENTICAL audio, not perceptual similarity, at both tiers.
"""
from __future__ import annotations

import hashlib
import subprocess

import pytest

from src.scrub import cli
from src.scrub.errors import FidelityError, ParseError
from src.scrub.formats.flac import f1, f2
from src.scrub.formats.flac import walker as w
from tests.scrub import flac_corpus as fc

pytestmark = pytest.mark.skipif(not fc.HAVE_FFMPEG, reason="ffmpeg not installed")

# Secrets the corpus plants across every locus; none may survive.
_SECRETS = [b"Title-", b"Artist-", b"Album-", b"GPS-", b"COVERGPS", b"app-hidden"]


def _pcm(path: str) -> str:
    """Decoded audio digest — the content identity for a lossless format."""
    r = subprocess.run(["ffmpeg", "-i", path, "-map", "0:a", "-vn", "-f", "s32le",
                        "-loglevel", "error", "pipe:1"], capture_output=True)
    return hashlib.sha1(r.stdout).hexdigest()


# --------------------------------------------------------------------------- #
# Walker
# --------------------------------------------------------------------------- #
def test_walker_accounts_for_every_block(tmp_path):
    p = fc.torture_flac(str(tmp_path / "t.flac"))
    layout = w.walk(open(p, "rb").read())
    names = [b.name for b in layout.blocks]
    assert names[0] == "STREAMINFO", "STREAMINFO must be first (spec)"
    for expected in ("VORBIS_COMMENT", "PICTURE", "APPLICATION"):
        assert expected in names, f"corpus should plant a {expected} block"
    assert layout.id3v2 is not None, "corpus plants an out-of-spec ID3v2 prefix"
    assert layout.audio_start > 0 and layout.audio_end > layout.audio_start


def test_walker_rejects_non_flac():
    with pytest.raises(ParseError):
        w.walk(b"not a flac file at all")


def test_walker_rejects_a_block_that_overruns_the_file(tmp_path):
    """Truncation is only detectable in the METADATA region: FLAC has no end marker,
    so audio runs to EOF by definition and a cut inside it is indistinguishable from
    a shorter recording. Cutting inside a metadata block must fail closed."""
    p = fc.torture_flac(str(tmp_path / "t.flac"))
    data = open(p, "rb").read()
    last_block = w.walk(data).blocks[-1]
    with pytest.raises(ParseError):
        w.walk(data[:last_block.offset + 6])   # header says more than the file holds


def test_vorbis_comment_parses_little_endian_lengths(tmp_path):
    """The one real spec trap in FLAC: every length is big-endian EXCEPT inside
    VORBIS_COMMENT, which is little-endian (Vorbis heritage)."""
    p = str(tmp_path / "v.flac")
    fc.base_flac(p)
    fc._add_tags(p, "XYZ")
    layout = w.walk(open(p, "rb").read())
    vendor, items = w.parse_vorbis_comment(layout.vorbis_comment.payload)
    assert vendor, "a real encoder always writes a vendor string"
    assert any(i.startswith("TITLE=") or i.startswith("title=") for i in items)


# --------------------------------------------------------------------------- #
# F1 — bit-preserving
# --------------------------------------------------------------------------- #
def test_f1_removes_every_secret(tmp_path):
    p = fc.torture_flac(str(tmp_path / "t.flac"))
    out = f1.scrub(open(p, "rb").read())
    for s in _SECRETS:
        assert s not in out, f"{s!r} survived F1"
    assert not out.startswith(b"ID3"), "out-of-spec ID3v2 prefix survived"


def test_f1_keeps_audio_bit_identical(tmp_path):
    """F1's whole promise: the audio bytes never move."""
    p = fc.torture_flac(str(tmp_path / "t.flac"))
    data = open(p, "rb").read()
    src_audio = data[w.walk(data).audio_start:]
    out = f1.scrub(data)
    assert out[w.walk(out).audio_start:] == src_audio


def test_f1_drops_the_vendor_string(tmp_path):
    """The vendor string names the encoder ("Lavf...", "reference libFLAC..."). It is
    a producer fingerprint that lives in a metadata block, so — unlike MP3's LAME tag
    — it costs nothing to remove, and F1 must not leave it."""
    p = str(tmp_path / "v.flac")
    fc.base_flac(p)
    before = w.walk(open(p, "rb").read())
    vendor, _ = w.parse_vorbis_comment(before.vorbis_comment.payload)
    assert vendor, "precondition: the encoder wrote a vendor string"
    out = f1.scrub(open(p, "rb").read())
    assert vendor.encode() not in out
    assert w.walk(out).vorbis_comment is None


def test_f1_emits_no_tool_constant(tmp_path):
    """Regression: F1 once emitted a fixed empty VORBIS_COMMENT and 8 KiB of zero
    padding. Both were constants stamped on every output — the scrubber fingerprint
    the guard exists to catch — so both were removed rather than normalised."""
    p = str(tmp_path / "v.flac")
    fc.base_flac(p)
    out = f1.scrub(open(p, "rb").read())
    layout = w.walk(out)
    assert all(b.type != w.PADDING for b in layout.blocks), "padding block re-appeared"
    assert layout.vorbis_comment is None, "empty comment block re-appeared"
    assert b"\x00" * 512 not in out, "long constant run re-appeared in output"


def test_f1_is_deterministic(tmp_path):
    p = fc.torture_flac(str(tmp_path / "t.flac"))
    data = open(p, "rb").read()
    assert f1.scrub(data) == f1.scrub(data)


def test_f1_residuals_clean(tmp_path):
    p = fc.torture_flac(str(tmp_path / "t.flac"))
    assert f1.residuals(f1.scrub(open(p, "rb").read())) == []


def test_f1_residuals_flag_a_leaky_file(tmp_path):
    """The residual check must be able to SEE a leak, or its silence means nothing."""
    p = fc.torture_flac(str(tmp_path / "t.flac"))
    assert f1.residuals(open(p, "rb").read()) != []


# --------------------------------------------------------------------------- #
# F2 — canonical lossless re-encode (the zero-cost A2 defense)
# --------------------------------------------------------------------------- #
def test_f2_is_bit_exact_and_clean(tmp_path):
    p = fc.torture_flac(str(tmp_path / "t.flac"))
    out_path = str(tmp_path / "o.flac")
    cli.scrub_file(p, out_path, "F2")
    for s in _SECRETS:
        assert s not in open(out_path, "rb").read(), f"{s!r} survived F2"
    assert _pcm(out_path) == _pcm(p), "F2 must return bit-identical audio"


def test_f2_normalizes_the_encoder_structure(tmp_path):
    """A2 at zero quality cost: sources encoded at different compression levels have
    different block sizes; after F2 they must agree."""
    sizes = set()
    for level in (0, 5, 8):
        src = str(tmp_path / f"l{level}.flac")
        fc.base_flac(src, level=level)
        out = f2.scrub(open(src, "rb").read())
        info = w.streaminfo(w.walk(out))
        sizes.add((info["min_blocksize"], info["max_blocksize"]))
    assert len(sizes) == 1, f"F2 left producer-dependent block sizes: {sizes}"


def test_f2_does_not_stamp_our_own_toolchain(tmp_path):
    """ffmpeg writes its own vendor string on re-encode. Swapping the source's
    fingerprint for ours would be a scrubber signature, so F1 runs over the output."""
    p = str(tmp_path / "a.flac")
    fc.base_flac(p)
    out = f2.scrub(open(p, "rb").read())
    assert b"Lavf" not in out and b"libFLAC" not in out
    assert w.walk(out).vorbis_comment is None


def test_f3_is_refused(tmp_path):
    """FLAC is lossless: a lossy tier would destroy audio to buy nothing F2 has not
    already bought, so it is refused rather than quietly aliased to F2."""
    p = str(tmp_path / "a.flac")
    fc.base_flac(p)
    with pytest.raises(FidelityError):
        cli.scrub_file(p, str(tmp_path / "o.flac"), "F3")


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #
def test_dispatch_routes_flac_by_content_not_extension(tmp_path):
    from src.scrub.dispatch import default_dispatcher
    real = str(tmp_path / "a.flac")
    fc.base_flac(real)                          # ffmpeg picks format by extension
    misnamed = tmp_path / "misnamed.mp3"        # ...so rename afterwards
    misnamed.write_bytes(open(real, "rb").read())
    handler = default_dispatcher().resolve(misnamed.read_bytes())
    assert handler.format_id == "flac"


def test_id3_prefixed_flac_is_not_stolen_by_the_mp3_handler(tmp_path):
    """An ID3v2 tag can prefix either format, and it can be kilobytes long — far past
    any header window — so dispatch confirms over the whole buffer."""
    from src.scrub.dispatch import default_dispatcher
    p = fc.torture_flac(str(tmp_path / "t.flac"))          # plants an ID3v2 prefix
    data = open(p, "rb").read()
    assert data[:3] == b"ID3", "precondition: corpus plants an ID3v2 prefix"
    assert default_dispatcher().resolve(data).format_id == "flac"


def test_mp3_still_wins_its_own_id3_prefixed_files(tmp_path):
    """The other direction: the FLAC confirmation must not steal MP3 files."""
    from src.scrub.dispatch import default_dispatcher
    from tests.scrub import mp3_corpus as mc
    if not mc.HAVE_FFMPEG:
        pytest.skip("ffmpeg not installed")
    p = mc.torture_mp3(str(tmp_path / "t.mp3"))
    assert default_dispatcher().resolve(open(p, "rb").read()).format_id == "mp3"
