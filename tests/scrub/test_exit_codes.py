"""The exit-code contract (November plan N2d): every refusal has its own code.

`cli.py` documents its exit codes as stable and keeps 1 for what nobody
anticipated. That only holds if no refusal raises the bare `ScrubError` base class
-- the CLI cannot tell what it means -- and the November fuzz campaign found nine
places that did: an external program missing or refusing the file, our own safety
checks, the PDF page cap. Each now raises the class that names it, and this test
keeps it that way.
"""
from __future__ import annotations

import inspect
import pathlib
import re

import pytest

from src.scrub import cli, errors
from src.scrub.errors import ParseError, ScrubError

SRC = pathlib.Path(__file__).resolve().parents[2] / "src"


def _refusal_classes():
    return [c for _n, c in inspect.getmembers(errors, inspect.isclass)
            if issubclass(c, ScrubError) and c is not ScrubError]


def test_no_refusal_raises_the_bare_base_class():
    bare = [f"{p.relative_to(SRC)}:{i}"
            for p in SRC.rglob("*.py")
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
            if re.search(r"\braise ScrubError\(", line)]
    assert not bare, "raise a subclass the CLI can map to its code: " + ", ".join(bare)


def test_every_refusal_class_has_a_code_of_its_own():
    codes = {c.__name__: cli.exit_code(c("x")) for c in _refusal_classes()}
    assert all(code not in (0, 1, 2, 8) for code in codes.values()), codes
    assert len(set(codes.values())) == len(codes), codes


def test_a_subclass_of_a_refusal_keeps_its_code():
    class Truncated(ParseError):
        pass
    assert cli.exit_code(Truncated("x")) == cli.exit_code(ParseError("x")) == 4
    assert cli.exit_code(RuntimeError("x")) == 1


def test_the_documented_table_names_every_code():
    doc = cli.__doc__
    for code in set(cli._EXIT.values()) | {0, 1, 2, 8}:
        assert re.search(rf"(^|[\s·]){code} [a-z]", doc, re.M), f"exit {code} undocumented"


def test_a_missing_program_exits_9_and_writes_nothing(tmp_path, monkeypatch):
    from src.scrub.formats.jpeg import f2

    from . import corpus
    monkeypatch.setattr(f2.shutil, "which", lambda name: None)
    src, dst = tmp_path / "in.jpg", tmp_path / "out.jpg"
    src.write_bytes(corpus.make_base_jpeg())
    assert cli.main([str(src), str(dst), "--fidelity", "F2", "--no-report"]) == 9
    assert not dst.exists() and list(tmp_path.iterdir()) == [src]


@pytest.mark.parametrize("module", ["jpeg.f2", "flac.f2", "m4a.f2", "m4a.f3",
                                    "mp3.f3", "pdf.f3"])
def test_each_tier_that_needs_a_program_says_which_when_it_is_missing(module, monkeypatch):
    import importlib
    mod = importlib.import_module(f"src.scrub.formats.{module}")
    monkeypatch.setattr(mod.shutil, "which", lambda name: None)
    finder = getattr(mod, "_tool", None) or mod._jpegtran
    with pytest.raises(errors.ToolError, match="required|not found"):
        finder("ffmpeg") if finder.__code__.co_argcount else finder()
