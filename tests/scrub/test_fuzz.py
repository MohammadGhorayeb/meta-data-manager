"""Mutated-input fuzzing: the fail-closed contract, checked rather than intended.

`docs/limits.md` and the CLI's exit-code table both promise that any file the tool
cannot fully scrub raises and writes nothing. That is easy to believe from reading
the handlers and hard to believe from reading the *libraries* underneath them, which
raise their own exception types from paths that only a damaged file reaches.

So real files are mutated — bytes flipped, regions zeroed, chunks cut and inserted,
truncations — and two properties are asserted on every result:

1. **`scrub()` raises `ScrubError` or succeeds.** Any other exception type means the
   CLI reports *unexpected* (exit 1) instead of the documented parse failure (exit
   4). Fail-closed still holds either way — nothing is written — but this project
   documents its exit codes as stable and asserts on them, so the classification is
   part of the contract rather than cosmetics.

2. **`describe()` never raises on a file we successfully scrubbed.** It is wrapped in
   a swallow, so a failure here does not crash: it silently degrades every report to
   "this format cannot describe its own metadata", which is exactly the kind of quiet
   regression nothing else would catch. A `describe()` failure on a file the scrub
   *rejected* is fine and expected — we could not read it either.

This found five real bugs on its first run, all of the same species: a library
exception crossing the handler boundary. `pikepdf.PdfError` from a deferred parse,
`UnicodeDecodeError` / `ValueError` / `TypeError` from our own serializer meeting a
damaged name object, and Pillow's `UnidentifiedImageError` / `OSError` from a JPEG
whose scan is broken but whose segments still walk.

Seeded, so a failure is reproducible; the seeds are part of the test.
"""
from __future__ import annotations

import random

import pytest

from src.scrub.dispatch import default_dispatcher
from src.scrub.errors import ScrubError

# Tuned for the suite's time budget rather than for maximum coverage: this runs on
# every push, twice (3.11 and 3.14). The wider sweep that found the original five
# bugs was 250 mutations x 3 seeds x every tier; it is reproducible by raising these
# two numbers, and worth doing after any change to a walker or a serializer.
SEEDS = (99, 1234)
MUTATIONS = 25          # per seed per (format, tier)


def _mutations(data: bytes, rnd: random.Random, n: int):
    """Damage in the shapes that actually occur: truncation, bit rot, holes,
    splices. Not uniformly random bytes — a file of noise is rejected by the magic
    check and never reaches the code worth testing."""
    yield "truncate-half", data[: len(data) // 2]
    yield "truncate-tiny", data[:16]
    for i in range(n):
        buf = bytearray(data)
        if not buf:
            return
        kind = rnd.choice(["flip", "zero", "cut", "grow"])
        if kind == "flip":
            for _ in range(rnd.randint(1, 8)):
                pos = rnd.randrange(len(buf))
                buf[pos] ^= 1 << rnd.randrange(8)
        elif kind == "zero":
            pos, length = rnd.randrange(len(buf)), rnd.randint(1, 64)
            buf[pos:pos + length] = b"\x00" * min(length, len(buf) - pos)
        elif kind == "cut":
            pos, length = rnd.randrange(len(buf)), rnd.randint(1, 128)
            del buf[pos:pos + length]
        else:
            pos = rnd.randrange(len(buf))
            buf[pos:pos] = bytes(rnd.randrange(256)
                                 for _ in range(rnd.randint(1, 32)))
        yield f"{kind}-{i}", bytes(buf)


def _corpus(tmp_path) -> dict[str, tuple[bytes, tuple[str, ...]]]:
    from . import corpus as imgc
    from . import docx_corpus as dc
    from . import flac_corpus as fc
    from . import m4a_corpus as mc
    from . import mp3_corpus as m3c
    from . import pdf_corpus as pc
    from .test_png import _png

    out: dict[str, tuple[bytes, tuple[str, ...]]] = {
        "jpeg": (imgc.build_torture_jpeg(), ("F1", "F2", "F3")),
        "png": (_png(), ("F1", "F2")),
        "pdf": (open(pc.torture_pdf(str(tmp_path / "t.pdf")), "rb").read(),
                ("F1", "F2", "F3")),
        "docx": (open(dc.synthetic(str(tmp_path / "s.docx")), "rb").read(),
                 ("F1", "F2")),
    }
    if m3c.HAVE_FFMPEG:
        out["mp3"] = (open(m3c.torture_mp3(str(tmp_path / "t.mp3")), "rb").read(),
                      ("F1", "F3"))
    if fc.HAVE_FFMPEG:
        out["flac"] = (open(fc.torture_flac(str(tmp_path / "t.flac")), "rb").read(),
                       ("F1", "F2"))
    if mc.HAVE_FFMPEG:
        out["m4a"] = (open(mc.torture_m4a(str(tmp_path / "t.m4a")), "rb").read(),
                      ("F1", "F2", "F3"))
    return out


@pytest.fixture(scope="module")
def corpus_files(tmp_path_factory):
    return _corpus(tmp_path_factory.mktemp("fuzz"))


@pytest.mark.parametrize("seed", SEEDS)
def test_a_damaged_file_never_escapes_the_fail_closed_contract(corpus_files, seed):
    """Every failure must be a ScrubError, so the CLI can classify it."""
    dispatcher = default_dispatcher()
    escapes: list[str] = []

    for fmt, (data, tiers) in corpus_files.items():
        for fidelity in tiers:
            rnd = random.Random(seed)
            for label, blob in _mutations(data, rnd, MUTATIONS):
                try:
                    handler = dispatcher.resolve(blob)
                except ScrubError:
                    continue                       # not claimed by any handler: fine
                except Exception as exc:           # noqa: BLE001
                    escapes.append(f"{fmt}/{fidelity}/{label} resolve: "
                                   f"{type(exc).__name__}: {exc}")
                    continue
                try:
                    handler.scrub(blob, fidelity)
                except ScrubError:
                    continue                       # the documented outcome
                except Exception as exc:           # noqa: BLE001
                    escapes.append(f"{fmt}/{fidelity}/{label} scrub: "
                                   f"{type(exc).__name__}: {exc}")

    assert not escapes, (
        f"{len(escapes)} non-ScrubError escape(s); the CLI would report these as "
        f"'unexpected' (exit 1) rather than a parse failure (exit 4):\n  "
        + "\n  ".join(escapes[:10]))


@pytest.mark.parametrize("seed", SEEDS)
def test_describe_survives_anything_we_agreed_to_scrub(corpus_files, seed):
    """If the scrub succeeded, the file was readable — so the report must be able to
    describe both sides of it. Failing here does not crash; it silently turns every
    report into "cannot describe itself", which is why it needs a test."""
    dispatcher = default_dispatcher()
    failures: list[str] = []

    for fmt, (data, tiers) in corpus_files.items():
        for fidelity in tiers:
            rnd = random.Random(seed)
            for label, blob in _mutations(data, rnd, MUTATIONS):
                try:
                    handler = dispatcher.resolve(blob)
                    out = handler.scrub(blob, fidelity)
                except Exception:                  # noqa: BLE001
                    continue                       # covered by the test above
                for side, payload in (("input", blob), ("output", out)):
                    try:
                        handler.describe(payload)
                    except Exception as exc:       # noqa: BLE001
                        failures.append(f"{fmt}/{fidelity}/{label} {side}: "
                                        f"{type(exc).__name__}: {exc}")

    assert not failures, (
        f"{len(failures)} describe() failure(s) on files we scrubbed successfully:\n  "
        + "\n  ".join(failures[:10]))


def test_the_mutator_actually_produces_files_we_still_claim(corpus_files):
    """A fuzz test whose inputs are all rejected at the magic check proves nothing.
    This asserts the mutations stay shallow enough to reach real code."""
    dispatcher = default_dispatcher()
    claimed = 0
    rnd = random.Random(SEEDS[0])
    data = corpus_files["jpeg"][0]
    for _label, blob in _mutations(data, rnd, MUTATIONS):
        try:
            dispatcher.resolve(blob)
            claimed += 1
        except Exception:                          # noqa: BLE001
            pass
    assert claimed > MUTATIONS // 4, (
        f"only {claimed} of {MUTATIONS} mutants were claimed by a handler — the "
        f"mutator is too destructive to be testing anything")
