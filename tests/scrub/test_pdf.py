"""PDF: walker, serializer, content tokenizer, and the recursive F1 tier.

The torture document is the centre of this file. Every assertion that matters is
"this specific locus is empty afterwards", because a PDF scrub that only clears
`/Info` passes a naive check while leaving XMP on an image, a page thumbnail, and a
full JPEG sitting inside a content stream.
"""
from __future__ import annotations

import subprocess

import pikepdf
import pytest

from src.scrub import cli
from src.scrub.errors import ContentError, FidelityError, ParseError
from src.scrub.formats.pdf import content as ct
from src.scrub.formats.pdf import f1
from src.scrub.formats.pdf import serialize as ser
from src.scrub.formats.pdf import walker as w
from tests.scrub import corpus as jpeg_corpus
from tests.scrub import pdf_corpus as pc


@pytest.fixture(scope="module")
def torture(tmp_path_factory):
    return pc.torture_pdf(str(tmp_path_factory.mktemp("pdf") / "torture.pdf"))


def _all_bytes(path: str) -> list[bytes]:
    """The file plus every decoded stream — where a surviving secret could hide."""
    with open(path, "rb") as f:
        blobs = [f.read()]
    with pikepdf.open(path) as pdf:
        for obj in pdf.objects:
            if isinstance(obj, pikepdf.Stream):
                try:
                    blobs.append(obj.read_bytes())
                except Exception:
                    blobs.append(obj.read_raw_bytes())
    return blobs


# --------------------------------------------------------------------------- #
# Walker
# --------------------------------------------------------------------------- #
def test_walker_counts_revisions_and_superseded_objects(tmp_path):
    p = pc.incremental_pdf(str(tmp_path / "h.pdf"), n_revisions=3)
    layout = w.walk(open(p, "rb").read())
    assert len(layout.revisions) == 3
    assert [r.prev for r in layout.revisions][0] is None
    assert layout.superseded == {5: 2, 6: 2}
    assert not layout.hybrid and not layout.encrypted


def test_walker_refuses_junk_before_the_header(tmp_path):
    """Offsets in a junk-prefixed file are header-relative, so every object would
    resolve to the wrong place. Refuse rather than guess."""
    p = pc.incremental_pdf(str(tmp_path / "h.pdf"))
    data = b"JUNK" * 4 + open(p, "rb").read()
    with pytest.raises(ParseError, match="before the %PDF header"):
        w.walk(data)


def test_walker_refuses_content_after_the_final_eof(tmp_path):
    p = pc.incremental_pdf(str(tmp_path / "h.pdf"))
    data = open(p, "rb").read() + b"HITCHHIKER"
    with pytest.raises(ParseError, match="after the final"):
        w.walk(data)


def test_walker_knows_it_cannot_see_inside_object_streams(tmp_path):
    """`ObjStm` holds objects compressed inside another object, so a byte scan cannot
    enumerate them. Reporting a ledger from such a file as complete would be a
    falsely clean result, so the walker says which it is."""
    src = pc.incremental_pdf(str(tmp_path / "h.pdf"))
    packed = str(tmp_path / "packed.pdf")
    with pikepdf.open(src) as pdf:
        pdf.save(packed, object_stream_mode=pikepdf.ObjectStreamMode.generate)
    layout = w.walk(open(packed, "rb").read())
    assert layout.object_streams > 0
    assert not layout.objects_fully_enumerated


# --------------------------------------------------------------------------- #
# Serializer
# --------------------------------------------------------------------------- #
def test_serializer_emits_no_producer_identity(torture):
    out = ser.serialize(pikepdf.open(torture))
    layout = w.walk(out)
    assert layout.binary_comment is None, (
        "the binary header comment is a per-producer constant; substituting our own "
        "would only make it ours")
    assert layout.header == b"%PDF-1.7", "the version header must be pinned, not inherited"
    trailer = out[out.rfind(b"trailer"):]
    assert b"/ID" not in trailer, "/ID[0] is inherited through a qpdf rewrite (W0)"
    assert b"/Info" not in trailer and b"/Prev" not in trailer


def test_serializer_is_deterministic(torture):
    assert ser.serialize(pikepdf.open(torture)) == ser.serialize(pikepdf.open(torture))


def test_serializer_output_has_no_orphans(torture):
    """The accounting ledger turned on our own writer: an object we emit that nothing
    references is exactly the residue this phase exists to remove."""
    out = ser.serialize(pikepdf.open(torture))
    import re
    defined = {int(m.group(1)) for m in re.finditer(rb"\n(\d+) 0 obj", out)}
    referenced = {int(m.group(1)) for m in re.finditer(rb"(\d+) 0 R", out)}
    assert defined - referenced == set()


# --------------------------------------------------------------------------- #
# Content tokenizer
# --------------------------------------------------------------------------- #
def test_tokenizer_reads_operators_with_their_operands():
    ops = ct.operations(b"BT /F1 14 Tf 72 720 Td (a \\(nested\\) string) Tj ET\n"
                        b"60 710 300 30 re f\n")
    assert [o.operator for o in ops] == [b"BT", b"Tf", b"Td", b"Tj", b"ET", b"re", b"f"]
    assert [t.raw for t in ops[1].operands] == [b"/F1", b"14"]


def test_inline_image_terminator_is_not_confused_by_its_own_payload():
    """`EI` occurs inside real JPEG data often enough to matter — it did on the first
    torture file built here. Terminating there truncates the image *and* mis-parses
    the rest of the page, so the scan starts after the JPEG's own EOI."""
    jpeg = jpeg_corpus.build_torture_jpeg()
    stream = (b"q 40 0 0 40 72 500 cm\nBI /W 8 /H 8 /CS /RGB /F /DCTDecode ID "
              + jpeg + b"\nEI Q\n")
    images = ct.inline_images(stream)
    assert len(images) == 1
    assert images[0].is_jpeg
    assert images[0].data == jpeg, "the payload must come back whole, trailer included"


def test_inline_image_rewrite_keeps_the_stream_parseable():
    jpeg = jpeg_corpus.build_torture_jpeg()
    stream = b"BI /W 8 /H 8 /CS /RGB /F /DCTDecode ID " + jpeg + b"\nEI\n"
    from src.scrub.formats.jpeg import f1 as jpeg_f1
    rebuilt = ct.replace_inline_images(stream, lambda img: jpeg_f1.scrub(img.data))
    assert len(ct.inline_images(rebuilt)) == 1
    assert ct.inline_images(rebuilt)[0].data == jpeg_f1.scrub(jpeg)


# --------------------------------------------------------------------------- #
# F1 — the tier
# --------------------------------------------------------------------------- #
def test_control_the_torture_file_carries_every_locus(torture):
    """CONTROL: if the corpus does not actually contain the secrets, every assertion
    below passes for free."""
    blobs = _all_bytes(torture)
    missing = [s for s in pc.TORTURE_SECRETS if not any(s in b for b in blobs)]
    assert missing == [], f"corpus is missing loci: {missing}"


def test_f1_clears_every_locus(torture, tmp_path):
    out = tmp_path / "scrubbed.pdf"
    cli.scrub_file(torture, str(out), "F1")
    blobs = _all_bytes(str(out))
    survivors = [s.decode("latin-1") for s in pc.TORTURE_SECRETS
                 if any(s in b for b in blobs)]
    assert survivors == [], f"secret survived the scrub: {survivors}"


def test_f1_preserves_the_visible_document(torture, tmp_path):
    out = tmp_path / "scrubbed.pdf"
    cli.scrub_file(torture, str(out), "F1")
    assert pc.pdftotext(str(out)) == pc.pdftotext(torture)


def test_f1_recurses_into_the_embedded_jpeg_without_touching_its_pixels(torture, tmp_path):
    """The invariant is not "raw stream bytes identical" — that would leave the
    embedded JPEG's EXIF and thumbnail in place. It is: the leaf's content-bearing
    payload is bit-identical per that leaf's own F1."""
    out = tmp_path / "scrubbed.pdf"
    cli.scrub_file(torture, str(out), "F1")
    with pikepdf.open(torture) as before, pikepdf.open(str(out)) as after:
        xb = before.pages[0].obj.Resources.XObject.Im0.read_raw_bytes()
        xa = after.pages[0].obj.Resources.XObject.Im0.read_raw_bytes()
    assert xa != xb, "the embedded JPEG's metadata should be gone"
    assert f1._jpeg_scan(xa) == f1._jpeg_scan(xb), "its scan must be byte-identical"


def test_f1_clears_the_inline_image_that_no_object_walk_can_see(torture, tmp_path):
    out = tmp_path / "scrubbed.pdf"
    cli.scrub_file(torture, str(out), "F1")
    with pikepdf.open(str(out)) as pdf:
        body = pdf.pages[0].obj.Contents.read_bytes()
    images = ct.inline_images(body)
    assert len(images) == 1 and images[0].is_jpeg
    from src.scrub.formats.jpeg import f1 as jpeg_f1
    assert jpeg_f1.residuals(images[0].data) == []
    assert b"TestCam" not in images[0].data


def test_f1_collapses_history_and_leaves_nothing_recoverable(tmp_path):
    src = pc.incremental_pdf(str(tmp_path / "h.pdf"), n_revisions=4)
    out = tmp_path / "scrubbed.pdf"
    cli.scrub_file(src, str(out), "F1")
    layout = w.walk(out.read_bytes())
    assert len(layout.revisions) == 1 and not layout.superseded
    data = out.read_bytes()
    assert b"CONFIDENTIAL" not in data
    assert pc.pdftotext(str(out)) == pc.pdftotext(src)


def test_f1_is_deterministic_across_processes(torture, tmp_path):
    """`InProcessScrubber` runs its repeats in one interpreter, so a code path that
    iterated a `set` to decide output *order* would look perfectly deterministic to
    the harness floor and differ on the next CLI invocation. PDF has more
    string-keyed iteration than any format so far, so this is checked out of
    process, with the hash seed varied."""
    import os
    import sys
    digests = set()
    for seed in ("0", "1", "424242"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        out = tmp_path / f"seed{seed}.pdf"
        subprocess.run([sys.executable, "-m", "src.scrub", "--fidelity", "F1",
                        torture, str(out)], check=True, env=env, capture_output=True)
        digests.add(out.read_bytes())
    assert len(digests) == 1, "output differs between interpreters"


@pytest.mark.parametrize("fidelity", ["F2", "F3"])
def test_unbuilt_tiers_refuse_rather_than_silently_downgrade(torture, tmp_path, fidelity):
    with pytest.raises(FidelityError):
        cli.scrub_file(torture, str(tmp_path / "o.pdf"), fidelity)
    assert not (tmp_path / "o.pdf").exists()


# --------------------------------------------------------------------------- #
# Fail-closed cases
# --------------------------------------------------------------------------- #
def test_encrypted_pdf_is_refused(tmp_path):
    src = pc.incremental_pdf(str(tmp_path / "h.pdf"))
    enc = tmp_path / "enc.pdf"
    with pikepdf.open(src) as pdf:
        pdf.save(enc, encryption=pikepdf.Encryption(owner="o", user="u"))
    with pytest.raises(ParseError, match="encrypted"):
        f1.scrub(enc.read_bytes())


def test_signed_document_is_refused_rather_than_silently_invalidated(tmp_path):
    src = pc.incremental_pdf(str(tmp_path / "h.pdf"))
    signed = tmp_path / "signed.pdf"
    with pikepdf.open(src) as pdf:
        pdf.Root.AcroForm = pikepdf.Dictionary(
            SigFlags=3, Fields=pikepdf.Array([]))
        pdf.save(signed)
    with pytest.raises(ParseError, match="signed"):
        f1.scrub(signed.read_bytes())


def test_embedded_attachment_is_refused_until_recursion_exists(tmp_path):
    src = pc.incremental_pdf(str(tmp_path / "h.pdf"))
    attached = tmp_path / "attached.pdf"
    with pikepdf.open(src) as pdf:
        pdf.Root.Names = pikepdf.Dictionary(
            EmbeddedFiles=pikepdf.Dictionary(Names=pikepdf.Array([])))
        pdf.save(attached)
    with pytest.raises(ParseError, match="attachment"):
        f1.scrub(attached.read_bytes())


def test_content_change_fails_closed(torture, monkeypatch, tmp_path):
    """If the scrub ever altered a page's marks, the tier must raise and write no
    file rather than ship a changed document."""
    real = f1.ct.replace_inline_images

    def corrupt(data, rewrite):
        return real(data, rewrite) + b"\n1 0 0 RG\n"

    monkeypatch.setattr(f1.ct, "replace_inline_images", corrupt)
    with pytest.raises(ContentError):
        f1.scrub(open(torture, "rb").read())


# --------------------------------------------------------------------------- #
# Real-world producers
# --------------------------------------------------------------------------- #
def test_scrubs_a_real_multipage_report(tmp_path):
    """`docs/p1_report.pdf` is a real 295 KB Skia-produced document with 800+ objects,
    embedded fonts and a structure tree — the case a synthetic corpus cannot stand in
    for."""
    out = tmp_path / "report.pdf"
    cli.scrub_file("docs/p1_report.pdf", str(out), "F1")
    assert pc.pdftotext(str(out)) == pc.pdftotext("docs/p1_report.pdf")
    layout = w.walk(out.read_bytes())
    assert len(layout.revisions) == 1 and layout.binary_comment is None
