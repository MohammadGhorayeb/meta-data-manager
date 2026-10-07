"""The generated programs (ELF, Mach-O, PE) Pareto matrix records honest verdicts.

A1@F1 is measured in every corpus layout plus real builds where a compiler exists.
A2@F1 is measured where two real compilers exist for one format (Linux: gcc and
clang for ELF, mingw-w64 and clang+lld for PE) and stated unmeasured elsewhere,
never clean. The fingerprint guard runs per family -- a mixed set could never fail
on one format's own mark -- and declares exactly the marks blanking leaves.
"""
from __future__ import annotations

import sys

import pytest

from tests.harness.plugins.exe import ExePlugin
from tests.harness.runner import matrix
from tests.scrub import e_exe, gen_matrix_exe

pytest.importorskip("elftools")
pytest.importorskip("macholib")
pytest.importorskip("pefile")


def _cell(doc, adversary, fidelity):
    return next(c for c in doc["cells"]
                if c["adversary"] == adversary and c["fidelity"] == fidelity)


@pytest.fixture(scope="module")
def doc(tmp_path_factory):
    return gen_matrix_exe.build_doc(str(tmp_path_factory.mktemp("exe_matrix")))


def test_matrix_builds_and_validates(doc):
    matrix.validate(doc)
    assert doc["format"] == "exe"
    assert doc["scrubber_fingerprint"]["verdict"] == "pass"


def test_a1_passes_at_f1_in_every_layout(doc):
    cell = _cell(doc, "A1", "F1")
    assert cell["verdict"] == "pass", cell["reason"]
    for layout in gen_matrix_exe.SHAPES:
        assert layout in cell["reason"]


def test_a2_is_measured_where_two_compilers_exist_and_never_guessed(doc):
    """FAIL is the expected answer (the compiler is in the code), and the cell has
    to say which container traits carry it; with one compiler it says so."""
    cell = _cell(doc, "A2", "F1")
    if not any(len(c) >= 2 for c in e_exe.producers().values()):
        assert cell["verdict"] == "not_tested"
        assert "fewer than two real compilers" in cell["reason"]
    else:
        assert cell["verdict"] in ("fail", "pass")
        assert "(" in cell["reason"]


def test_the_tiers_that_do_not_exist_are_not_tested_not_failed(doc):
    for adversary in ("A1", "A2"):
        assert "not_built" in _cell(doc, adversary, "F2")["reason"]
        assert "not_applicable" in _cell(doc, adversary, "F3")["reason"]
        for fidelity in ("F2", "F3"):
            assert _cell(doc, adversary, fidelity)["verdict"] == "not_tested"


def test_the_declared_marks_carry_no_locus():
    """A declaration broad enough to hide a leak would be an exemption: each is a
    single fill character, the blanked-time shape, or `a.out`."""
    for constant in ExePlugin().mandatory_constants():
        assert len(constant) == 1 or set(constant) <= set(b"0-:TZ") \
            or constant == b"a.out\0", constant


def test_the_declarations_are_generated_from_the_code_that_writes_them():
    from src.scrub.formats.exe import codesign, go
    assert ExePlugin().mandatory_constants() == [
        b"\0", bytes([go.BLANK]), go.BLANKED_TIME,
        codesign.CANONICAL_IDENTIFIER + b"\0"]


def test_the_guard_runs_per_family_so_a_formats_own_mark_can_fail_it(tmp_path):
    """The reason for families: with every format mixed together, `a.out` (in
    every Mach-O output, no ELF one) could never be common to all outputs."""
    families = gen_matrix_exe._families(str(tmp_path))
    assert set(families) == {"elf", "macho", "pe", "go"}
    assert all(len(paths) >= 3 for paths in families.values())


@pytest.mark.skipif(not sys.platform.startswith("linux"),
                    reason="the A2 producers are Linux compilers")
def test_on_linux_a2_names_the_compilers_it_compared(doc):
    cell = _cell(doc, "A2", "F1")
    if cell["verdict"] == "not_tested":
        pytest.skip("fewer than two compilers installed")
    assert "gcc" in cell["reason"] and "clang" in cell["reason"]
