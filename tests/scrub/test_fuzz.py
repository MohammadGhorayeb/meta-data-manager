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

from src.scrub import cli
from src.scrub.dispatch import default_dispatcher
from src.scrub.errors import ParseError, ScrubError

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
    from . import bmp_corpus as bmc
    from . import corpus as imgc
    from . import docx_corpus as dc
    from . import elf_corpus as ec
    from . import epub_corpus as epc
    from . import flac_corpus as fc
    from . import gif_corpus as gfc
    from . import heic_corpus as hc
    from . import m4a_corpus as mc
    from . import macho_corpus as mc_
    from . import metafile_corpus as mfc
    from . import mp3_corpus as m3c
    from . import mp4_corpus as vc
    from . import pdf_corpus as pc
    from . import pe_corpus as pec
    from . import pptx_corpus as ppc
    from . import raw_corpus as rc
    from . import svg_corpus as svc
    from . import tiff_corpus as tfc
    from . import webp_corpus as wbc
    from . import xlsx_corpus as xc
    from . import zip_corpus as zc
    from .test_png import _png

    out: dict[str, tuple[bytes, tuple[str, ...]]] = {
        "raw": (rc.build("olympus"), ("F1",)),
        "raw-cr3": (rc.cr3(), ("F1",)),
        "raw-raf": (rc.raf(), ("F1",)),
        "exe": (ec.build("alice", go=True), ("F1",)),
        "exe-32be": (ec.build("alice", bits=32, order=">", shared_tail=True),
                     ("F1",)),
        "tiff": (tfc.build(), ("F1",)),
        "webp": (wbc.build(frames=3), ("F1",)),
        "gif": (gfc.build(frames=3), ("F1",)),
        "bmp": (bmc.build(1, header="v5", icc="custom"), ("F1",)),
        "bmp-rle8": (bmc.build(1, header="info", bpp=8, compression="rle8"), ("F1",)),
        "svg": (svc.build(shape="inkscape"), ("F1",)),
        "svgz": (svc.svgz(shape="illustrator"), ("F1",)),
        "emf": (mfc.emf(1, chunk=300), ("F1",)),
        "wmf": (mfc.wmf(1), ("F1",)),
        "exe-macho": (mc_.build("alice", go=True), ("F1",)),
        "exe-fat": (mc_.fat("alice"), ("F1",)),
        "exe-pe-msvc": (pec.build("alice", dll=True), ("F1",)),
        "exe-pe-mingw": (pec.build("alice", shape="mingw", go=True, plus=False),
                         ("F1",)),
        "jpeg": (imgc.build_torture_jpeg(), ("F1", "F2", "F3")),
        "png": (_png(), ("F1", "F2")),
        "pdf": (open(pc.torture_pdf(str(tmp_path / "t.pdf")), "rb").read(),
                ("F1", "F2", "F3")),
        "docx": (open(dc.synthetic(str(tmp_path / "s.docx")), "rb").read(),
                 ("F1", "F2")),
        # Containers whose members are files of their own: a mutation lands in the
        # container, a member's header or a member's body, and each must still
        # end in a ScrubError (Phase 6's validation pass added these three).
        "zip": (zc.build(1, shape="ditto"), ("F1",)),
        "epub": (epc.build(1, shape="sigil"), ("F1",)),
        # Excel and PowerPoint, every locus planted (November plan N2a: they had
        # been fuzzed once by hand, 3,000 each, and never by the suite).
        "xlsx": (xc.build(1), ("F1",)),
        "pptx": (ppc.build(1), ("F1",)),
    }
    if hc.HAVE_HEIF:
        out["heic"] = (open(hc.torture(str(tmp_path / "t.heic")), "rb").read(),
                       ("F1",))
    if m3c.HAVE_FFMPEG:
        out["mp3"] = (open(m3c.torture_mp3(str(tmp_path / "t.mp3")), "rb").read(),
                      ("F1", "F3"))
    if fc.HAVE_FFMPEG:
        out["flac"] = (open(fc.torture_flac(str(tmp_path / "t.flac")), "rb").read(),
                       ("F1", "F2"))
    if mc.HAVE_FFMPEG:
        out["m4a"] = (open(mc.torture_m4a(str(tmp_path / "t.m4a")), "rb").read(),
                      ("F1", "F2", "F3"))
    if vc.HAVE_FFMPEG:
        out["mp4"] = (open(vc.build(str(tmp_path / "t.mov")), "rb").read(), ("F1",))
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
                except ScrubError as exc:
                    # The documented outcome -- unless it is the bare base class,
                    # which the CLI can only report as "unexpected" (exit 1). The
                    # November campaign found jpegtran's refusals raised that way.
                    if cli.exit_code(exc) == 1:
                        escapes.append(f"{fmt}/{fidelity}/{label} scrub: bare "
                                       f"{type(exc).__name__}: {exc}")
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


# Found by the deep campaign of Phase 6's validation pass (3 seeds x 1,000
# mutations on ZIP, EPUB, HEIC and the telemetry MP4s; `docs/p6_tail_plan.md` §14):
# records cut short escaping as `struct.error` / `IndexError`. Replayed exactly.
@pytest.mark.parametrize("seed,label", [(1, "flip-643"), (2, "flip-113"),
                                        (3, "flip-372")])
def test_damaged_heic_records_are_refusals(tmp_path, seed, label):
    from . import heic_corpus as hc
    if not hc.HAVE_HEIF:
        pytest.skip("pillow-heif not installed")
    data = open(hc.torture(str(tmp_path / "t.heic")), "rb").read()
    # By number, not label: libheif on Linux writes other bytes, the random stream
    # diverges, and mutation 643 may be a different kind there -- still a valid
    # damaged file to hold to the contract, and the exact replay where bytes match.
    n = label.rsplit("-", 1)[1]
    blob = next(b for lbl, b in _mutations(data, random.Random(seed), 1000)
                if lbl.endswith(f"-{n}"))
    try:
        default_dispatcher().resolve(blob).scrub(blob, "F1")
    except ScrubError:
        pass


@pytest.mark.parametrize("cut", [3, 9, 15])
def test_an_end_record_cut_short_is_a_refusal(cut):
    from src.scrub.formats.ooxml import zipread

    from . import zip_corpus as zc
    data = zc.build(1, shape="ditto")
    with pytest.raises(ScrubError):
        zipread.read(data[:-cut])
    for blob in (data[:-cut], data[:-cut] + b"\x00" * cut):
        try:
            default_dispatcher().resolve(blob).scrub(blob, "F1")
        except ScrubError:
            pass


# --------------------------------------------------------------------------- #
# Found by the November campaign (scripts/fuzz_campaign.py, docs/november_plan.md
# N2a). Each failed before its fix.
# --------------------------------------------------------------------------- #
def _png_with(chunk: bytes, body: bytes) -> bytes:
    """`test_png._png()` with one chunk's data replaced and its CRC made right, so
    the damage gets past the CRC check to the decoder behind it."""
    import struct
    import zlib

    from .test_png import _png
    data, out, pos = _png(), bytearray(), 8
    out += data[:8]
    while pos + 8 <= len(data):
        length, ctype = struct.unpack(">I4s", data[pos:pos + 8])
        b = body if ctype == chunk else data[pos + 8:pos + 8 + length]
        out += struct.pack(">I", len(b)) + ctype + b
        out += struct.pack(">I", zlib.crc32(ctype + b) & 0xFFFFFFFF)
        pos += 12 + length
    return bytes(out)


@pytest.mark.parametrize("chunk,body", [
    (b"IDAT", b"\x00" * 40),                                  # does not inflate
    (b"IHDR", b"\x00\x00\x00\x10\x00\x00\x00\x10\x03\x02\x00\x00\x00"),  # depth 3
])
def test_a_png_whose_crcs_check_out_but_does_not_decode_is_a_parse_refusal(chunk, body):
    """Pillow's UnidentifiedImageError and OSError escaped PNG F2 as "unexpected"."""
    from src.scrub.formats.png import f2
    with pytest.raises(ParseError):
        f2.scrub(_png_with(chunk, body))


def test_a_mach_o_signature_string_past_its_directory_is_a_parse_refusal():
    """`bytes.index` raised ValueError("subsection not found") when a damaged
    Mach-O's code-directory identifier had no NUL before the directory's end."""
    from src.scrub.formats.exe import codesign
    with pytest.raises(ParseError):
        codesign._c_string(b"abc\0", 0, 3, "identifier")
    assert codesign._c_string(b"abc\0def", 0, 8, "identifier") == b"abc"

    from . import macho_corpus as mc
    data = mc.build("alice", go=True)
    blob = next(b for lbl, b in _mutations(data, random.Random(11), 20)
                if lbl == "grow-10")
    try:
        default_dispatcher().resolve(blob).scrub(blob, "F1")
    except ScrubError as exc:
        assert cli.exit_code(exc) != 1



def _replay(data: bytes, seed: int, label: str) -> bytes:
    return next(b for lbl, b in _mutations(data, random.Random(seed), 1000) if lbl == label)


def _refused(blob: bytes) -> ScrubError:
    with pytest.raises(ScrubError) as caught:
        default_dispatcher().resolve(blob).scrub(blob, "F1")
    assert cli.exit_code(caught.value) != 1, caught.value
    return caught.value


def test_an_epub_naming_an_unknown_xml_encoding_is_a_parse_refusal():
    """`encoding="TTF-8"` in META-INF made ElementTree raise LookupError."""
    import io
    import zipfile

    from . import epub_corpus as epc
    src = epc.build(1, shape="sigil")
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(src)) as zin, zipfile.ZipFile(out, "w") as zout:
        for info in zin.infolist():
            body = zin.read(info)
            if info.filename == "META-INF/container.xml":
                body = body.replace(b'encoding="UTF-8"', b'encoding="TTF-8"', 1)
                assert b"TTF-8" in body
            zout.writestr(info, body)
    assert isinstance(_refused(out.getvalue()), ParseError)


@pytest.mark.parametrize("shape,seed,label", [("msvc", 22, "flip-556"),
                                              ("mingw", 22, "flip-678")])
def test_a_pe_resource_directory_past_the_end_is_a_parse_refusal(shape, seed, label):
    """struct.error escaped the PE resource walk when an offset or count pointed
    past the end of the file."""
    from . import pe_corpus as pec
    data = (pec.build("alice", dll=True) if shape == "msvc"
            else pec.build("alice", shape="mingw", go=True, plus=False))
    assert isinstance(_refused(_replay(data, seed, label)), ParseError)


def test_a_pdf_name_that_is_not_utf8_compares_as_no_match():
    """A legal PDF name need not be UTF-8; `str()` on it raised UnicodeDecodeError
    in F1's key stripping. It is none of the names F1 acts on."""
    import pikepdf

    from src.scrub.formats.pdf import f1 as pdf_f1

    class Undecodable:                      # what pikepdf's Name did on byte 0xC9
        def __str__(self):
            raise UnicodeDecodeError("utf-8", b"\xc9", 0, 1, "invalid continuation byte")
    assert pdf_f1._name({"/Type": Undecodable()}, "/Type") == ""
    assert pdf_f1._name(pikepdf.Dictionary(Type=pikepdf.Name.Annot), "/Type") == "/Annot"


def test_an_svg_encoding_name_with_any_byte_is_refused_by_name():
    from src.scrub.formats.svg import f1 as svg_f1
    with pytest.raises(ParseError, match="encoding"):
        svg_f1._check_encoding(b'<?xml version="1.0" encoding="UT\xc6-8"?><svg/>')


# The corpora below hold Pillow-encoded JPEGs, so their bytes can differ on another
# platform; there the same seed and index make ANOTHER damaged file (the HEIC replay
# above met this on Linux). Where the bytes match the machine the campaign ran on,
# the replay must be refused; elsewhere it is held to the contract alone.
_REFERENCE = {"cr3": "ad47346385be1128", "torture_jpeg": "bb490d455494ebaa"}


def _is_reference(name: str, data: bytes) -> bool:
    import hashlib
    return hashlib.sha256(data).hexdigest()[:16] == _REFERENCE[name]


def _held_to_contract(tmp_path, blob: bytes, fidelity: str = "F1") -> None:
    src, dst = tmp_path / "in.bin", tmp_path / "out.bin"
    src.write_bytes(blob)
    try:
        cli.scrub_file(str(src), str(dst), fidelity)
    except ScrubError as exc:
        assert cli.exit_code(exc) != 1, exc
        assert not dst.exists()


def test_a_cr3_track_without_its_sample_table_is_a_parse_refusal(tmp_path):
    """AttributeError on None escaped when a damaged CR3 lacked a track's boxes."""
    from . import raw_corpus as rc
    data = rc.cr3()
    if not _is_reference("cr3", data):
        _held_to_contract(tmp_path, _nth(data, 33, 6))
        return
    assert isinstance(_refused(_replay(data, 33, "zero-4")), ParseError)


# The campaign's re-run (7 left of 1,803), each through the CLI path it was found on.
def _nth(data: bytes, seed: int, index: int) -> bytes:
    return next(b for i, (_lbl, b) in enumerate(_mutations(data, random.Random(seed), 1000))
                if i == index)


def _cli_refuses(tmp_path, blob: bytes, fidelity: str = "F1") -> ScrubError:
    src, dst = tmp_path / "in.bin", tmp_path / "out.bin"
    src.write_bytes(blob)
    with pytest.raises(ScrubError) as caught:
        cli.scrub_file(str(src), str(dst), fidelity)
    assert cli.exit_code(caught.value) != 1, caught.value
    assert not dst.exists()
    return caught.value


@pytest.mark.parametrize("seed,index", [(22, 299), (33, 353), (33, 689)])
def test_a_cr3_metadata_block_without_a_readable_ifd_is_refused(tmp_path, seed, index):
    """The residual check crashed on None (AttributeError) for a damaged CMT block or
    CTMD maker note; without the IFD the identity fields cannot be checked."""
    from . import raw_corpus as rc
    data = rc.cr3()
    if not _is_reference("cr3", data):
        _held_to_contract(tmp_path, _nth(data, seed, index))
        return
    assert isinstance(_cli_refuses(tmp_path, _nth(data, seed, index)), ParseError)


def test_a_jpeg_that_jpegtran_accepts_but_pillow_cannot_decode_is_a_parse_refusal(tmp_path):
    import shutil

    from . import corpus as imgc
    if shutil.which("jpegtran") is None:
        pytest.skip("jpegtran absent")
    data = imgc.build_torture_jpeg()
    if not _is_reference("torture_jpeg", data):
        _held_to_contract(tmp_path, _nth(data, 22, 698), "F2")
        return
    assert isinstance(_cli_refuses(tmp_path, _nth(data, 22, 698), "F2"), ParseError)


def test_a_pdf_stream_filter_name_that_is_not_text_is_refused():
    """Refused, not read as no match: a damaged /DCTDecode would skip the JPEG clean."""
    from src.scrub.formats.pdf import f1 as pdf_f1

    class Undecodable:
        def __str__(self):
            raise UnicodeDecodeError("utf-8", b"\xd4", 0, 1, "invalid continuation byte")

    class Stream(dict):
        pass
    with pytest.raises(ParseError, match="filter"):
        pdf_f1._filter_names(Stream({"/Filter": [Undecodable()]}))
