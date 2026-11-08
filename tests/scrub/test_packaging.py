"""The package a user installs has to be the code the tests ran.

The suite imports `src.scrub` from a checkout; a user gets `scrubmeta` from
`pip install`. pyproject.toml maps one onto the other with an explicit package list,
so a new directory left off the list passes every other test and then fails with
ModuleNotFoundError on the first user's machine. These check the mapping without
building anything; `scripts/smoke_install.py` (the CI "clean install" job) does build it.
"""
from __future__ import annotations

import hashlib
import pathlib
import re
import tomllib

from src.scrub import __version__, cli

ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src" / "scrub"
CONFIG = tomllib.loads((ROOT / "pyproject.toml").read_text())
APACHE_2 = "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"


def _python_files():
    return [p for p in SOURCE.rglob("*.py") if "__pycache__" not in p.parts]


def test_every_source_directory_is_packaged():
    on_disk = {".".join(("scrubmeta", *p.parent.relative_to(SOURCE).parts))
               for p in _python_files()}
    assert set(CONFIG["tool"]["setuptools"]["packages"]) == on_disk


def test_every_source_directory_is_a_regular_package():
    assert all((p.parent / "__init__.py").is_file() for p in _python_files())


def test_nothing_but_python_ships():
    """A data file would need its own `package-data` line, or be silently left out."""
    strays = [p for p in SOURCE.rglob("*")
              if p.is_file() and p.suffix not in (".py", ".pyc") and p.name != ".DS_Store"]
    assert strays == []


def test_installed_code_never_imports_the_checkout_name():
    """Only relative imports survive the rename from `src.scrub` to `scrubmeta`."""
    checkout = re.compile(r"^\s*(?:from|import)\s+src\b", re.M)
    assert [p for p in _python_files() if checkout.search(p.read_text())] == []


def test_the_command_points_at_a_real_function():
    target = CONFIG["project"]["scripts"]["scrubmeta"]
    assert target == "scrubmeta.cli:main" and callable(cli.main)
    assert CONFIG["tool"]["setuptools"]["package-dir"] == {"scrubmeta": "src/scrub"}


def test_version_is_read_from_the_package():
    assert CONFIG["tool"]["setuptools"]["dynamic"]["version"] == {"attr": "scrubmeta.__version__"}
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:rc\d+|\.dev\d+)?", __version__)


def test_dependencies_are_the_runtime_list():
    assert CONFIG["tool"]["setuptools"]["dynamic"]["dependencies"] == {"file": ["requirements.txt"]}


def test_licence_is_the_verbatim_text():
    assert hashlib.sha256((ROOT / "LICENSE").read_bytes()).hexdigest() == APACHE_2
    assert CONFIG["project"]["license"] == "Apache-2.0"
    assert (ROOT / "NOTICE").is_file()
