"""PDF: walker, serializer, content tokenizer, all three tiers, and the advisory.

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
from src.scrub.formats.pdf import canon, f1, f2, f3, redaction
from src.scrub.formats.pdf import content as ct
from src.scrub.formats.pdf import serialize as ser
from src.scrub.formats.pdf import walker as w
from tests.scrub import corpus as jpeg_corpus
from tests.scrub import pdf_corpus as pc


@pytest.fixture(scope="module")
def torture(tmp_path_factory):
    return pc.torture_pdf(str(tmp_path_factory.mktemp("pdf") / "torture.pdf"))


def _need_poppler() -> None:
    """Skip when poppler is absent.

    Locally this is a convenience. In CI it must never fire: `tests/test_ci_contract.py`
    asserts the workflow installs poppler, precisely so that a missing tool cannot
    turn PDF content verification into a silent skip while the build stays green.
    """
    if not pc.HAVE_POPPLER:
        pytest.skip("poppler not installed (pdftotext/pdftoppm)")


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
    _need_poppler()
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
    _need_poppler()
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


def test_an_unknown_fidelity_is_refused(torture, tmp_path):
    """All three tiers exist now, so the fail-closed case is a bad tier name — it must
    still raise rather than quietly fall back to a tier the caller did not ask for."""
    with pytest.raises(FidelityError):
        cli.scrub_file(torture, str(tmp_path / "o.pdf"), "F4")
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
    _need_poppler()
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
    _need_poppler()
    """`docs/p1_report.pdf` is a real 295 KB Skia-produced document with 800+ objects,
    embedded fonts and a structure tree — the case a synthetic corpus cannot stand in
    for."""
    out = tmp_path / "report.pdf"
    cli.scrub_file("docs/p1_report.pdf", str(out), "F1")
    assert pc.pdftotext(str(out)) == pc.pdftotext("docs/p1_report.pdf")
    layout = w.walk(out.read_bytes())
    assert len(layout.revisions) == 1 and layout.binary_comment is None


# --------------------------------------------------------------------------- #
# F3: rasterise
# --------------------------------------------------------------------------- #
def _render(path: str, out_dir, dpi: int = 150) -> bytes:
    stem = str(out_dir / ("r_" + pathlib.Path(path).stem))
    subprocess.run(["pdftoppm", "-r", str(dpi), "-png", "-singlefile", path, stem],
                   check=True, capture_output=True)
    return pathlib.Path(stem + ".png").read_bytes()


def test_f3_destroys_the_text_layer(torture, tmp_path):
    """The tier's defining cost, asserted rather than described. If text still comes
    out, the page was not actually rasterised and the file only looks scrubbed."""
    _need_poppler()
    out = tmp_path / "f3.pdf"
    cli.scrub_file(torture, str(out), "F3")
    assert pc.pdftotext(torture).strip() != ""
    assert pc.pdftotext(str(out)).strip() == ""


def test_f3_preserves_the_visible_page(torture, tmp_path):
    """Hard constraint #1 still applies: what the reader *sees* must survive. The gate
    is perceptual, not bit-exact — F3 is a lossy tier and the page is now a JPEG."""
    _need_poppler()
    from PIL import Image
    out = tmp_path / "f3.pdf"
    cli.scrub_file(torture, str(out), "F3")

    before = Image.open(io.BytesIO(_render(torture, tmp_path))).convert("RGB")
    after = Image.open(io.BytesIO(_render(str(out), tmp_path))).convert("RGB")
    assert before.size == after.size, "the page changed dimensions"
    diff = [abs(a - b)
            for pa, pb in zip(before.getdata(), after.getdata(), strict=True)
            for a, b in zip(pa, pb, strict=True)]
    assert sum(diff) / len(diff) < 2.0, "the rendered page changed materially"


def test_f3_leaves_no_font_or_text_operator(torture, tmp_path):
    _need_poppler()
    out = tmp_path / "f3.pdf"
    cli.scrub_file(torture, str(out), "F3")
    assert f3.residuals(out.read_bytes()) == []
    with pikepdf.open(str(out)) as pdf:
        for page in pdf.pages:
            assert "/Font" not in (page.obj.get("/Resources") or {})
            assert b"BT" not in page.obj.Contents.read_bytes()


def test_f3_page_count_and_geometry_survive(tmp_path):
    """Page count is content; page size is content. A tier that silently dropped a
    page would still pass every "is the metadata gone" check."""
    _need_poppler()
    src = pc.incremental_pdf(str(tmp_path / "multi.pdf"))
    out = tmp_path / "f3.pdf"
    cli.scrub_file(src, str(out), "F3")
    with pikepdf.open(src) as a, pikepdf.open(str(out)) as b:
        assert len(a.pages) == len(b.pages)
        wa = float(a.pages[0].obj.MediaBox[2]) - float(a.pages[0].obj.MediaBox[0])
        wb = float(b.pages[0].obj.MediaBox[2]) - float(b.pages[0].obj.MediaBox[0])
        # Rounded up to the render pixel grid, so within one pixel at the render DPI.
        assert abs(wa - wb) <= 72.0 / f3.DEFAULT_DPI


def test_f3_is_deterministic(torture):
    _need_poppler()
    raw = pathlib.Path(torture).read_bytes()
    assert len({f3.scrub(raw) for _ in range(3)}) == 1


def test_f3_refuses_a_nonsensical_resolution(torture):
    with pytest.raises(ParseError):
        f3.scrub(pathlib.Path(torture).read_bytes(), dpi=0)


# --------------------------------------------------------------------------- #
# W7: the redaction advisory
# --------------------------------------------------------------------------- #
def _one_page(body: bytes, tmp_path) -> bytes:
    with pikepdf.new() as pdf:
        page = pdf.add_blank_page(page_size=(612, 792))
        page.obj.Contents = pikepdf.Stream(pdf, body)
        page.obj.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(
            F1=pdf.make_indirect(pikepdf.Dictionary(
                Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
                BaseFont=pikepdf.Name.Helvetica))))
        buf = io.BytesIO()
        pdf.save(buf)
    return buf.getvalue()


@pytest.mark.parametrize("body,expected", [
    (b"BT /F1 12 Tf 72 700 Td (nothing to hide) Tj ET", []),
    (b"BT /F1 12 Tf 100 700 Td (Agent Smith) Tj ET 0 g 90 690 200 30 re f",
     ["text_under_fill"]),
    (b"BT /F1 12 Tf 3 Tr 72 700 Td (hidden identifier) Tj ET", ["invisible_text"]),
    (b"BT /F1 12 Tf 72 -400 Td (off the page) Tj ET", ["text_outside_crop"]),
])
def test_redaction_detector_finds_the_three_patterns(body, expected, tmp_path):
    risks = redaction.detect(_one_page(body, tmp_path))
    assert [r.kind for r in risks] == expected


@pytest.mark.parametrize("body", [
    # Stroked, not filled: an outline does not hide anything underneath it.
    b"BT /F1 12 Tf 100 700 Td (Agent Smith) Tj ET 90 690 200 30 re S",
    # Filled, but nowhere near the text.
    b"BT /F1 12 Tf 100 700 Td (Agent Smith) Tj ET 0 g 90 100 200 30 re f",
    # Filled over the text, but drawn BEFORE it — that is a highlight, not a cover.
    b"0 g 90 690 200 30 re f BT /F1 12 Tf 100 700 Td (Agent Smith) Tj ET",
])
def test_redaction_detector_stays_quiet_when_it_should(body, tmp_path):
    """A warning that fires on ordinary drawing gets ignored, and then it protects
    nobody. Under-reporting is the chosen failure direction, so these must be silent."""
    assert redaction.detect(_one_page(body, tmp_path)) == []


@pytest.mark.parametrize("body", [
    # A Chrome-printed page opens exactly like this: scale down, flip the y axis,
    # then work in the transformed space. The text is on the page; only its *raw* Tm
    # translation looks off-page.
    b".24 0 0 -.24 0 792 cm q 3.125 0 0 3.125 187 212 cm "
    b"BT /F1 12 Tf 0 88 Td (ordinary body text) Tj ET Q",
    # A simple translation, same principle.
    b"1 0 0 1 100 400 cm BT /F1 12 Tf 0 0 Td (also on the page) Tj ET",
])
def test_redaction_detector_respects_the_transformation_matrix(body, tmp_path):
    """Text is placed by the text matrix *and* the CTM, so `Tm`'s own translation is
    not a page coordinate.

    This is a regression test for a real false positive, and for why it survived:
    every synthetic case above uses an identity CTM, so none of them exercised the
    composition. The first run against a real Chrome-printed document reported that
    all the text on two pages sat outside the crop box — a warning firing on ordinary
    output, which is the one failure mode this detector must not have.
    """
    assert redaction.detect(_one_page(body, tmp_path)) == []


def test_redaction_warning_does_not_fail_the_scrub(tmp_path):
    """The advisory is about the *input*; the scrub still did what it promised. A
    redaction risk must never cost the user a working scrub."""
    src = pc.redacted_pdf(str(tmp_path / "redacted.pdf"))
    out = tmp_path / "out.pdf"
    advisories = cli.scrub_file(src, str(out), "F1")
    assert out.exists()
    assert any("redaction" in a for a in advisories)


def test_the_scrub_really_does_preserve_the_hidden_text(tmp_path):
    _need_poppler()
    """The uncomfortable fact the advisory exists to state, asserted so it cannot
    quietly stop being true: every tier preserves content, so the words under the box
    survive the scrub. If this ever fails, the tier started destroying content."""
    src = pc.redacted_pdf(str(tmp_path / "redacted.pdf"))
    out = tmp_path / "out.pdf"
    cli.scrub_file(src, str(out), "F1")
    assert "SECRET" in pc.pdftotext(str(out))
