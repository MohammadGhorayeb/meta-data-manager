"""The generated ZIP Pareto matrix records honest verdicts."""
from __future__ import annotations

import pytest

from src.scrub.formats.ooxml import zipwrite
from tests.harness import config
from tests.harness.oracle import fingerprint_guard
from tests.harness.plugins.zip import ZipPlugin
from tests.harness.runner import matrix
from tests.scrub import e_zip, gen_matrix_zip


def _cell(doc, adversary, fidelity):
    return next(c for c in doc["cells"]
                if c["adversary"] == adversary and c["fidelity"] == fidelity)


@pytest.fixture(scope="module")
def doc(tmp_path_factory):
    return gen_matrix_zip.build_doc(str(tmp_path_factory.mktemp("zip_matrix")))


def test_matrix_builds_and_validates(doc):
    matrix.validate(doc)
    assert doc["format"] == "zip"
    assert doc["scrubber_fingerprint"]["verdict"] == "pass"


def test_a1_passes_in_every_producer_shape(doc):
    cell = _cell(doc, "A1", "F1")
    assert cell["verdict"] == "pass" and "4 producer shapes" in cell["reason"]


def test_a2_is_measured_over_real_archivers(doc):
    cell = _cell(doc, "A2", "F1")
    assert cell["verdict"] in ("pass", "fail")
    assert "infozip" in cell["reason"] or "python_shutil" in cell["reason"]


def test_the_archivers_were_given_the_same_content_and_differ_before(tmp_path):
    """The A2 cell means something only if the peer set holds one content and its
    archives are told apart before the scrub."""
    sources = e_zip.build_sources(str(tmp_path), repeats=1)
    assert len(sources) >= 2
    plugin = ZipPlugin()
    assert len({plugin.canonical_content(p[0]) for p in sources.values()}) == 1
    raw = e_zip.run_condition("raw", {k: v * 2 for k, v in sources.items()},
                              str(tmp_path))
    assert raw["struct_fingerprints"], "the archivers were indistinguishable raw"


def test_every_archiver_scrubs_to_the_same_file(tmp_path):
    """Stronger than the A2 cell, and what docs/formats.md says: the archives of one
    folder from different archivers are one file, byte for byte, after F1."""
    from src.scrub.formats.zip import f1
    sources = e_zip.build_sources(str(tmp_path), repeats=1)
    outs = {name: f1.scrub(open(p[0], "rb").read()) for name, p in sources.items()}
    assert len(set(outs.values())) == 1, sorted(outs)


def test_the_guard_catches_a_mark_in_the_container(tmp_path, monkeypatch):
    """Non-vacuity: with member data blanked and the header constants declared, an
    archive comment the writer added on every output must still fail the guard.
    (The scrub's own residual check refuses such an output first; it is switched
    off here so the guard is what is tested.)"""
    from src.scrub.formats.zip import f1
    monkeypatch.setattr(f1, "residuals", lambda data: [])
    real = zipwrite._serialise

    def marked(entries):
        out = real(entries)
        return out[:-2] + b"\x0b\x00scrubbed-v1"
    monkeypatch.setattr(zipwrite, "_serialise", marked)
    verdict, sigs = fingerprint_guard.evaluate(
        gen_matrix_zip._scrubber(), ZipPlugin(),
        gen_matrix_zip._diverse(str(tmp_path)), "F1", min_len=config.MIN_SIG_LEN)
    assert verdict == "fail"
    assert any(b"scrubbed-v1".hex() in s["bytes_hex"] for s in sigs)
