"""Phase 5 M3 -- PE at F1: what a Windows build left, removed, and the program runs
the same under Wine.

The hand-built corpus (`pe_corpus.py`) carries both toolchain families' loci --
MSVC's Rich header, CodeView and REPRO entries; mingw's COFF `.file` records, DWARF
and checksum -- read independently with pefile, on any machine. The real builds
(mingw-w64, Go for Windows) and pip's own MSVC-built launchers need Linux and Wine,
which CI installs.
"""
from __future__ import annotations

import ast
import glob
import os
import shutil
import struct
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
from src.scrub.formats.exe import pe  # noqa: E402
from src.scrub.formats.exe.handler import ExeHandler  # noqa: E402
from tests.scrub import elf_corpus as ec  # noqa: E402
from tests.scrub import pe_corpus as pc  # noqa: E402

pefile = pytest.importorskip("pefile")

SHAPES = {"msvc": {}, "msvc32": {"plus": False}, "mingw": {"shape": "mingw"},
          "mingw32": {"shape": "mingw", "plus": False},
          "go": {"shape": "mingw", "go": True}, "dll": {"dll": True}}


def _build(shape: str, user: str = "alice", **kw) -> bytes:
    return pc.build(user, **SHAPES[shape], **kw)


def _u16(s: str) -> bytes:
    return s.encode("utf-16-le")


def _planted(user: str, shape: str) -> list[bytes]:
    out = [_u16(pc.company(user)), _u16(pc.copyright_(user)),
           _u16(pc.original_name(user)), user.encode()]
    if shape.startswith(("msvc", "dll")):
        out += [pc.pdb_path(user), pc.pdb_guid(user), pc.repro_hash(user), b"Rich"]
    else:
        out += [pc.SOURCE_FILE, pc.home_unix(user)]
    if shape == "go":
        out += [ec.revision(user), ec.module(user), ec.go_build_id(user)]
    return out


def _version(p) -> dict[str, str]:
    found = {}
    for fi in getattr(p, "FileInfo", []) or []:
        for e in fi:
            for st in getattr(e, "StringTable", []):
                found.update({k.decode(): v.decode() for k, v in st.entries.items()})
    return found


# --------------------------------------------------------------------------- #
# The corpus
# --------------------------------------------------------------------------- #
def test_the_corpus_imports_nothing_from_the_scrubber():
    tree = ast.parse(open(os.path.join(os.path.dirname(__file__), "pe_corpus.py"),
                          encoding="utf-8").read())
    imported = [n.module for n in ast.walk(tree)
                if isinstance(n, ast.ImportFrom) and n.module]
    imported += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
                 for a in n.names]
    assert not [m for m in imported if m.split(".")[0] in ("src", "scrub")]


@pytest.mark.parametrize("shape", SHAPES)
def test_an_independent_reader_finds_what_the_corpus_plants(shape):
    data = _build(shape)
    p = pefile.PE(data=data)
    assert p.FILE_HEADER.TimeDateStamp == pc.link_time("alice")
    assert _version(p)["CompanyName"] == pc.company("alice")
    if shape.startswith(("msvc", "dll")):
        assert p.parse_rich_header() is not None
        assert pc.pdb_path("alice") in data
    else:
        assert p.OPTIONAL_HEADER.CheckSum == p.generate_checksum()
    for value in _planted("alice", shape):
        assert value in data, value


def test_the_scrubbers_checksum_is_pefiles():
    for shape in SHAPES:
        data = _build(shape)
        assert pe.checksum(data, pe.parse(data).checksum_at) == \
            pefile.PE(data=data).generate_checksum()


# --------------------------------------------------------------------------- #
# F1 on the corpus
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("shape", SHAPES)
def test_every_planted_value_is_gone(shape):
    data = _build(shape)
    out = pe.scrub(data)
    for value in _planted("alice", shape):
        assert value not in out, f"{value!r} survived"
    assert len(out) == len(data)
    assert pe.residuals(out) == []
    p = pefile.PE(data=out)
    assert p.FILE_HEADER.TimeDateStamp == 0
    assert p.parse_rich_header() is None


@pytest.mark.parametrize("shape", SHAPES)
def test_what_describes_the_program_and_what_the_loader_reads_stay(shape):
    """ProductName, FileDescription and the versions describe the program; the
    CET flag entry is read by the loader; POGO is a toolchain trace (A2), not an
    identity. The code and its text are untouched."""
    out = pe.scrub(_build(shape))
    p = pefile.PE(data=out)
    version = _version(p)
    assert version["ProductName"] == pc.PRODUCT
    assert version["FileDescription"] == pc.DESCRIPTION
    assert version["FileVersion"] == pc.FILE_VERSION
    assert not version["CompanyName"] and not version["LegalCopyright"]
    if shape.startswith(("msvc", "dll")):
        kept = [pefile.DEBUG_TYPE.get(d.struct.Type) for d in p.DIRECTORY_ENTRY_DEBUG]
        assert kept == ["IMAGE_DEBUG_TYPE_POGO", "IMAGE_DEBUG_TYPE_EX_DLLCHARACTERISTICS"]
    assert pc.CODE in out and pc.PROGRAM_TEXT in out


@pytest.mark.parametrize("shape", SHAPES)
def test_nothing_moves_and_the_checksum_is_redone(shape):
    data = _build(shape)
    out = pe.scrub(data)
    a, b = pefile.PE(data=data), pefile.PE(data=out)
    layout = lambda p: [(s.Name, s.VirtualAddress, s.PointerToRawData,  # noqa: E731
                         s.SizeOfRawData) for s in p.sections]
    assert layout(a) == layout(b)
    if a.OPTIONAL_HEADER.CheckSum:
        assert b.OPTIONAL_HEADER.CheckSum == b.generate_checksum()


@pytest.mark.parametrize("shape", ["msvc", "mingw", "go", "dll"])
def test_two_builds_that_differ_only_in_who_built_them_come_out_identical(shape):
    """A1 in miniature, same-length names: link time, PDB path and GUID, REPRO
    hash, Rich key, company, copyright and file name all differ going in."""
    a, b = _build(shape, "alice"), _build(shape, "carol")
    assert a != b
    assert pe.scrub(a) == pe.scrub(b)


@pytest.mark.parametrize("shape", SHAPES)
def test_scrubbing_twice_changes_nothing(shape):
    once = pe.scrub(_build(shape))
    assert pe.scrub(once) == once


def test_a_dlls_export_stamp_goes():
    out = pe.scrub(_build("dll"))
    p = pefile.PE(data=out)
    assert p.DIRECTORY_ENTRY_EXPORT.struct.TimeDateStamp == 0


# --------------------------------------------------------------------------- #
# What stays, and is said
# --------------------------------------------------------------------------- #
def test_a_program_that_can_read_its_version_info_keeps_it_and_says_so():
    data = _build("msvc", reads_version=True)
    out = pe.scrub(data)
    assert _u16(pc.company("alice")) in out
    assert pc.pdb_path("alice") not in out
    assert any("version.dll" in a for a in pe.advise(data))
    assert pe.residuals(out) == []


def test_program_text_is_kept_and_reported():
    data = _build("mingw", text_path=True)
    out = pe.scrub(data)
    assert pc.home_unix("alice") + b"/hello.c" in out
    assert any("own text" in a for a in pe.advise(data))


def test_the_report_names_what_the_build_left():
    found = pe.describe(_build("msvc"))
    assert found["PDB path"] == pc.pdb_path("alice").decode()
    assert found["Link time (COFF header)"] == str(pc.link_time("alice"))
    assert found["Rich header"].startswith("3 ")
    assert found["Version: CompanyName"] == pc.company("alice")
    found = pe.describe(_build("mingw"))
    assert pc.SOURCE_FILE.decode() in found["Source file names (COFF symbols)"]


# --------------------------------------------------------------------------- #
# Fail closed
# --------------------------------------------------------------------------- #
def test_an_authenticode_signed_program_is_refused():
    with pytest.raises(UnsupportedFormatError, match="Authenticode"):
        pe.scrub(_build("msvc", signed=True))


def test_a_dotnet_assembly_is_refused():
    with pytest.raises(UnsupportedFormatError, match=r"\.NET"):
        pe.scrub(_build("msvc", dotnet=True))


def test_a_debug_section_the_program_relocates_is_refused():
    with pytest.raises(ParseError, match="referenced"):
        pe.scrub(_build("mingw", reloc_into_debug=True))


@pytest.mark.parametrize("cut", [0.1, 0.5, 0.95])
def test_a_truncated_file_is_refused(cut):
    data = _build("mingw")
    with pytest.raises(ParseError):
        pe.scrub(data[:int(len(data) * cut)])


def test_a_dos_program_is_not_taken_for_a_windows_one():
    dos = b"MZ" + bytes(0x3A) + struct.pack("<I", 0x40) + b"\xcd\x20" * 32
    assert not pe.is_pe(dos)
    assert not ExeHandler().claims(dos)


def test_residuals_catch_a_stamp_and_a_stale_checksum():
    data = _build("mingw")
    out = bytearray(pe.scrub(data))
    p = pe.parse(bytes(out))
    struct.pack_into("<I", out, p.coff + 4, pc.link_time("alice"))
    found = pe.residuals(bytes(out))
    assert any("link time" in r for r in found)
    assert any("checksum" in r for r in found)


# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #
def test_the_dispatcher_sends_pe_here_and_the_cli_writes_it(tmp_path):
    data = _build("msvc")
    assert isinstance(default_dispatcher().resolve(data), ExeHandler)
    src, out = tmp_path / "prog.exe", tmp_path / "clean.exe"
    src.write_bytes(data)
    cli.scrub_file(str(src), str(out), "F1")
    assert out.read_bytes() == pe.scrub(data)


# --------------------------------------------------------------------------- #
# Real programs under Wine
# --------------------------------------------------------------------------- #
needs_wine = pytest.mark.skipif(not (pc.wine() and pc.have_mingw()),
                                reason="needs Linux, Wine and mingw-w64")


def _same_run(original: str, data: bytes, tmp_path, name: str) -> None:
    clean = tmp_path / name
    clean.write_bytes(data)
    for args in ((), ("one", "two", "three")):
        assert pc.run(original, *args) == pc.run(str(clean), *args), args


@needs_wine
@pytest.mark.parametrize("flags,rc", [(("-g",), False), (("-O2",), True)],
                         ids=["g", "O2-version-resource"])
def test_a_real_mingw_build_runs_the_same_and_two_builders_converge(flags, rc,
                                                                    tmp_path):
    a = pc.compile_c(str(tmp_path / "alice" / "proj"), flags, rc=rc)
    b = pc.compile_c(str(tmp_path / "carol" / "proj"), flags, rc=rc)
    da = open(a, "rb").read()
    ca, cb = pe.scrub(da), pe.scrub(open(b, "rb").read())
    assert pe.residuals(ca) == []
    assert b"/alice/" not in ca and _u16("SENTINEL-AUTHOR") not in ca
    _same_run(a, ca, tmp_path, "ca.exe")
    assert ca == cb, "two build directories of equal length must converge"


@pytest.mark.skipif(not (pc.wine() and shutil.which("go")),
                    reason="needs Linux, Wine and go")
def test_a_real_go_build_for_windows_runs_the_same(tmp_path):
    src = tmp_path / "main.go"
    src.write_text('package main\nimport ("fmt"; "os")\n'
                   'func main() { fmt.Println("hello", len(os.Args)); '
                   'os.Exit(len(os.Args)) }\n')
    env = dict(os.environ, GOOS="windows", GOARCH="amd64",
               GOCACHE=str(tmp_path / "gc"), HOME=str(tmp_path), GOFLAGS="")
    subprocess.run(["go", "build", "-o", "tool.exe", "main.go"], cwd=tmp_path,
                   env=env, check=True, capture_output=True)
    data = open(tmp_path / "tool.exe", "rb").read()
    out = pe.scrub(data)
    assert pe.residuals(out) == []
    _same_run(str(tmp_path / "tool.exe"), out, tmp_path, "clean.exe")


def _pip_launchers() -> list[str]:
    import pip
    return sorted(glob.glob(os.path.join(os.path.dirname(pip.__file__), "_vendor",
                                         "distlib", "*.exe")))


@pytest.mark.parametrize("name", ["t64.exe", "w64.exe", "t32.exe", "w64-arm.exe"])
def test_pips_own_msvc_launchers_lose_their_authors_folder(name, tmp_path):
    """Real MSVC builds, on every machine with pip: a Rich header, and a CodeView
    path naming the author's Windows user folder (survey §4.5)."""
    path = next((p for p in _pip_launchers() if p.endswith(os.sep + name)), None)
    if path is None:
        pytest.skip(f"{name} not vendored by this pip")
    data = open(path, "rb").read()
    assert b"\\Users\\" in data and pe.rich_header(data, pe.parse(data))
    out = pe.scrub(data)
    assert b"\\Users\\" not in out and pe.residuals(out) == []
    if name == "t64.exe" and pc.wine():
        _same_run(path, out, tmp_path, name)


# --------------------------------------------------------------------------- #
# The survey's own builds, where they exist
# --------------------------------------------------------------------------- #
SURVEY = os.path.expanduser(os.environ.get(
    "PE_SAMPLES", "~/metadata-research/step2/exe/pe/out/alice"))
SURVEY_FILES = ["mingw_O2.exe", "mingw_g.exe", "mingw_s.exe", "mingw_nots.exe",
                "mingw_rc.exe", "clang_pdb.exe", "go_win.exe"]


@pytest.mark.parametrize("name", SURVEY_FILES)
def test_the_survey_builds_scrub_clean(name):
    path = os.path.join(SURVEY, name)
    if not os.path.exists(path):
        pytest.skip(f"{name}: survey build not on this machine (p5 plan §0)")
    out = pe.scrub(open(path, "rb").read())
    assert pe.residuals(out) == []
    assert b"/home/alice/proj" not in out.replace(b"/home/alice/proj/hello.go", b"")
