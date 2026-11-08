"""`doctor` and `--version` (November plan N4): what an install can do, said truly.

The table of which level needs which program is the kind of thing that drifts, so
it is checked against the code in both directions.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from src.scrub import __version__, cli, doctor
from src.scrub.dispatch import default_dispatcher

REPO = pathlib.Path(__file__).resolve().parents[2]
FORMATS = REPO / "src" / "scrub" / "formats"
_USES = re.compile(r'_tool\("([\w-]+)"\)|shutil\.which\("([\w-]+)"\)|^ENGINE = "([\w-]+)"', re.M)


def _programs_used_by(fmt: str, level: str) -> set[str]:
    path = FORMATS / fmt / f"{level.lower()}.py"
    if not path.exists():
        return set()
    return {a or b or c for a, b, c in _USES.findall(path.read_text(encoding="utf-8"))}


def _levels():
    return [(h.format_id, level) for h in default_dispatcher()._handlers
            for level in h.fidelities]


def test_every_level_in_the_table_exists_and_every_program_is_described():
    levels = set(_levels())
    for (fmt, level), programs in doctor.NEEDS.items():
        assert (fmt, level) in levels, (fmt, level)
        assert set(programs) <= set(doctor.PROGRAMS), programs


@pytest.mark.parametrize("fmt,level", _levels())
def test_the_table_says_what_each_level_really_uses(fmt, level):
    """Both directions: a program the level's code calls must be listed (or the
    level would refuse with no warning from doctor), and a listed one must be
    called (or doctor would report a level unavailable that works)."""
    assert _programs_used_by(fmt, level) == set(doctor.NEEDS.get((fmt, level), ()))


def _fake_survey(monkeypatch, missing=()):
    monkeypatch.setattr(doctor.shutil, "which",
                        lambda name: None if name in missing else f"/bin/{name}")
    monkeypatch.setattr(doctor, "program_version", lambda name, path: "1.2.3")


def test_a_complete_install_exits_0(monkeypatch):
    _fake_survey(monkeypatch)
    text, code = doctor.report()
    assert code == 0 and "MISSING" not in text and " NO " not in text
    assert text.startswith(f"scrubmeta {__version__}")


def test_a_missing_program_names_the_levels_it_costs_and_how_to_get_it(monkeypatch):
    _fake_survey(monkeypatch, missing=("lame",))
    text, code = doctor.report()
    assert code == 9                       # what a clean needing it would exit with
    assert "F3 NO (needs lame)" in text and "brew install lame" in text
    assert re.search(r"mp3\s+F1 yes", text), "the light clean does not need it"


def test_the_word_doctor_alone_runs_it_and_two_paths_are_a_clean(monkeypatch, capsys):
    _fake_survey(monkeypatch)
    assert cli.main(["doctor"]) == 0
    assert "Cleaning levels" in capsys.readouterr().out
    assert cli.main(["doctor", "/nonexistent/out"]) == 2   # a file called doctor


def test_version_flag_prints_the_package_version(capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(["--version"])
    assert caught.value.code == 0
    assert capsys.readouterr().out.strip() == f"scrubmeta {__version__}"


def test_runtime_requirements_are_the_versions_the_tests_run_against():
    """requirements.txt is what an install gets; the harness's pins are what every
    published result was measured with. They must be the same versions."""
    def pins(path):
        out = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"([A-Za-z0-9_.-]+)==([\w.]+)", line.strip())
            if m:
                out[m.group(1).lower()] = m.group(2)
        return out
    runtime, tested = pins(REPO / "requirements.txt"), pins(REPO / "tests/harness/requirements.txt")
    assert set(runtime) == {"pikepdf", "pillow", "numpy"}
    assert all(tested.get(k) == v for k, v in runtime.items()), (runtime, tested)


def test_the_container_image_pins_every_program_to_an_exact_version():
    """E-INSTALL: the image exists so every user has the same programs. A program
    `doctor` knows that the Dockerfile leaves unpinned would quietly bring back the
    fingerprint the image is there to remove."""
    from tests.test_ci_contract import _PACKAGE_FOR
    dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^FROM \S+@sha256:[0-9a-f]{64}$", dockerfile, re.M), "base not pinned"
    for program in doctor.PROGRAMS:
        package = _PACKAGE_FOR[program]
        assert re.search(rf"\b{re.escape(package)}=\S+", dockerfile), package
