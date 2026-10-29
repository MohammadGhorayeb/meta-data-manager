"""PowerPoint presentations at F1 (`docs/office_plan.md`, O3).

Every locus in `pptx_corpus` was a probe first (§2). Here each is planted again and
must be gone after F1 -- or kept and reported, or refused -- while every slide's
text and geometry come out unchanged and the deck renders the same.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from src.scrub.dispatch import default_dispatcher
from src.scrub.errors import ContentError
from src.scrub.formats.ooxml import xmlspans
from src.scrub.formats.pptx import f1
from src.scrub.formats.pptx.handler import PptxHandler
from tests.scrub import pptx_corpus as pc


def _all_bytes(data: bytes) -> bytes:
    return b"\n".join(pc.members(data).values())


# --------------------------------------------------------------------------- #
# 1. What F1 removes
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("locus", pc.REMOVED)
def test_each_planted_locus_is_gone(locus):
    out = f1.scrub(pc.build(1, loci=(locus,)))
    everything = _all_bytes(out)
    for value in pc.planted(1, locus):
        assert value not in everything, (locus, value)
    assert f1.residuals(out) == []


def test_two_decks_differing_only_in_metadata_come_out_identical():
    assert f1.scrub(pc.build(1)) == f1.scrub(pc.build(2))


def test_f1_is_a_fixed_point():
    out = f1.scrub(pc.build(1))
    assert f1.scrub(out) == out


def test_an_id_extension_goes_with_the_list_it_leaves_empty():
    """`<a:extLst>` holding only `a16:creationId` goes whole: an empty list left
    behind would mark the shape as edited. A list holding something else stays."""
    m = pc.members(f1.scrub(pc.build(1, loci=("creation_ids", "table_ids", "picture"))))
    slide = m["ppt/slides/slide1.xml"]
    assert b"creationId" not in slide and b"rowId" not in slide and b"colId" not in slide
    assert b"<a:extLst" not in slide and b"<p:extLst" not in slide


def test_add_in_tags_go_with_the_list_that_named_them():
    pres = pc.members(f1.scrub(pc.build(1, loci=("tags",))))["ppt/presentation.xml"]
    assert b"custDataLst" not in pres and b"p:tags" not in pres


def test_recent_colours_go_and_the_part_stays():
    m = pc.members(f1.scrub(pc.build(1, loci=("recent_colours",))))
    assert b"clrMru" not in m["ppt/presProps.xml"]


# --------------------------------------------------------------------------- #
# 2. Content: text and geometry unchanged, and the deck still renders the same
# --------------------------------------------------------------------------- #
def _drawn(part: bytes) -> list[tuple]:
    """What a slide draws: every text run and every position and size."""
    out = []
    for el in xmlspans.parse(part):
        if el.uri == pc.A and el.local == "t":
            out.append(("t", el.text))
        elif el.local in ("off", "ext") and el.uri == pc.A and el.attr("x") is not None:
            out.append((el.local, el.attr("x"), el.attr("y")))
        elif el.local in ("off", "ext") and el.uri == pc.A and el.attr("cx") is not None:
            out.append((el.local, el.attr("cx"), el.attr("cy")))
        elif el.local == "srcRect" and el.uri == pc.A:
            out.append(("crop", tuple(sorted(el.attrs.items()))))
    return out


def test_every_slide_draws_the_same_text_and_geometry():
    data = pc.build(1)
    before, after = pc.members(data), pc.members(f1.scrub(data))
    for name, body in before.items():
        if name.startswith(("ppt/slides/slide", "ppt/notesSlides/")) and name.endswith(".xml"):
            assert _drawn(after[name]) == _drawn(body), name


def test_surviving_parts_only_lose_bytes():
    data = pc.build(1)
    before, after = pc.members(data), pc.members(f1.scrub(data))
    for name, body in after.items():
        if name in before and not name.startswith("ppt/media/"):
            assert len(body) <= len(before[name]), name


@pytest.mark.skipif(shutil.which("soffice") is None or shutil.which("pdftoppm") is None,
                    reason="soffice or pdftoppm absent")
def test_libreoffice_renders_the_same_slides(tmp_path):
    data = pc.build(1)
    (tmp_path / "in.pptx").write_bytes(data)
    (tmp_path / "out.pptx").write_bytes(f1.scrub(data))
    profile = f"-env:UserInstallation=file://{tmp_path}/lo"
    subprocess.run(["soffice", profile, "--headless", "--convert-to", "pdf", "--outdir",
                    str(tmp_path), str(tmp_path / "in.pptx"), str(tmp_path / "out.pptx")],
                   capture_output=True, timeout=240, check=False)
    pages = {}
    for name in ("in", "out"):
        assert (tmp_path / f"{name}.pdf").exists(), name
        subprocess.run(["pdftoppm", "-r", "50", "-png", str(tmp_path / f"{name}.pdf"),
                        str(tmp_path / name)], capture_output=True, timeout=120, check=True)
        pages[name] = [p.read_bytes() for p in sorted(tmp_path.glob(f"{name}-*.png"))]
    assert pages["in"] and pages["out"] == pages["in"]


# --------------------------------------------------------------------------- #
# 3. Kept and reported; refused
# --------------------------------------------------------------------------- #
def test_kept_content_is_kept_and_reported():
    out = f1.scrub(pc.build(1, loci=pc.KEPT + ("picture",)))
    kept = " | ".join(f1.advisories(out))
    for phrase in ("speaker notes", "hidden slide", "cropped picture", "section name",
                   "external link target"):
        assert phrase in kept, phrase
    everything = _all_bytes(out)
    assert b"Remember to mention the layoffs" in everything
    assert b"Draft figures, not for circulation" in everything


@pytest.mark.parametrize("locus", pc.REFUSED)
def test_refused_loci_are_refused_naming_why(locus):
    with pytest.raises(ContentError):
        f1.scrub(pc.build(1, loci=(locus,)))


# --------------------------------------------------------------------------- #
# 4. Dispatch
# --------------------------------------------------------------------------- #
def test_the_dispatcher_hands_a_deck_to_powerpoint():
    assert isinstance(default_dispatcher().resolve(pc.build(1)), PptxHandler)


# --------------------------------------------------------------------------- #
# 5. The benchmark, so its row in docs/office_plan.md §2 goes stale loudly
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(shutil.which("mat2") is None, reason="mat2 absent")
def test_mat2_leaves_the_shape_ids_and_refuses_revision_info(tmp_path):
    deck = tmp_path / "a.pptx"
    deck.write_bytes(pc.build(1, loci=("properties", "creation_ids")))
    subprocess.run(["mat2", str(deck)], capture_output=True, timeout=120, check=False)
    out = tmp_path / "a.cleaned.pptx"
    assert out.exists()
    left = _all_bytes(out.read_bytes())
    assert pc.secret("AUTHOR", 1).encode() not in left
    assert pc.secret("SHAPE", 1).encode() in left, "MAT2 now removes a16:creationId"

    rev = tmp_path / "b.pptx"
    rev.write_bytes(pc.build(1, loci=("revision_info",)))
    subprocess.run(["mat2", str(rev)], capture_output=True, timeout=120, check=False)
    assert not (tmp_path / "b.cleaned.pptx").exists(), "MAT2 now cleans revisionInfo.xml"
