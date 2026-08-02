"""MP3 handler: walker, F1 (bit-preserving tag strip), F3 (canonical re-encode).

Skipped when ffmpeg/lame are absent (the corpus + F3 need them)."""
from __future__ import annotations

import hashlib
import subprocess

import pytest

from src.scrub.formats.mp3 import f1, f3
from src.scrub.formats.mp3 import walker as w
from tests.scrub import mp3_corpus as mc

pytestmark = pytest.mark.skipif(not mc.HAVE_FFMPEG, reason="ffmpeg not installed")
_needs_lame = pytest.mark.skipif(not mc.HAVE_LAME, reason="lame not installed")

# secrets planted across loci by the corpus (all must vanish)
_SECRETS = [b"Title-", b"Artist-", b"GPS-", b"COVERGPS", b"ApeGhost", b"OldTitle",
            b"HITCHHIKER", b"ape-hidden"]


def _pcm(data: bytes) -> str:
    p = subprocess.run(["ffmpeg", "-i", "pipe:0", "-f", "s16le", "-ac", "2",
                        "-ar", "44100", "pipe:1"], input=data, capture_output=True)
    return hashlib.sha1(p.stdout).hexdigest()


def _carve(data: bytes, secrets) -> list:
    hits = []
    for s in secrets:
        u16 = s.decode("latin-1").encode("utf-16-le")
        if s in data or u16 in data:
            hits.append(s.decode("latin-1"))
    return hits


# --------------------------------------------------------------------------- #
# walker
# --------------------------------------------------------------------------- #
def test_walker_parses_torture(tmp_path):
    p = mc.torture_mp3(str(tmp_path / "t.mp3"))
    data = open(p, "rb").read()
    L = w.walk(data)
    assert L.id3v2 is not None and L.id3v2[0] == 0          # front ID3v2
    assert len(L.frames) > 0
    assert L.xing is not None and L.xing.lame_version       # Xing/LAME header found
    kinds = {t.kind for t in L.trailers}
    assert "apev2" in kinds and "id3v1" in kinds             # ghost trailers detected
    assert L.appended[1] > 0                                 # hitchhiker present


def test_walker_rejects_non_mp3():
    from src.scrub.errors import ParseError
    with pytest.raises(ParseError):
        w.walk(b"not audio at all, no frame sync here" * 4)


# --------------------------------------------------------------------------- #
# F1 — bit-preserving
# --------------------------------------------------------------------------- #
def test_f1_removes_all_user_metadata(tmp_path):
    data = open(mc.torture_mp3(str(tmp_path / "t.mp3")), "rb").read()
    assert _carve(data, _SECRETS)                            # present before
    scrubbed = f1.scrub(data)
    assert _carve(scrubbed, _SECRETS) == [], "a secret survived F1"
    assert f1.residuals(scrubbed) == []
    L = w.walk(scrubbed)
    assert L.id3v2 is None and L.trailers == [] and L.appended[1] == 0


def test_f1_preserves_pcm_bit_exact(tmp_path):
    data = open(mc.torture_mp3(str(tmp_path / "t.mp3")), "rb").read()
    assert _pcm(data) == _pcm(f1.scrub(data)), "F1 changed the decoded audio"


def test_f1_is_deterministic(tmp_path):
    data = open(mc.torture_mp3(str(tmp_path / "t.mp3")), "rb").read()
    assert f1.scrub(data) == f1.scrub(data)


def test_f1_residuals_flags_dirty(tmp_path):
    data = open(mc.torture_mp3(str(tmp_path / "t.mp3")), "rb").read()
    assert f1.residuals(data)                                # dirty input flagged


# --------------------------------------------------------------------------- #
# F3 — canonical re-encode (A2 defense)
# --------------------------------------------------------------------------- #
@_needs_lame
def test_f3_removes_all_user_metadata(tmp_path):
    data = open(mc.torture_mp3(str(tmp_path / "t.mp3")), "rb").read()
    scrubbed = f3.scrub(data)
    assert _carve(scrubbed, _SECRETS) == []
    assert f3.residuals(scrubbed) == []


@_needs_lame
def test_f3_normalizes_encoder_fingerprint(tmp_path):
    """The A2 defense: F1 keeps each producer's fingerprint, F3 collapses all to
    one canonical 192 CBR signature."""
    src = mc.producers(str(tmp_path), repeats=1)

    def sig(data: bytes):
        L = w.walk(data)
        # A missing Xing header is itself a producer signature — shineenc writes
        # none at all — so absence is a value here, never a crash.
        version = L.xing.lame_version if L.xing else b"<no-xing>"
        return (version, tuple(sorted({fr.bitrate for fr in L.frames})))

    raw_sigs = {sig(open(p[0], "rb").read()) for p in src.values()}
    assert len(raw_sigs) > 1, "producers should differ before scrub"
    f3_sigs = {sig(f3.scrub(open(paths[0], "rb").read())) for paths in src.values()}
    assert len(f3_sigs) == 1, f"F3 must collapse producers to one signature: {f3_sigs}"
    assert b"<no-xing>" not in {v for v, _ in f3_sigs}, "F3 output must carry the canonical header"


@_needs_lame
@pytest.mark.skipif(not mc.HAVE_SHINE, reason="shineenc not installed")
def test_f3_accepts_audio_from_a_non_lowpassing_encoder(tmp_path):
    """Regression: the perceptual gate used to compare full-band waveforms, so a
    source from an encoder that codes past LAME's ~19 kHz lowpass (shineenc, on
    broadband audio) failed the gate at NCC 0.95 and the tool refused a file it had
    scrubbed correctly — the removed energy was inaudible. The gate now compares the
    audible band; this must scrub, and must still be perceptually faithful."""
    wav = str(tmp_path / "n.wav")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "anoisesrc=duration=2:color=white:seed=3:amplitude=0.3",
                    "-ac", "2", "-ar", "44100", wav], check=True)
    src = str(tmp_path / "n.mp3")
    subprocess.run(["shineenc", "-q", "-b", "128", wav, src], check=True)
    data = open(src, "rb").read()

    scrubbed = f3.scrub(data)                     # raised ContentError before the fix
    ncc = f3._best_ncc(f3._bandlimit(f3._pcm_mono(data)),
                       f3._bandlimit(f3._pcm_mono(scrubbed)))
    assert ncc >= f3.PERCEPTUAL_MIN_NCC


@_needs_lame
def test_perceptual_gate_still_rejects_different_audio(tmp_path):
    """The band-limited gate must not become a rubber stamp: unrelated audio in
    place of the scrub output has to be caught."""
    from src.scrub.errors import ContentError
    a = open(mc.torture_mp3(str(tmp_path / "a.mp3")), "rb").read()
    other = str(tmp_path / "b.mp3")
    mc.base_mp3_ffmpeg(other, freq=1900, dur=2.0)
    with pytest.raises(ContentError):
        f3._check_perceptual(a, open(other, "rb").read())


@_needs_lame
def test_f3_is_deterministic(tmp_path):
    data = open(mc.torture_mp3(str(tmp_path / "t.mp3")), "rb").read()
    assert f3.scrub(data) == f3.scrub(data)


@_needs_lame
def test_f3_content_preserved_perceptually(tmp_path):
    data = open(mc.torture_mp3(str(tmp_path / "t.mp3")), "rb").read()
    scrubbed = f3.scrub(data)                                # raises if past the gate
    # Compare the way the gate does: in the audible band. Full-band comparison
    # would penalise the inaudible >16 kHz content LAME lowpasses away.
    ncc = f3._best_ncc(f3._bandlimit(f3._pcm_mono(data)),
                       f3._bandlimit(f3._pcm_mono(scrubbed)))
    assert ncc >= f3.PERCEPTUAL_MIN_NCC
