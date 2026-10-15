"""EMF and WMF at F1 (`docs/word_plan.md` §6, W2b). Each locus here is one the
survey of the metafiles inside 784 real Word documents found; the corpus plants it
the way Office writes it (`metafile_corpus.py`)."""
from __future__ import annotations

import io
import shutil
import struct
import subprocess

import pytest
from PIL import Image, ImageChops

from src.scrub.dispatch import default_dispatcher
from src.scrub.errors import ContentError, ParseError
from src.scrub.formats.metafile import emf, wmf
from tests.scrub import metafile_corpus as mc

U16 = "SENTINEL".encode("utf-16-le")


def _clean(blob: bytes) -> bool:
    return b"SENTINEL" not in blob and U16 not in blob


@pytest.mark.parametrize("locus", [l for l in mc.EMF_LOCI if mc.planted(1, l)])
def test_each_emf_locus_is_removed(locus):
    data = mc.emf(1, loci=(locus,))
    planted = mc.planted(1, locus)
    assert all(p in data for p in planted), "control: the locus is planted"
    out = emf.scrub(data)
    assert not [p for p in planted if p in out]
    assert emf.residuals(data) and emf.residuals(out) == []


@pytest.mark.parametrize("locus", mc.WMF_LOCI)
def test_each_wmf_locus_is_removed(locus):
    data = mc.wmf(1, loci=(locus,))
    planted = mc.planted(1, locus, kind="wmf")
    assert all(p in data for p in planted)
    out = wmf.scrub(data)
    assert not [p for p in planted if p in out]
    assert wmf.residuals(data) and wmf.residuals(out) == []


def test_every_locus_at_once_leaves_no_sentinel_in_either_encoding():
    for data, m in ((mc.emf(1), emf), (mc.wmf(1), wmf),
                    (mc.wmf(1, placeable=False), wmf)):
        assert not _clean(data)
        out = m.scrub(data)
        assert _clean(out) and m.scrub(out) == out


def test_variants_clean_to_the_same_bytes():
    assert len({emf.scrub(mc.emf(v)) for v in (1, 2, 3)}) == 1
    assert len({wmf.scrub(mc.wmf(v)) for v in (1, 2, 3)}) == 1


def test_every_drawing_record_is_kept_byte_for_byte():
    data = mc.emf(1)
    out = emf.scrub(data)
    assert emf._drawing(data) == emf._drawing(out)
    assert len(emf._drawing(out)) >= 6                    # brush, rect, bitmap, EOF...
    assert wmf._drawing(mc.wmf(1)) == wmf._drawing(wmf.scrub(mc.wmf(1)))


def test_a_file_with_nothing_to_remove_is_untouched():
    for data, m in ((mc.emf(1, loci=()), emf), (mc.wmf(1, loci=()), wmf)):
        assert m.scrub(data) == data


def test_a_wrong_record_count_is_corrected_and_nothing_else():
    """3 of 48 real EMFs left their EOF record out of `nRecords`."""
    data = bytearray(mc.emf(1, loci=()))
    n = struct.unpack_from("<I", data, 52)[0]
    struct.pack_into("<I", data, 52, n - 1)
    out = emf.scrub(bytes(data))
    assert struct.unpack_from("<I", out, 52)[0] == n
    assert out[:52] == bytes(data[:52]) and out[56:] == bytes(data[56:])


def _picture(data: bytes, object_id: int) -> bytes:
    """An EMF+ image object's bytes, its continuation chunks joined."""
    recs, _ = emf.records(data)
    whole = b""
    for t, s, _e in recs:
        if t != emf.EMR_GDICOMMENT or data[s + 12:s + 16] != b"EMF+":
            continue
        size = struct.unpack_from("<I", data, s + 8)[0]
        for ptype, flags, body, _ in emf._plus_records(data[s + 16:s + 12 + size]):
            if emf._is_image(ptype, flags) and flags & 0xFF == object_id:
                whole += body[4:] if flags & emf.CONTINUED or whole else body
    return whole


def test_a_picture_split_across_records_is_cleaned_and_its_pixels_kept():
    data = mc.emf(1, loci=("plus_image_continued",), chunk=300)
    before, after = _picture(data, 1), _picture(emf.scrub(data), 1)
    assert mc.secret("PHOTO-ARTIST", 1).encode() in before
    assert mc.secret("PHOTO-ARTIST", 1).encode() not in after
    a = Image.open(io.BytesIO(before[28:])).convert("RGB")
    b = Image.open(io.BytesIO(after[28:])).convert("RGB")
    assert ImageChops.difference(a, b).getbbox() is None


def test_the_embedded_emf_is_cleaned_and_rechunked_with_windows_checksum():
    data = mc.wmf(1, loci=("embedded_emf",), chunk=300)
    out = wmf.scrub(data)
    body = out[22:]                                       # past the placeable header
    assert sum(w for (w,) in struct.iter_unpack("<H", body)) & 0xFFFF == 0
    _, _, recs, _ = wmf._parse(out)
    chunks = [wmf._escape(out[s:e])[1] for f, s, e in recs if f == wmf.META_ESCAPE]
    whole, sizes = wmf._embedded_emf(chunks)
    assert emf.is_emf(whole) and _clean(whole) and emf.residuals(whole) == []
    assert all(n == 300 for n in sizes[:-1])              # the original chunk size
    checks = [struct.unpack_from("<H", c, 12)[0] for c in chunks]
    assert checks[0] and not any(checks[1:])              # first chunk only, as Word


def test_the_placeable_header_is_normalised_and_its_checksum_kept_valid():
    data = bytearray(mc.wmf(1, loci=()))
    struct.pack_into("<I", data, 16, 0x41424344)          # Reserved, used to hide data
    out = wmf.scrub(bytes(data))
    assert out[16:20] == b"\0\0\0\0"
    check = 0
    for (w,) in struct.iter_unpack("<H", out[:20]):
        check ^= w
    assert struct.unpack_from("<H", out, 20)[0] == check


# --- refusals: none of these appeared in the survey ------------------------------

def _emf_with(record: bytes) -> bytes:
    data = mc.emf(1, loci=())
    recs, _ = emf.records(data)
    eof = recs[-1][1]
    out = bytearray(data[:eof] + record + data[eof:])
    struct.pack_into("<II", out, 48, len(out), len(recs) + 1)
    return bytes(out)


@pytest.mark.parametrize("record,match", [
    (mc._comment(b"GDIC" + struct.pack("<I", 0x40000004) + bytes(16)), "multi-format"),
    (mc._rec(99, bytes(328)), "colour profile"),
    (mc._rec(107, bytes(16)), "printer driver"),
    (mc._comment(b"EMF+" + mc._plus(0x40FF, 0, b"")), "unknown record type"),
    (mc._rec(200, bytes(8)), "unknown record type"),
])
def test_what_has_no_rule_is_refused(record, match):
    with pytest.raises(ContentError, match=match):
        emf.scrub(_emf_with(record))


def test_a_bitmap_linking_a_colour_profile_is_refused():
    v5 = bytearray(124)
    struct.pack_into("<I", v5, 0, 124)
    struct.pack_into("<I", v5, 56, emf.PROFILE_LINKED)
    record = mc._rec(81, struct.pack("<iiii", 0, 0, 1, 1) + struct.pack(
        "<iiiiiiIIIIIIii", 0, 0, 0, 0, 1, 1, 80, 124, 204, 4, 0, 0x00CC0020, 1, 1)
        + bytes(v5) + bytes(4))
    with pytest.raises(ContentError, match="colour profile"):
        emf.scrub(_emf_with(record))


def test_a_wmf_escape_other_than_a_comment_is_refused():
    data = mc.wmf(1, loci=())
    eof = len(data) - 6
    out = data[:eof] + mc._wrec(0x0626, struct.pack("<HH", 0x1013, 4) + b"%!PS") + data[eof:]
    with pytest.raises(ContentError, match="escape"):
        wmf.scrub(out)


@pytest.mark.parametrize("damage", ["no_eof", "huge_record"])
def test_damaged_files_are_parse_errors(damage):
    data = bytearray(mc.emf(1, loci=()))
    if damage == "no_eof":
        data = data[:-20]
    else:
        struct.pack_into("<I", data, 112, 0x7FFFFFF0)
    with pytest.raises(ParseError):
        emf.scrub(bytes(data))


def test_dispatch_claims_metafiles_and_nothing_that_only_starts_like_one():
    d = default_dispatcher()
    for data in (mc.emf(1), mc.wmf(1), mc.wmf(1, placeable=False)):
        assert d.resolve(data).format_id == "metafile"
    with pytest.raises(Exception, match="no handler"):
        d.resolve(b"\x01\x00\x00\x00" + bytes(200))


# --- content: LibreOffice draws the picture the same before and after ------------

@pytest.mark.skipif(not shutil.which("soffice"), reason="needs LibreOffice")
@pytest.mark.parametrize("kind", ["emf", "wmf"])
def test_libreoffice_draws_the_same_picture(kind, tmp_path):
    """Converted by Draw, the picture alone. (Inside a Word document, Writer's page
    layout moves by a pixel or two between runs of the SAME file -- 2 renders in 6,
    measured -- so a page render cannot tell a change from noise; Draw's render of
    the picture was the same 6 times in 6.) The EMF draws both EMF+ pictures,
    including the one split across records, a GDI bitmap and a rectangle; the WMF
    draws through the EMF embedded in it."""
    from src.scrub.formats.docx.f3 import profile_arg
    data = mc.emf(1) if kind == "emf" else mc.wmf(1)
    m = emf if kind == "emf" else wmf
    for label, blob in (("in", data), ("out", m.scrub(data))):
        (tmp_path / f"{label}.{kind}").write_bytes(blob)
    subprocess.run(["soffice", "--headless", profile_arg(str(tmp_path)), "--convert-to",
                    "png", "--outdir", str(tmp_path), str(tmp_path / f"in.{kind}"),
                    str(tmp_path / f"out.{kind}")],
                   capture_output=True, timeout=180, check=False)
    assert (tmp_path / "in.png").exists(), "Draw wrote nothing: is libreoffice-draw installed?"
    before = Image.open(tmp_path / "in.png").convert("RGB")
    after = Image.open(tmp_path / "out.png").convert("RGB")
    assert len(before.getcolors(1 << 20) or []) > 8, "control: the picture is drawn"
    assert before.size == after.size and ImageChops.difference(before, after).getbbox() is None


# --- the real ones: every metafile inside 784 real Word documents ----------------

@pytest.mark.skipif(not mc.survey_samples(), reason="the survey's metafiles are not "
                    "on this machine (local research data, never in the repo)")
def test_every_metafile_from_real_word_documents():
    """409 files (48 EMF, 361 WMF). Each cleans; a second clean finds nothing; one
    with nothing to remove comes back byte for byte -- but for a record count that
    left out the end record (3 EMFs), corrected; and the two WMFs carrying their
    source EMF come back as Word wrote them, chunks and checksum included."""
    counts = {"cleaned": 0, "untouched": 0, "count fixed": 0}
    for path in mc.survey_samples():
        data = open(path, "rb").read()
        m = emf if emf.is_emf(data) else wmf
        out, removed = m.scrub_with_report(data)
        assert m.residuals(out) == [] and m.scrub(out) == out, path
        if removed:
            counts["cleaned"] += 1
        elif out == data:
            counts["untouched"] += 1
        else:
            assert m is emf and out[:52] == data[:52] and out[56:] == data[56:], path
            counts["count fixed"] += 1
    assert counts == {"cleaned": 32, "untouched": 374, "count fixed": 3}, counts
