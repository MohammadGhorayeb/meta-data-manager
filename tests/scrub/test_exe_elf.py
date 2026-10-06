"""Phase 5 M1 -- ELF at F1: what the build left, removed, and the program runs the same.

Two corpora, as for RAW. The hand-built one (`elf_corpus.py`) carries every locus
the survey measured with a distinctive value derived from who built it, in both
widths and byte orders, and runs on any machine. The real builds (gcc, clang, Go)
need Linux, which CI is: they are what proves the acceptance test -- the scrubbed
program prints the same and exits the same -- rather than a parse.

The independent reader is pyelftools, so "it is gone" is never the scrubber's own
parser agreeing with itself.
"""
from __future__ import annotations

import ast
import io
import os
import re
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from src.scrub import cli  # noqa: E402
from src.scrub.dispatch import default_dispatcher  # noqa: E402
from src.scrub.errors import (  # noqa: E402
    FidelityError,
    ParseError,
    UnsupportedFormatError,
)
from src.scrub.formats.exe import elf  # noqa: E402
from src.scrub.formats.exe.handler import ExeHandler  # noqa: E402
from tests.scrub import elf_corpus as ec  # noqa: E402

elftools = pytest.importorskip("elftools.elf.elffile")

SHAPES = [(64, "<"), (64, ">"), (32, "<"), (32, ">")]
SHAPE_IDS = ["64LE", "64BE", "32LE", "32BE"]
VARIANTS = [(b, o, go) for b, o in SHAPES for go in (False, True)]
VARIANT_IDS = [f"{i}-{'go' if go else 'c'}" for i in SHAPE_IDS for go in (False, True)]


def _read(data: bytes):
    return elftools.ELFFile(io.BytesIO(data))


def _planted(user: str, go: bool) -> list[bytes]:
    out = [user.encode(), ec.COMPILER, ec.PRODUCER, ec.SOURCE_FILE, ec.DEBUGLINK,
           ec.gnu_build_id(user)]
    if go:
        out += [ec.revision(user), ec.revision(user)[:12], ec.commit_time(user),
                ec.module(user), ec.go_build_id(user)]
    return out


# --------------------------------------------------------------------------- #
# The corpus
# --------------------------------------------------------------------------- #
def test_the_corpus_imports_nothing_from_the_scrubber():
    tree = ast.parse(open(os.path.join(os.path.dirname(__file__), "elf_corpus.py"),
                          encoding="utf-8").read())
    imported = [n.module for n in ast.walk(tree)
                if isinstance(n, ast.ImportFrom) and n.module]
    imported += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
                 for a in n.names]
    assert not [m for m in imported if m.split(".")[0] in ("src", "scrub")]


@pytest.mark.parametrize("bits,order,go", VARIANTS, ids=VARIANT_IDS)
def test_an_independent_reader_finds_what_the_corpus_plants(bits, order, go):
    """Otherwise every "it is gone" below passes vacuously."""
    data = ec.build("alice", bits=bits, order=order, go=go)
    e = _read(data)
    assert ec.COMPILER in e.get_section_by_name(".comment").data()
    assert ec.home("alice") in e.get_section_by_name(".debug_line_str").data()
    notes = {n["n_name"]: n for s in e.iter_sections()
             if s.header.sh_type == "SHT_NOTE" for n in s.iter_notes()}
    assert bytes.fromhex(notes["GNU"]["n_desc"]) == ec.gnu_build_id("alice")
    assert ("Go" in notes) == go
    files = [s.name for s in e.get_section_by_name(".symtab").iter_symbols()
             if s["st_info"]["type"] == "STT_FILE"]
    assert files == [ec.SOURCE_FILE.decode()]
    for value in _planted("alice", go):
        assert value in data


# --------------------------------------------------------------------------- #
# F1 on the corpus
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bits,order,go", VARIANTS, ids=VARIANT_IDS)
def test_every_planted_value_is_gone(bits, order, go):
    out = elf.scrub(ec.build("alice", bits=bits, order=order, go=go))
    for value in _planted("alice", go):
        assert value not in out, f"{value!r} survived"
    assert elf.residuals(out) == []


@pytest.mark.parametrize("bits,order,go", VARIANTS, ids=VARIANT_IDS)
def test_nothing_moves_and_the_code_is_untouched(bits, order, go):
    data = ec.build("alice", bits=bits, order=order, go=go)
    out = elf.scrub(data)
    assert len(out) == len(data)
    a, b = _read(data), _read(out)
    layout = lambda e: [(s.name, s["sh_offset"], s["sh_size"], s["sh_flags"])  # noqa: E731
                        for s in e.iter_sections()]
    assert layout(a) == layout(b)
    assert b.get_section_by_name(".text").data() == ec.CODE
    assert b.get_section_by_name(".rodata").data().startswith(ec.PROGRAM_TEXT)
    assert data[:64] == out[:64], "the ELF header changed"


@pytest.mark.parametrize("go", [False, True], ids=["c", "go"])
def test_the_build_ids_are_recomputed_not_zeroed(go):
    """Zeros would mark the file as cleaned (limit #9); the original links it to
    the build. A hash of the cleaned file is neither."""
    data = ec.build("alice", go=go)
    out = elf.scrub(data)
    notes = {n["n_name"]: n for s in _read(out).iter_sections()
             if s.header.sh_type == "SHT_NOTE" for n in s.iter_notes()}
    gnu = bytes.fromhex(notes["GNU"]["n_desc"])
    assert len(gnu) == 20 and gnu != ec.gnu_build_id("alice") and any(gnu)
    if go:
        old, new = ec.go_build_id("alice"), out[data.index(ec.go_build_id("alice")):][:83]
        assert new != old
        assert [i for i, c in enumerate(new) if c == 0x2F] == \
            [i for i, c in enumerate(old) if c == 0x2F]
        assert all(chr(c).isalnum() or c in b"-_/" for c in new)


@pytest.mark.parametrize("go", [False, True], ids=["c", "go"])
def test_two_builds_that_differ_only_in_who_built_them_come_out_identical(go):
    """A1 in miniature: same program, different builder, same-length names."""
    a = elf.scrub(ec.build("alice", go=go))
    b = elf.scrub(ec.build("carol", go=go))
    assert a == b


def test_a_longer_path_still_shows_in_the_section_sizes():
    """Limit #49, asserted so it cannot quietly become false: zeroing in place
    keeps every section's size, and a longer build path made a bigger one."""
    a = elf.scrub(ec.build("alice"))
    b = elf.scrub(ec.build("bob"))
    assert a != b
    assert b"alice" not in a and b"bob" not in b
    size = lambda d: _read(d).get_section_by_name(".debug_info")["sh_size"]  # noqa: E731
    assert size(a) - size(b) == len("alice") - len("bob")


@pytest.mark.parametrize("go", [False, True], ids=["c", "go"])
def test_scrubbing_twice_changes_nothing(go):
    once = elf.scrub(ec.build("alice", go=go))
    assert elf.scrub(once) == once


def test_a_name_another_symbol_shares_is_kept():
    """Linkers merge string tails: `tool.c` may be the end of `sentinel_tool.c`.
    Blanking the whole file name would rename the function too."""
    out = elf.scrub(ec.build("alice", shared_tail=True))
    names = [s.name for s in _read(out).get_section_by_name(".symtab").iter_symbols()]
    assert "tool.c" in names and "main" in names
    assert ec.SOURCE_FILE not in out and b"sentinel_" not in out


# --------------------------------------------------------------------------- #
# What stays, and is said
# --------------------------------------------------------------------------- #
def test_program_text_is_kept_and_reported():
    """A path the program can print is content (survey §5)."""
    data = ec.build("alice", text_path=True)
    out = elf.scrub(data)
    assert ec.home("alice") + b"/hello.c" in out
    assert any("user directory" in a and "/home/alice/" in a for a in elf.advise(data))
    assert elf.residuals(out) == []


def test_a_go_program_that_can_read_its_build_info_keeps_it_and_says_so():
    data = ec.build("alice", go=True, read_build_info=True)
    out = elf.scrub(data)
    assert ec.revision("alice") in out and ec.module("alice") in out
    assert any("ReadBuildInfo" in a for a in elf.advise(data))
    assert elf.residuals(out) == []


def test_the_report_names_what_the_build_left():
    found = elf.describe(ec.build("alice", go=True))
    assert ec.COMPILER.decode() in found["Compiler (.comment)"]
    assert found["GNU build ID"] == ec.gnu_build_id("alice").hex()
    assert found["Go vcs.revision"] == ec.revision("alice").decode()
    assert ec.SOURCE_FILE.decode() in found["Source file names (symbol table)"]


# --------------------------------------------------------------------------- #
# Fail closed
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("etype,word", [(ec.ET_REL, "relocatable"),
                                        (ec.ET_CORE, "core dump")])
def test_objects_and_core_dumps_are_refused_by_name(etype, word):
    with pytest.raises(UnsupportedFormatError, match=word):
        elf.scrub(ec.build("alice", etype=etype))


def test_a_file_without_section_headers_is_refused():
    with pytest.raises(ParseError, match="section headers"):
        elf.scrub(ec.build("alice", section_headers=False))


def test_an_unloaded_section_inside_a_loaded_segment_is_refused():
    """Zeroing it would zero running memory: the loader maps segments, not
    sections, so a section's flag is only a claim."""
    with pytest.raises(ParseError, match="inside a loaded segment"):
        elf.scrub(ec.build("alice", unloaded_inside_load=True))


@pytest.mark.parametrize("cut", [0.3, 0.7, 0.99])
def test_a_truncated_file_is_refused(cut):
    data = ec.build("alice", go=True)
    with pytest.raises(ParseError):
        elf.scrub(data[:int(len(data) * cut)])


def test_residuals_catch_a_leak_the_scrub_left():
    out = bytearray(elf.scrub(ec.build("alice")))
    comment = _read(bytes(out)).get_section_by_name(".comment")
    out[comment["sh_offset"]:comment["sh_offset"] + 4] = b"GCC:"
    assert any(".comment" in r for r in elf.residuals(bytes(out)))
    out = bytearray(elf.scrub(ec.build("alice")))
    i = out.index(b"GNU\0") + 4
    out[i] ^= 0xFF
    assert any("build ID" in r for r in elf.residuals(bytes(out)))


# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #
def test_the_dispatcher_sends_elf_here_and_the_cli_writes_it(tmp_path):
    data = ec.build("alice", text_path=True)
    assert isinstance(default_dispatcher().resolve(data), ExeHandler)
    src, out = tmp_path / "prog", tmp_path / "prog.clean"
    src.write_bytes(data)
    advisories = cli.scrub_file(str(src), str(out), "F1")
    assert out.read_bytes() == elf.scrub(data)
    assert any("user directory" in a for a in advisories)


def test_f2_is_not_offered():
    """F2 would drop the zeroed sections and renumber the section table, closing
    limit #49. Not built; asked for, it is refused rather than approximated."""
    with pytest.raises(FidelityError, match="does not offer F2"):
        ExeHandler().scrub(ec.build("alice"), "F2")


# --------------------------------------------------------------------------- #
# Real programs: the acceptance test is that they run the same
# --------------------------------------------------------------------------- #
needs_gcc = pytest.mark.skipif(not ec.have("gcc"), reason="needs Linux and gcc")


def _same_run(original: str, data: bytes, tmp_path, name: str) -> None:
    clean = tmp_path / name
    clean.write_bytes(data)
    clean.chmod(0o755)
    for args in ((), ("one",), ("one", "two", "three")):
        assert ec.run(original, *args) == ec.run(str(clean), *args), args


@needs_gcc
@pytest.mark.parametrize("compiler", ["gcc", "clang"])
def test_a_real_debug_build_runs_the_same_and_two_builders_converge(compiler,
                                                                    tmp_path):
    if not ec.have(compiler):
        pytest.skip(f"{compiler} not installed")
    a = ec.compile_c(str(tmp_path / "alice" / "proj"), compiler)
    b = ec.compile_c(str(tmp_path / "carol" / "proj"), compiler)
    da, db = open(a, "rb").read(), open(b, "rb").read()
    assert da != db and b"alice" in da
    ca, cb = elf.scrub(da), elf.scrub(db)
    assert b"alice" not in ca and elf.residuals(ca) == []
    _same_run(a, ca, tmp_path, "ca")
    assert ca == cb, "same-length build paths must come out byte-identical"


@needs_gcc
def test_the_build_id_strip_leaves_behind_is_closed(tmp_path):
    """Survey §3: strip removes the paths and keeps a build ID hashed from them,
    so two users' stripped builds still differ. After F1 they do not, whatever
    the lengths of their paths -- strip already removed what had a length."""
    builds = []
    for who in ("alice", "bob"):
        path = ec.compile_c(str(tmp_path / who / "proj"), "gcc")
        subprocess.run(["strip", path], check=True)
        builds.append((path, open(path, "rb").read()))
    assert builds[0][1] != builds[1][1]
    clean = [elf.scrub(d) for _, d in builds]
    assert clean[0] == clean[1]
    _same_run(builds[0][0], clean[0], tmp_path, "c0")


needs_go = pytest.mark.skipif(not (ec.have("go") and ec.have("git")),
                              reason="needs Linux, go and git")


@needs_go
def test_a_real_go_build_loses_its_commit_and_runs_the_same(tmp_path):
    repo = tmp_path / "alice" / "repo"
    repo.mkdir(parents=True)
    (repo / "main.go").write_text(
        'package main\nimport ("fmt"; "os")\n'
        'func main() { fmt.Println("hello", len(os.Args)); os.Exit(len(os.Args)) }\n')
    env = dict(os.environ, GOCACHE=str(tmp_path / "gocache"), GOFLAGS="",
               GOPATH=str(tmp_path / "gopath"), HOME=str(tmp_path),
               GIT_AUTHOR_NAME="A", GIT_AUTHOR_EMAIL="a@x", GIT_COMMITTER_NAME="A",
               GIT_COMMITTER_EMAIL="a@x")
    for cmd in (["git", "init", "-q"], ["go", "mod", "init", "github.com/alice/tool"],
                ["git", "add", "."], ["git", "commit", "-qm", "init"],
                ["go", "build", "-o", "tool", "."]):
        subprocess.run(cmd, cwd=repo, env=env, check=True, capture_output=True)
    data = open(repo / "tool", "rb").read()
    rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, env=env,
                         capture_output=True, text=True).stdout.strip().encode()
    assert rev in data
    out = elf.scrub(data)
    assert rev not in out and rev[:12] not in out
    assert elf.residuals(out) == []
    _same_run(str(repo / "tool"), out, tmp_path, "tool")
    shown = subprocess.run(["go", "version", "-m", str(tmp_path / "tool")],
                           capture_output=True, text=True, env=env).stdout
    assert "vcs.revision=0000000000000000000000000000000000000000" in shown


# --------------------------------------------------------------------------- #
# The survey's own builds, where they exist
# --------------------------------------------------------------------------- #
SURVEY = os.path.expanduser(os.environ.get(
    "EXE_SAMPLES", "~/metadata-research/step2/exe/elf/out"))
SURVEY_FILES = ["gcc_O2", "gcc_g", "gcc_static", "clang_O2", "clang_g", "gcc_s",
                "gcc_g_strip", "rs_O", "go_bin", "go_trim", "go_vcs", "go_vcs_trim"]


@pytest.mark.parametrize("name", SURVEY_FILES)
def test_the_survey_builds_scrub_clean(name):
    path = os.path.join(SURVEY, "alice", name)
    if not os.path.exists(path):
        pytest.skip(f"{name}: survey build not on this machine (p5 plan §0)")
    data = open(path, "rb").read()
    out = elf.scrub(data)
    assert elf.residuals(out) == []
    parsed = elf.parse(out)
    loaded = [(s.offset, s.end) for s in parsed.sections if s.loaded and s.in_file]
    left = [m.start() for m in re.finditer(rb"/home/alice", out)]
    assert all(any(a <= i < b for a, b in loaded) for i in left), \
        "a build path survived outside the program's own text"
    assert not left or any("user directory" in a for a in elf.advise(data))
