"""Phase 5 M2 -- Mach-O at F1: what the build left, removed, and the program still
runs on a Mac -- which, on Apple Silicon, means its signature had to be redone.

Like ELF, two corpora. The hand-built one (`macho_corpus.py`) carries every locus
the survey measured, signed ad hoc by the corpus's own signer, read independently
with macholib, on any machine. The real builds (clang, swiftc, rustc, Go) need
macOS: they prove the acceptance test, and Apple's own `codesign -v` judges the
signature we recompute. CI runs them in its macOS job.
"""
from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from src.scrub import cli  # noqa: E402
from src.scrub.dispatch import default_dispatcher  # noqa: E402
from src.scrub.errors import (  # noqa: E402
    ParseError,
    UnsupportedFormatError,
)
from src.scrub.formats.exe import codesign, macho  # noqa: E402
from src.scrub.formats.exe.handler import ExeHandler  # noqa: E402
from tests.scrub import elf_corpus as ec  # noqa: E402
from tests.scrub import macho_corpus as mc  # noqa: E402

macholib = pytest.importorskip("macholib.MachO")

SHAPES = {"thin": {}, "go": {"go": True}, "dylib": {"filetype": mc.MH_DYLIB},
          "codesign": {"sign_style": "codesign"},
          "32be": {"bits": 32, "order": ">", "cpu": mc.CPU_PPC, "signed": False}}


def _build(shape: str, user: str = "alice") -> bytes:
    return mc.fat(user) if shape == "fat" else mc.build(user, **SHAPES[shape])


ALL = [*SHAPES, "fat"]


def _planted(user: str, shape: str) -> list[bytes]:
    out = [home for home in (mc.home(user), mc.temp_object(user),
                             mc.swift_module(user), mc.uuid(user), mc.PRODUCER,
                             mc.SOURCE_FILE)]
    if shape != "32be":
        out.append(mc.link_name(user, SHAPES.get(shape, {}).get("sign_style",
                                                                  "linker")))
    if shape == "go":
        out += [ec.revision(user), ec.commit_time(user), ec.module(user),
                ec.go_build_id(user)]
    return out


def _read(data: bytes, tmp_path):
    path = tmp_path / "m.bin"
    path.write_bytes(data)
    return macholib.MachO(str(path))


def _sig(data: bytes) -> codesign.Signature:
    m = macho.parse_slice(data)
    return codesign.parse(data, *m.signature)


# --------------------------------------------------------------------------- #
# The corpus
# --------------------------------------------------------------------------- #
def test_the_corpus_imports_nothing_from_the_scrubber():
    tree = ast.parse(open(os.path.join(os.path.dirname(__file__), "macho_corpus.py"),
                          encoding="utf-8").read())
    imported = [n.module for n in ast.walk(tree)
                if isinstance(n, ast.ImportFrom) and n.module]
    imported += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
                 for a in n.names]
    assert not [m for m in imported if m.split(".")[0] in ("src", "scrub")]


@pytest.mark.parametrize("shape", ALL)
def test_an_independent_reader_finds_what_the_corpus_plants(shape, tmp_path):
    data = _build(shape)
    m = _read(data, tmp_path)
    assert len(m.headers) == (2 if shape == "fat" else 1)
    for h in m.headers:
        kinds = [type(c).__name__ for _, c, _ in h.commands]
        assert "uuid_command" in kinds and "symtab_command" in kinds
    for value in _planted("alice", shape):
        assert value in data, value


@pytest.mark.parametrize("shape", ["thin", "go", "dylib", "codesign"])
def test_the_corpus_signer_and_ours_agree_on_the_original(shape):
    """Two implementations of the page-hash scheme: the corpus signed it, the
    scrubber's verifier must accept it before anything is edited."""
    data = _build(shape)
    assert mc.signature_pages_ok(data)
    assert codesign.verify(data, _sig(data)) == []


# --------------------------------------------------------------------------- #
# F1 on the corpus
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("shape", ALL)
def test_every_planted_value_is_gone(shape):
    data = _build(shape)
    out = macho.scrub(data)
    if shape == "dylib":
        # The install name is the loader's (kept, reported); nothing else may hold
        # the home directory.
        name = mc.install_name("alice")
        assert out.count(mc.home("alice")) == out.count(name) == 1
        out = out.replace(name, b"")
    for value in _planted("alice", shape):
        assert value not in out, f"{value!r} survived"
    assert b"alice" not in out
    assert macho.residuals(macho.scrub(data)) == []
    assert len(macho.scrub(data)) == len(data)


@pytest.mark.parametrize("shape", ["thin", "go", "dylib", "codesign"])
def test_the_signature_is_recomputed_and_a_second_implementation_accepts_it(shape):
    out = macho.scrub(_build(shape))
    assert mc.signature_pages_ok(out), "the corpus's own signer disagrees"
    sig = _sig(out)
    assert codesign.verify(out, sig) == []
    assert all(codesign.identifier(out, cd) == b"a.out" for cd in sig.directories)
    assert sig.adhoc


@pytest.mark.parametrize("shape", ["thin", "fat"])
def test_nothing_moves_and_the_code_is_untouched(shape, tmp_path):
    data = _build(shape)
    out = macho.scrub(data)
    for a, b in zip(_read(data, tmp_path).headers, _read(out, tmp_path).headers,
                    strict=True):
        assert [type(c).__name__ for _, c, _ in a.commands] == \
            [type(c).__name__ for _, c, _ in b.commands]
    for start, size in macho.slices(out):
        assert mc.CODE in out[start:start + size]
        assert mc.PROGRAM_TEXT in out[start:start + size]


def test_the_uuid_is_recomputed_with_ld64s_version_bits():
    data = _build("thin")
    out = macho.scrub(data)
    at = macho.parse_slice(out).uuid_at
    u = out[at:at + 16]
    assert u != mc.uuid("alice") and any(u)
    assert u[6] >> 4 == 3 and u[8] >> 6 == 2


@pytest.mark.parametrize("shape", ["thin", "go", "fat", "codesign"])
def test_two_builds_that_differ_only_in_who_built_them_come_out_identical(shape):
    """A1 in miniature, same-length names: home directory, temp directory, object
    time, random UUID and link name all differ going in. (Not a dylib: its install
    name is what the loader reads, so two of them differ in content.)"""
    a, b = _build(shape, "alice"), _build(shape, "carol")
    assert a != b
    assert macho.scrub(a) == macho.scrub(b)


@pytest.mark.parametrize("shape", ALL)
def test_scrubbing_twice_changes_nothing(shape):
    once = macho.scrub(_build(shape))
    assert macho.scrub(once) == once


def test_the_codesign_identifier_no_longer_carries_the_old_uuid():
    """`codesign -s -` names the program `<name>-55554944<UUID>`: recomputing
    LC_UUID alone would have left the original UUID in the signature."""
    data = _build("codesign")
    assert mc.uuid("alice").hex().encode() in data
    out = macho.scrub(data)
    assert mc.uuid("alice").hex().encode() not in out


# --------------------------------------------------------------------------- #
# What stays, and is said
# --------------------------------------------------------------------------- #
def test_program_text_and_an_install_name_are_kept_and_reported():
    data = mc.build("alice", filetype=mc.MH_DYLIB, text_path=True)
    out = macho.scrub(data)
    assert mc.install_name("alice") in out
    assert mc.home("alice") + b"hello.c" in out
    advice = macho.advise(data)
    assert any("library path" in a for a in advice)
    assert any("own text" in a for a in advice)
    assert macho.residuals(out) == []


def test_a_go_program_that_can_read_its_build_info_keeps_it_and_says_so():
    data = mc.build("alice", go=True, read_build_info=True)
    out = macho.scrub(data)
    assert ec.revision("alice") in out
    assert any("ReadBuildInfo" in a for a in macho.advise(data))


def test_the_report_names_what_the_build_left():
    found = macho.describe(_build("thin"))
    assert found["UUID"] == mc.uuid("alice").hex()
    assert found["Debug map"].startswith("4 paths")   # module, dir, source, object
    assert mc.swift_module("alice").decode() in found["Debug map"]
    assert str(mc.mtime("alice")) in found["Object file times"]
    assert "__DWARF" in found["Unmapped segments"]
    assert "sentinel_tool-arm64.out" in found["Code signature"]
    assert "SDK 14.4.0" in found["Build version"]


# --------------------------------------------------------------------------- #
# Fail closed
# --------------------------------------------------------------------------- #
def test_an_identity_signature_is_refused_by_name():
    with pytest.raises(UnsupportedFormatError, match="developer identity.*SENTINELTM"):
        macho.scrub(mc.build("alice", identity=True))


@pytest.mark.parametrize("filetype,word", [(mc.MH_OBJECT, "compiler object"),
                                           (mc.MH_DSYM, "dSYM")])
def test_objects_and_debug_symbol_files_are_refused_by_name(filetype, word):
    with pytest.raises(UnsupportedFormatError, match=word):
        macho.scrub(mc.build("alice", filetype=filetype))


def test_an_unmapped_segment_over_mapped_bytes_is_refused():
    with pytest.raises(ParseError, match="overlaps a mapped"):
        macho.scrub(mc.build("alice", dwarf_overlaps=True))


@pytest.mark.parametrize("cut", [0.2, 0.6, 0.97])
def test_a_truncated_file_is_refused(cut):
    data = _build("go")
    with pytest.raises(ParseError):
        macho.scrub(data[:int(len(data) * cut)])


def test_a_java_class_file_is_not_taken_for_a_universal_binary():
    assert not macho.is_macho(mc.java_class())
    assert not ExeHandler().claims(mc.java_class())


def test_residuals_catch_a_stale_signature_and_a_leak():
    out = bytearray(macho.scrub(_build("thin")))
    i = out.index(mc.CODE)
    out[i] ^= 0xFF
    assert any("killed at launch" in r for r in macho.residuals(bytes(out)))
    out = bytearray(macho.scrub(_build("thin")))
    sig = _sig(bytes(out))
    cd = sig.directories[0]
    at = cd.at + cd.ident_offset
    out[at:at + 5] = b"hello"
    assert any("names the program" in r for r in macho.residuals(bytes(out)))


# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("shape", ["thin", "fat"])
def test_the_dispatcher_sends_mach_o_here_and_the_cli_writes_it(shape, tmp_path):
    data = _build(shape)
    assert isinstance(default_dispatcher().resolve(data), ExeHandler)
    src, out = tmp_path / "prog", tmp_path / "prog.clean"
    src.write_bytes(data)
    cli.scrub_file(str(src), str(out), "F1")
    assert out.read_bytes() == macho.scrub(data)


# --------------------------------------------------------------------------- #
# Real programs on macOS: they run the same, and Apple accepts the signature
# --------------------------------------------------------------------------- #
needs_clang = pytest.mark.skipif(not mc.have("clang"), reason="needs macOS and clang")


def _same_run(original: str, data: bytes, tmp_path, name: str) -> None:
    clean = tmp_path / name
    clean.write_bytes(data)
    clean.chmod(0o755)
    for args in ((), ("one",), ("one", "two", "three")):
        assert mc.run(original, *args) == mc.run(str(clean), *args), args
    universal = data[:4] == b"\xca\xfe\xba\xbe"
    v = subprocess.run(["codesign", "-v", *(["--arch", "arm64"] if universal else []),
                        str(clean)], capture_output=True, text=True)
    if v.returncode and "not signed at all" not in v.stderr:
        pytest.fail(f"Apple's codesign rejects our signature: {v.stderr}")


@needs_clang
@pytest.mark.parametrize("flags", [("-g",), ("-O2",), ("-g", "-arch", "arm64",
                                                       "-arch", "x86_64")],
                         ids=["g", "O2", "universal-g"])
def test_a_real_build_runs_the_same_and_two_builders_converge(flags, tmp_path):
    a = mc.compile_c(str(tmp_path / "alice" / "proj"), flags)
    b = mc.compile_c(str(tmp_path / "carol" / "proj"), flags)
    da, db = open(a, "rb").read(), open(b, "rb").read()
    ca, cb = macho.scrub(da), macho.scrub(db)
    assert macho.residuals(ca) == []
    assert not re.search(rb"/(?:alice|carol)/", ca)
    _same_run(a, ca, tmp_path, "ca")
    assert ca == cb, "two build directories of equal length must converge"


@pytest.mark.skipif(not mc.have("rustc"), reason="needs macOS and rustc")
def test_a_plain_rustc_build_loses_the_home_directory(tmp_path):
    """Survey §4.1: `rustc -O` embeds ~/.rustup paths in the debug map."""
    src = tmp_path / "hello.rs"
    src.write_text('fn main() { println!("hi {}", std::env::args().count()); }\n')
    subprocess.run(["rustc", "-O", str(src), "-o", str(tmp_path / "rs")], check=True,
                   capture_output=True)
    data = open(tmp_path / "rs", "rb").read()
    home = os.path.expanduser("~").encode()
    out = macho.scrub(data)
    assert home not in out and macho.residuals(out) == []
    _same_run(str(tmp_path / "rs"), out, tmp_path, "rs_clean")


@pytest.mark.skipif(not mc.have("swiftc"), reason="needs macOS and swiftc")
def test_a_swift_debug_build_loses_its_module_path(tmp_path):
    src = tmp_path / "hello.swift"
    src.write_text('print("hi", CommandLine.arguments.count)\n')
    subprocess.run(["swiftc", "-g", str(src), "-o", str(tmp_path / "sw")], check=True,
                   capture_output=True, cwd=tmp_path)
    data = open(tmp_path / "sw", "rb").read()
    out = macho.scrub(data)
    assert b".swiftmodule" not in out and macho.residuals(out) == []
    _same_run(str(tmp_path / "sw"), out, tmp_path, "sw_clean")


@pytest.mark.skipif(not (mc.have("go") and mc.have("git")),
                    reason="needs macOS, go and git")
def test_a_real_go_build_for_the_mac_runs_the_same(tmp_path):
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
    out = macho.scrub(data)
    assert rev in data and rev not in out and macho.residuals(out) == []
    _same_run(str(repo / "tool"), out, tmp_path, "tool")


# --------------------------------------------------------------------------- #
# The survey's own builds, where they exist
# --------------------------------------------------------------------------- #
SURVEY = os.path.expanduser(os.environ.get(
    "MACHO_SAMPLES", "~/metadata-research/step2/exe/macho"))
SURVEY_FILES = ["buildA/c_O2", "buildA/c_g", "buildA/rs_O", "buildA/rs_g",
                "buildA/sw_O", "more/fat_g", "more/resigned", "more/sw_g",
                "more/libfoo.dylib", "more/x86only", "go/go_darwin"]


@pytest.mark.parametrize("name", SURVEY_FILES)
def test_the_survey_builds_scrub_clean_and_still_run(name, tmp_path):
    path = os.path.join(SURVEY, name)
    if not os.path.exists(path):
        pytest.skip(f"{name}: survey build not on this machine (p5 plan §0, §9)")
    data = open(path, "rb").read()
    out = macho.scrub(data)
    assert macho.residuals(out) == []
    assert b"/var/folders/" not in out
    if mc.CAN_RUN and not name.endswith(".dylib") and shutil.which("codesign"):
        _same_run(path, out, tmp_path, "clean")
