"""The generated Excel Pareto matrix (`xlsx`) records honest verdicts."""
from __future__ import annotations

import pytest

from tests.harness import config
from tests.harness.oracle import fingerprint_guard
from tests.harness.plugins.xlsx import XlsxPlugin, cells
from tests.harness.runner import matrix
from tests.scrub import e_office, gen_matrix_xlsx, office_matrix

pytestmark = pytest.mark.skipif(len(e_office.PRODUCERS["xlsx"]()) < 2,
                                reason="fewer than two workbook producers here")


def _cell(doc, adversary, fidelity):
    return next(c for c in doc["cells"]
                if c["adversary"] == adversary and c["fidelity"] == fidelity)


@pytest.fixture(scope="module")
def doc(tmp_path_factory):
    return gen_matrix_xlsx.build_doc(str(tmp_path_factory.mktemp("xlsx_matrix")))


def test_matrix_builds_and_validates(doc):
    matrix.validate(doc)
    assert doc["format"] == "xlsx"
    guard = doc["scrubber_fingerprint"]
    assert guard["verdict"] == "pass", [(s["decoded"][:80], s["bytes_hex"][:160])
                                        for s in guard.get("signatures", [])]


def test_a1_passes_in_every_producer_shape_this_machine_writes(doc):
    cell = _cell(doc, "A1", "F1")
    assert cell["verdict"] == "pass", cell["reason"]
    for shape in office_matrix.shapes("xlsx"):
        assert shape in cell["reason"], shape


def test_a2_names_its_channels_and_who_is_missing(doc):
    """The packager closes (our writer); what still separates producers is named
    per channel; and the cell says Office was not in the peer set rather than
    implying it was."""
    cell = _cell(doc, "A2", "F1")
    assert cell["verdict"] == "fail", cell["reason"]
    assert "closed at F1: packager" in cell["reason"]
    assert "Peer set" in cell["reason"] and "Not in the peer set: Microsoft" in cell["reason"]


def test_every_producer_was_given_the_same_content(tmp_path):
    """The A2 premise: the producers differ, the cells do not."""
    sources = e_office.build_sources("xlsx", str(tmp_path), repeats=1)
    seen = {name: cells(open(paths[0], "rb").read())
            for name, paths in sources.items()}
    first = next(iter(seen.values()))
    assert first, "nothing was read back"
    for name, content in seen.items():
        assert content == first, name


def test_the_packager_channel_closes_and_the_producers_differed_raw(tmp_path):
    sources = e_office.build_sources("xlsx", str(tmp_path), repeats=2)
    raw = e_office.run_condition("xlsx", "raw", sources, str(tmp_path))
    clean = e_office.run_condition("xlsx", "F1", sources, str(tmp_path))
    assert raw["by_channel"]["packager"], "the packagers were indistinguishable raw"
    assert clean["by_channel"]["packager"] == []
    assert clean["by_channel"]["unclassified"] == []


def test_the_guard_catches_a_mark_in_the_workbook_part(tmp_path, monkeypatch):
    """Non-vacuity: with the ZIP fields and the format's names declared, an
    extension written into every workbook_part must fail the guard. (The scrub's own
    residual check refuses it first; switched off here.)"""
    from src.scrub.formats.xlsx import f1
    monkeypatch.setattr(f1, "residuals", lambda data: [])
    real = f1._scrub_xml

    def marked(name, body, ctx):
        out = real(name, body, ctx)
        if name == "xl/workbook.xml":
            out = out.replace(b"</workbook>", b'<extLst><ext uri="scrubbed-v1"/></extLst>'
                              b"</workbook>")
        return out
    monkeypatch.setattr(f1, "_scrub_xml", marked)
    verdict, sigs = fingerprint_guard.evaluate(
        office_matrix._scrubber(), XlsxPlugin(),
        office_matrix._diverse("xlsx", str(tmp_path)), "F1", min_len=config.MIN_SIG_LEN)
    assert verdict == "fail"
    assert any(b"scrubbed-v1".hex() in s["bytes_hex"] for s in sigs)
