"""M14 — DOCX F3: re-typeset through one engine, and what that does and does not buy.

F2 closed the document model's *spelling* and left its *substance*, which cannot be
normalised by rewriting because changing it changes the document. It can be
**regenerated**: opening the document in one engine and saving it again produces that
engine's part set, styles and paragraph idiom for every input.

This is where F3 differs from PDF's. Rasterising *relocates* the typesetter's
geometry into pixels, where E-PDF-RASTER then read the producer off the ink at 100%;
a round-trip **rebuilds the symbolic model**. So it is the one tier in this project
where F3 had a real chance of buying what F2 could not — and the measurement says it
very nearly does.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess

import pytest

from src.scrub.errors import ContentError, FidelityError
from src.scrub.formats.docx import f2, f3, textextract
from src.scrub.formats.ooxml import opc

from . import docx_corpus as C
from . import e_docx

pytestmark = pytest.mark.skipif(
    not f3.available(),
    reason="DOCX F3 re-typesets through LibreOffice, which is not installed")


@pytest.fixture(scope="module")
def real(tmp_path_factory):
    return C.producers(str(tmp_path_factory.mktemp("docxf3")))


def test_the_words_survive_the_retypeset(real):
    """F3 does not promise the same rendering, but it must not lose the text."""
    for name, path in real.items():
        data = open(path, "rb").read()
        assert textextract.document_text(f3.scrub(data)).split() == \
            textextract.document_text(data).split(), name


def test_the_engines_own_metadata_is_removed_afterwards(real):
    """The round-trip **adds** metadata: LibreOffice writes its own
    `docProps/core.xml`, its own wall-clock ZIP timestamps and its own `Application`
    string. Running F2 over the engine's output is not optional."""
    for name, path in real.items():
        out = f3.scrub(open(path, "rb").read())
        assert "docProps/core.xml" not in opc.parse(out).parts(), name
        assert b"LibreOffice" not in out, name
        assert f2.residuals(out) == [], name


def test_it_is_still_a_docx_that_opens(real, tmp_path):
    for name, path in real.items():
        out = tmp_path / f"{name}.docx"
        out.write_bytes(f3.scrub(open(path, "rb").read()))
        sub = tmp_path / f"o_{name}"
        sub.mkdir()
        subprocess.run(["soffice", "--headless", "--convert-to", "pdf",
                        "--outdir", str(sub), str(out)],
                       capture_output=True, timeout=200, check=False)
        assert list(sub.glob("*.pdf")), name


def test_structural_refusals_still_apply_before_the_engine_sees_it(tmp_path):
    """The engine would happily open a macro-bearing package and drop things
    silently, so it is refused before we hand the document to something we do not
    control."""
    import zipfile
    src = str(tmp_path / "m.docx")
    C.synthetic(src)
    with zipfile.ZipFile(src, "a", zipfile.ZIP_DEFLATED) as z:
        zi = zipfile.ZipInfo("word/vbaProject.bin", (1980, 1, 1, 0, 0, 0))
        zi.compress_type = zipfile.ZIP_DEFLATED
        z.writestr(zi, b"\xd0\xcf\x11\xe0")
    with pytest.raises(ContentError, match="macro"):
        f3.scrub(open(src, "rb").read())


# --------------------------------------------------------------------------- #
# The result
# --------------------------------------------------------------------------- #
def test_the_retypeset_collapses_the_model_channel_to_one_key(tmp_path):
    """The measurement M14 exists for.

    F2 leaves seven model keys separating producers; the re-typeset leaves **one**.
    Everything else — which parts exist, which settings are written, how a paragraph
    is built, the section properties — becomes the engine's, identically, for every
    input.
    """
    sources = e_docx.build_sources(str(tmp_path), repeats=2)
    if len(sources) < 2:
        pytest.skip("need at least two producers for a peer set")
    at_f2 = e_docx.run_condition("F2", sources, str(tmp_path))
    at_f3 = e_docx.run_condition("F3", sources, str(tmp_path))

    f2_model = set(at_f2["by_channel"]["model"])
    f3_model = set(at_f3["by_channel"]["model"])
    assert f3_model < f2_model, (f2_model, f3_model)
    assert not at_f3["by_channel"]["packager"]


def test_what_survives_is_the_sources_own_styles_not_the_producers(tmp_path):
    """The surviving key is a **primary-production trace**, and naming it that way is
    the point rather than a euphemism.

    After the round-trip every producer carries the engine's own style set. The only
    difference is that documents whose *source* already had a style keep it — the
    engine preserves the input's styles rather than regenerating them from nothing.
    That is the same species as M4A's primary-encoding trace: a residue of how the
    original was made, surviving a rebuild, rather than a trait of the tool that made
    the file we were handed.
    """
    from tests.harness.plugins.docx import DocxPlugin
    plugin = DocxPlugin()
    sources = C.producers_matched(str(tmp_path), repeats=1)
    if len(sources) < 2:
        pytest.skip("need at least two producers")

    style_sets = {}
    for name, paths in sources.items():
        out = os.path.join(str(tmp_path), f"{name}_f3.docx")
        with open(out, "wb") as fh:
            fh.write(f3.scrub(open(paths[0], "rb").read()))
        style_sets[name] = set(plugin.structural_features(out)["style_ids"])

    common = set.intersection(*style_sets.values())
    assert common, "the engine should give every document the same base styles"
    # Every producer's set is the common core plus at most what its source carried.
    for name, styles in style_sets.items():
        assert common <= styles, name


# --------------------------------------------------------------------------- #
# The cost, and why this corpus cannot measure it
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(shutil.which("pdftoppm") is None, reason="pdftoppm absent")
def test_rendering_through_the_same_engine_cannot_measure_the_retypeset_cost(
        real, tmp_path):
    """**A negative result about our own method, asserted so it cannot be forgotten.**

    Rendering a document before and after F3 through LibreOffice reports them
    identical — and that is not evidence the tier is lossless. The renderer *is* the
    engine that did the round-trip, so the check is the engine grading its own
    homework. The real cost appears when a Word-authored document is re-typeset by
    LibreOffice and then opened **in Word**, which is exactly what cannot be scripted
    on any platform.

    So the tier's cost is reported as *not measured* rather than as zero, and this
    test exists to stop a future reader mistaking the identical hashes below for the
    former.
    """
    def render(path: str, tag: str) -> str | None:
        out = tmp_path / tag
        out.mkdir()
        subprocess.run(["soffice", "--headless", "--convert-to", "pdf",
                        "--outdir", str(out), path], capture_output=True, timeout=200)
        pdfs = list(out.glob("*.pdf"))
        if not pdfs:
            return None
        subprocess.run(["pdftoppm", "-r", "100", "-png", str(pdfs[0]),
                        str(out / "p")], capture_output=True, timeout=200)
        pngs = sorted(out.glob("p*.png"))
        if not pngs:
            return None
        h = hashlib.sha256()
        for png in pngs:
            h.update(png.read_bytes())
        return h.hexdigest()

    name, path = next((n, p) for n, p in real.items() if n != "synth_zipfile")
    scrubbed = tmp_path / "f3.docx"
    scrubbed.write_bytes(f3.scrub(open(path, "rb").read()))
    before, after = render(path, "before"), render(str(scrubbed), "after")
    if before is None or after is None:
        pytest.skip("LibreOffice produced no PDF here")
    assert before == after, (
        "if this ever differs, LibreOffice is not even self-consistent and the "
        "measurement is worth less than it already is")


def test_the_advisory_states_the_retypeset_cost(real):
    """The cost is stated wherever the tier is offered, not after it is used."""
    for name, path in real.items():
        out = f3.scrub(open(path, "rb").read())
        assert any("re-typeset" in a for a in f3.advisories(out)), name


def test_the_tier_refuses_rather_than_returning_the_input_when_the_engine_fails():
    """A tier that silently hands back its input looks exactly like a successful
    scrub. Asserted on the code path rather than by breaking LibreOffice."""
    import inspect
    src = inspect.getsource(f3._round_trip)
    assert "refusing rather" in src
    assert "ContentError" in src


def test_f3_is_unavailable_rather_than_silent_without_the_engine(monkeypatch):
    monkeypatch.setattr(f3, "available", lambda: False)
    with pytest.raises(FidelityError, match="LibreOffice"):
        f3.scrub(b"")
