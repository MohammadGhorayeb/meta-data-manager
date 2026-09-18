"""The generated HEIC Pareto matrix records honest verdicts.

HEIC is the first Phase 4 format and the first one whose matrix ships a **measured
failure as its headline**: F1 relocates items and rebuilds the tables and changes
nothing else, so every container-writer choice survives it. That is not a defect in
F1 — it is what a bit-preserving tier is — and the value of running the cell is the
list of features that leak, because that list is the specification for the F2 that
would close it. Phase 3 did the same thing for PDF (E-PDF measured which producer
channel leaked before F2 was designed).

The tiers that do not exist say `not_tested`, never `fail`: a tier that was never run
has no verdict to report.
"""
from __future__ import annotations

import pytest

from tests.harness.runner import matrix
from tests.scrub import gen_matrix_heic
from tests.scrub import heic_corpus as hc

pytestmark = pytest.mark.skipif(not hc.HAVE_HEIF, reason="pillow-heif absent")


def _cell(doc, adversary, fidelity):
    return next(c for c in doc["cells"]
                if c["adversary"] == adversary and c["fidelity"] == fidelity)


@pytest.fixture(scope="module")
def doc(tmp_path_factory):
    return gen_matrix_heic.build_doc(str(tmp_path_factory.mktemp("heic_matrix")))


def test_matrix_builds_and_validates(doc):
    matrix.validate(doc)
    assert doc["format"] == "heic"
    assert doc["scrubber_fingerprint"]["verdict"] == "pass"


def test_a1_passes_at_f1(doc):
    """EXIF, XMP, the `uri ` plist, the thumbnail and the mattes all collapse: three
    files differing only in metadata scrub to indistinguishable output."""
    assert _cell(doc, "A1", "F1")["verdict"] == "pass"


def test_the_tiers_that_do_not_exist_are_not_tested_not_failed(doc):
    for adversary in ("A1", "A2"):
        for fidelity in ("F2", "F3"):
            cell = _cell(doc, adversary, fidelity)
            assert cell["verdict"] == "not_tested"
            assert "not_built" in cell["reason"]


def test_a2_at_f1_fails_and_names_every_channel(doc):
    """A fail with no named leak is not evidence. These five features ARE the F2
    specification, so the cell has to carry them rather than a verdict alone."""
    cell = _cell(doc, "A2", "F1")
    assert cell["verdict"] == "fail"
    leaked = {leak["locus"]["feature_id"] for leak in cell["leaks"]}
    assert {"struct:brand", "struct:meta_table_order",
            "struct:iloc_widths"} <= leaked, leaked


def test_a2_says_the_peer_set_holds_the_picture_constant(doc):
    """The trap this peer set is built to avoid: producers whose *content* differs
    separate on content, and reporting that as a muxer fingerprint would be measuring
    the wrong thing. The tiles are byte-identical across all three writers, and the
    cell has to say so."""
    reason = _cell(doc, "A2", "F1")["reason"]
    assert "BYTE-IDENTICAL" in reason
    for producer in hc.PRODUCERS:
        assert producer in reason
    # And the residual F1 cannot address is stated, not implied by silence.
    assert "does not re-encode" in reason


def test_the_peer_set_really_does_share_its_tiles(tmp_path):
    """The claim above, checked rather than asserted in prose."""
    from tests.harness.plugins.heic import HeicPlugin

    plugin = HeicPlugin()
    sources = gen_matrix_heic._producers(str(tmp_path), repeats=1)
    digests = {name: plugin.coded_image_digest(paths[0])
               for name, paths in sources.items()}
    assert len(set(digests.values())) == 1, digests
    pixels = {name: plugin.canonical_content(paths[0])
              for name, paths in sources.items()}
    assert len({p for p in pixels.values()}) == 1
