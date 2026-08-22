"""PDF: walker, serializer, content tokenizer, and the F1 and F2 tiers.

The torture document is the centre of this file. Every assertion that matters is
"this specific locus is empty afterwards", because a PDF scrub that only clears
`/Info` passes a naive check while leaving XMP on an image, a page thumbnail, and a
full JPEG sitting inside a content stream.
"""
from __future__ import annotations

import io
import pathlib
import subprocess

import pikepdf
import pytest

from src.scrub import cli
from src.scrub.errors import ContentError, FidelityError, ParseError
from src.scrub.formats.pdf import canon, f1, f2
from src.scrub.formats.pdf import content as ct
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


def test_unbuilt_tiers_refuse_rather_than_silently_downgrade(torture, tmp_path):
    """F3 is M5. A tier that does not exist must refuse, never quietly hand back the
    next tier down — a caller who asked for rasterisation and got a structural rewrite
    would believe the text layer was gone when it is still fully extractable."""
    with pytest.raises(FidelityError):
        cli.scrub_file(torture, str(tmp_path / "o.pdf"), "F3")
    assert not (tmp_path / "o.pdf").exists()


# --------------------------------------------------------------------------- #
# F2: content-stream canonicalisation
# --------------------------------------------------------------------------- #
def test_canon_collapses_two_spellings_of_the_same_page():
    """The whole premise of the tier, at its smallest. Two producers write the same
    line four ways — `Td` with integers and one `Tj` per word, versus `Tm` with
    trailing zeros and `TJ` arrays — and canonicalisation must make them one file."""
    td_style = b"BT /F1 12 Tf 14 TL 72 720 Td (Hello) Tj ( world) Tj T* (two) Tj ET"
    tm_style = (b"BT /F1 12.00 Tf 1 0 0 1 72.0 720.000 Tm [(Hello)] TJ [( world)] TJ "
                b"1 0 0 1 72 706 Tm [(two)] TJ ET")
    assert canon.canonicalize(td_style) == canon.canonicalize(tm_style)


@pytest.mark.parametrize("source", [
    b"BT /F1 12 Tf 16 TL 72 700 Td (a) ' 3 1 (b) \" (c) ' ET",
    b"BT 20 TL 10 700 Td (x) Tj ET q BT 5 TL 10 600 Td (y) Tj T* (z) Tj ET Q "
    b"BT 10 500 Td T* (w) Tj ET",
    rb"BT 0 0 Td <48656C6C6F> Tj (A\(B\101) Tj ET",
    b"BT 12 0 0 12 5 5 Tm 2 3 Td (a) Tj -1 -1 Td (b) Tj ET",
    b"q 1 0 0 RG 10 10 100 100 re f Q",
    b"q 1 0 0 1 0 0 cm BI /W 1 /H 1 /CS /G /BPC 8 /L 1 ID \x00 EI Q BT 0 0 Td (t) Tj ET",
    b"",
])
def test_canon_preserves_what_the_stream_paints(source):
    out = canon.canonicalize(source)
    assert canon.painted(source) == canon.painted(out)
    assert canon.canonicalize(out) == out, "canonicalisation must be a fixed point"


@pytest.mark.parametrize("mutation,replacement", [
    (b"(Hello)", b"(Hellp)"),                     # a glyph changed
    (b"1 0 0 1 72 700 Tm", b"1 0 0 1 72 701 Tm"),  # a line moved
    (b"1 0 0 1 72 684 Tm", b"1 0 0 1 72 700 Tm"),  # leading swallowed
    (b"5 5 10 10 re\n", b""),                     # a drawing operator dropped
])
def test_the_paint_invariant_is_not_vacuous(mutation, replacement):
    """A self-check that passes on everything proves nothing. Each mutation is a real
    content change, and `painted()` has to see every one of them — otherwise the F2
    content promise is an assertion that cannot fail."""
    source = (b"BT /F1 12 Tf 16 TL 72 700 Td (Hello) Tj T* (world) Tj ET "
              b"1 0 0 RG 5 5 10 10 re f")
    good = canon.canonicalize(source)
    broken = good.replace(mutation, replacement)
    assert broken != good, "the mutation did not apply — the test would be vacuous"
    assert canon.painted(broken) != canon.painted(source)


def test_canon_leaves_no_relative_positioning_behind():
    """`Td`, `TD`, `T*` and `TL` are four ways of saying where the next line starts,
    and which one a producer reaches for is spelling. After canonicalisation only the
    absolute `Tm` should remain — otherwise the choice is still in the file."""
    source = (b"BT /F1 12 Tf 14 TL 72 720 Td (a) Tj T* (b) Tj 5 -20 TD (c) Tj ET")
    out = canon.canonicalize(source)
    ops = {o.operator for o in ct.operations(out)}
    assert not (ops & canon.REWRITTEN_AWAY), f"relative positioning survived: {ops}"
    assert b"Tm" in out


def test_canon_refuses_a_form_whose_leading_is_inherited():
    """A Form XObject starts in its caller's text state, so a `T*` with no `TL` of its
    own cannot be resolved to an absolute position. Refusing is the only correct
    answer; guessing zero silently moves every line in the form."""
    form = b"BT 0 0 Td (a) Tj T* (b) Tj ET"
    with pytest.raises(ParseError, match="inherited from the caller"):
        canon.canonicalize(form, inherits=True)
    assert f2.canonical_or_none(form, inherits=True) is None, (
        "one unresolvable stream must be left alone, not fail the whole document")


def test_canon_invents_no_spacing_operator_for_an_invoked_stream():
    """`0 Tw 0 Tc` before every run was the first implementation, and it put a
    constant into every file we emit — the FLAC empty-comment mistake. A stream that
    never sets spacing must come out with no spacing operator at all."""
    out = canon.canonicalize(b"BT /F1 12 Tf 0 0 Td (in a form) Tj ET", inherits=True)
    assert b"Tw" not in out and b"Tc" not in out


def test_f2_clears_every_locus_f1_does(torture, tmp_path):
    out = tmp_path / "f2.pdf"
    cli.scrub_file(torture, str(out), "F2")
    blobs = _all_bytes(str(out))
    survivors = [s.decode("latin-1") for s in pc.TORTURE_SECRETS
                 if any(s in b for b in blobs)]
    assert survivors == [], f"secret survived F2: {survivors}"
    assert f2.residuals(out.read_bytes()) == []


def test_f2_renders_the_same_page(torture, tmp_path):
    """The content promise, checked in pixel space rather than through our own
    invariant. `painted()` and the rewriter share a state machine, so agreeing with
    each other proves less than agreeing with poppler."""
    pytest.importorskip("shutil")
    import shutil
    if not shutil.which("pdftoppm"):
        pytest.skip("poppler not installed")
    out = tmp_path / "f2.pdf"
    cli.scrub_file(torture, str(out), "F2")

    def render(path):
        stem = str(tmp_path / ("r_" + pathlib.Path(path).stem))
        subprocess.run(["pdftoppm", "-r", "150", "-png", "-singlefile", path, stem],
                       check=True, capture_output=True)
        return pathlib.Path(stem + ".png").read_bytes()

    assert render(str(out)) == render(torture)
    assert pc.pdftotext(str(out)) == pc.pdftotext(torture)


def test_f2_normalises_the_compression_filter(torture, tmp_path):
    """One filter and one deflate level for everything we may decode, so neither is a
    producer tell. Level 6 rather than 9 is measured, not assumed: it is what four of
    the five peer producers emit, and 9 is what none of them emits."""
    out = tmp_path / "f2.pdf"
    cli.scrub_file(torture, str(out), "F2")
    with pikepdf.open(str(out)) as pdf:
        for obj in pdf.objects:
            if not isinstance(obj, pikepdf.Stream):
                continue
            filters = f1._filter_names(obj)
            if set(filters) & f2._KEEP_FILTER:
                continue
            assert filters == ["/FlateDecode"], f"stray filter {filters}"
            assert obj.read_raw_bytes()[:2] == b"\x78\x9c", "deflate level is not 6"


def test_f2_normalises_font_subset_tags(tmp_path):
    """`/ABCDEF+Helvetica` carries an arbitrary per-producer tag. Reassigning it is
    lossless — ISO 32000 §9.6.4 asks only that it be unique within the document."""
    src = tmp_path / "sub.pdf"
    with pikepdf.new() as pdf:
        page = pdf.add_blank_page(page_size=(200, 200))
        page.obj.Contents = pikepdf.Stream(pdf, b"BT /F1 12 Tf 10 100 Td (hi) Tj ET")
        font = pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
            BaseFont=pikepdf.Name("/QWERTY+Helvetica")))
        page.obj.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
        pdf.save(src)
    out = f2.scrub(src.read_bytes())
    assert b"QWERTY+" not in out
    assert b"AAAAAA+Helvetica" in out


def test_f2_merges_a_multi_stream_page(tmp_path):
    """How a producer chops a page into content streams is style with no rendering
    meaning — ISO 32000 §7.8.2 says the reader concatenates them."""
    src = tmp_path / "split.pdf"
    with pikepdf.new() as pdf:
        page = pdf.add_blank_page(page_size=(200, 200))
        page.obj.Contents = pikepdf.Array([
            pdf.make_stream(b"BT /F1 12 Tf 10 100 Td (a)"),
            pdf.make_stream(b" Tj ET")])
        page.obj.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(
            F1=pdf.make_indirect(pikepdf.Dictionary(
                Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
                BaseFont=pikepdf.Name.Helvetica))))
        pdf.save(src)
    with pikepdf.open(io.BytesIO(f2.scrub(src.read_bytes()))) as after:
        assert isinstance(after.pages[0].obj.get("/Contents"), pikepdf.Stream)


def test_f2_is_deterministic(torture):
    raw = pathlib.Path(torture).read_bytes()
    assert len({f2.scrub(raw) for _ in range(5)}) == 1


def test_f2_content_change_fails_closed(torture, monkeypatch, tmp_path):
    """If the rewrite ever paints something else, the tier must raise rather than
    ship a file whose page we cannot vouch for."""
    monkeypatch.setattr(canon, "canonicalize",
                        lambda data, inherits=False: b"BT /F1 12 Tf 0 0 Td (X) Tj ET")
    with pytest.raises(ContentError):
        f2.scrub(pathlib.Path(torture).read_bytes())


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
