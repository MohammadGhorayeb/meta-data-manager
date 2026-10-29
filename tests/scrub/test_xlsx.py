"""Excel workbooks at F1 (`docs/office_plan.md`, O2).

Every locus in `xlsx_corpus` was a probe first (§2): planted, run through MAT2 and
ExifTool, and measured. Here each is planted again and must be gone after F1 -- or
kept and reported, or refused -- while every cell comes out byte-identical.
"""
from __future__ import annotations

import re
import shutil
import subprocess

import pytest

from src.scrub.dispatch import default_dispatcher
from src.scrub.errors import ContentError
from src.scrub.formats.ooxml import opc, xmlspans
from src.scrub.formats.xlsx import f1
from src.scrub.formats.xlsx.handler import XlsxHandler
from tests.scrub import xlsx_corpus as xc


def _all_bytes(data: bytes) -> bytes:
    return b"\n".join(xc.members(data).values())


# --------------------------------------------------------------------------- #
# 1. What F1 removes
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("locus", xc.REMOVED)
def test_each_planted_locus_is_gone(locus):
    out = f1.scrub(xc.build(1, loci=(locus,)))
    everything = _all_bytes(out)
    for value in xc.planted(1, locus):
        assert value not in everything, (locus, value)
    assert f1.residuals(out) == []


def test_two_workbooks_differing_only_in_metadata_come_out_identical():
    """The differential test: same cells, same picture pixels, every locus planted
    with different values -- one output, byte for byte."""
    assert f1.scrub(xc.build(1)) == f1.scrub(xc.build(2))


def test_f1_is_a_fixed_point():
    out = f1.scrub(xc.build(1))
    assert f1.scrub(out) == out


def test_window_and_cursor_are_gone():
    m = xc.members(f1.scrub(xc.build(1, loci=("window", "selection"))))
    assert not re.search(rb"\s(?:xWindow|yWindow|windowWidth|windowHeight)=",
                         m["xl/workbook.xml"])
    assert b"<selection" not in m["xl/worksheets/sheet1.xml"]


def test_the_save_folder_goes_with_its_wrapper():
    """`x15ac:absPath` is all its `mc:AlternateContent` holds, so the wrapper goes
    too: an empty `mc:Choice` left behind would mark the file as edited."""
    wb = xc.members(f1.scrub(xc.build(1, loci=("abspath",))))["xl/workbook.xml"]
    assert b"absPath" not in wb and b"AlternateContent" not in wb


def test_protection_stays_but_the_password_goes():
    m = xc.members(f1.scrub(xc.build(1, loci=("protection",))))
    assert b'<workbookProtection lockStructure="1"/>' in m["xl/workbook.xml"]
    assert b'<sheetProtection sheet="1"/>' in m["xl/worksheets/sheet1.xml"]
    assert b"readOnlyRecommended" in m["xl/workbook.xml"]


def test_printer_settings_go_with_the_reference_to_them():
    m = xc.members(f1.scrub(xc.build(1, loci=("printer_settings",))))
    assert not any(n.startswith("xl/printerSettings/") for n in m)
    assert b'<pageSetup orientation="portrait"/>' in m["xl/worksheets/sheet1.xml"]


def test_text_no_cell_uses_is_blanked_in_place():
    """Blanked, not removed: every later string keeps its index, so no cell changes."""
    before = xc.members(xc.build(1, loci=("orphan_string", "hidden_sheet")))
    after = xc.members(f1.scrub(xc.build(1, loci=("orphan_string", "hidden_sheet"))))
    sst = after["xl/sharedStrings.xml"]
    assert sst.count(b"<si>") == before["xl/sharedStrings.xml"].count(b"<si>")
    assert b"<si><t/></si>" in sst
    assert b"Text on a hidden sheet" in sst          # used by a hidden sheet: kept


def test_strings_count_as_used_wherever_the_sheet_is_stored():
    """A sheet at an unusual path still uses its strings: only a cell anywhere in
    the package decides, never the folder the part sits in."""
    sheet = (f'<worksheet xmlns="{xc.X}"><sheetData><row r="1"><c r="A1" t="s">'
             '<v>2</v></c></row></sheetData></worksheet>').encode()
    assert f1._referenced_strings({"xl/deep/elsewhere/sheet9.xml": sheet}) == {2}


# --------------------------------------------------------------------------- #
# 2. Content: every cell byte-identical, and the workbook still opens
# --------------------------------------------------------------------------- #
def _sheet_data(part: bytes) -> bytes:
    els = xmlspans.parse(part)
    el = next(e for e in els if e.uri == xc.X and e.local == "sheetData")
    return part[el.start:el.end]


def test_every_cell_is_byte_identical():
    data = xc.build(1)
    before, after = xc.members(data), xc.members(f1.scrub(data))
    for name in before:
        if name.startswith("xl/worksheets/") and name.endswith(".xml"):
            assert _sheet_data(after[name]) == _sheet_data(before[name]), name


def test_surviving_parts_only_lose_bytes():
    """F1 only deletes: a part it kept is identical or shorter, never rewritten."""
    data = xc.build(1)
    before, after = xc.members(data), xc.members(f1.scrub(data))
    for name, body in after.items():
        if name in before and not name.startswith("xl/media/"):
            assert len(body) <= len(before[name]), name


@pytest.mark.skipif(shutil.which("soffice") is None, reason="soffice absent")
def test_libreoffice_shows_the_same_cells(tmp_path):
    data = xc.build(1)
    (tmp_path / "in.xlsx").write_bytes(data)
    (tmp_path / "out.xlsx").write_bytes(f1.scrub(data))
    profile = f"-env:UserInstallation=file://{tmp_path}/lo"
    for name in ("in", "out"):
        subprocess.run(["soffice", profile, "--headless", "--convert-to", "csv",
                        "--outdir", str(tmp_path), str(tmp_path / f"{name}.xlsx")],
                       capture_output=True, timeout=180, check=False)
    before, after = tmp_path / "in.csv", tmp_path / "out.csv"
    assert before.exists() and after.exists()
    assert after.read_bytes() == before.read_bytes()
    assert b"Quarterly total,42,84" in after.read_bytes()


# --------------------------------------------------------------------------- #
# 3. Kept and reported; refused
# --------------------------------------------------------------------------- #
def test_kept_content_is_kept_and_reported():
    out = f1.scrub(xc.build(1, loci=xc.KEPT))
    kept = " | ".join(f1.advisories(out))
    for phrase in ("hidden sheet", "hidden row", "header/footer", "hidden defined name",
                   "external link target", "fileVersion"):
        assert phrase in kept, phrase
    assert b"file:///\\\\fileserver\\finance\\q3.xlsx" in _all_bytes(out)


@pytest.mark.parametrize("locus", xc.REFUSED)
def test_refused_loci_are_refused_naming_why(locus):
    with pytest.raises(ContentError, match="refused at F1"):
        f1.scrub(xc.build(1, loci=(locus,)))


# --------------------------------------------------------------------------- #
# 4. Dispatch
# --------------------------------------------------------------------------- #
def test_the_dispatcher_hands_a_workbook_to_excel_not_to_the_archive_handler():
    data = xc.build(1)
    assert isinstance(default_dispatcher().resolve(data), XlsxHandler)
    assert opc.parse(data).flavour == "xlsx"


# --------------------------------------------------------------------------- #
# 5. The benchmark, so its row in docs/office_plan.md §2 goes stale loudly
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(shutil.which("mat2") is None, reason="mat2 absent")
def test_mat2_leaves_the_save_folder_and_refuses_a_pivot_cache(tmp_path):
    clean = tmp_path / "a.xlsx"
    clean.write_bytes(xc.build(1, loci=("properties", "abspath", "revision_ids")))
    subprocess.run(["mat2", str(clean)], capture_output=True, timeout=120, check=False)
    out = tmp_path / "a.cleaned.xlsx"
    assert out.exists()
    left = _all_bytes(out.read_bytes())
    assert xc.secret("AUTHOR", 1).encode() not in left
    assert xc.secret("USER", 1).encode() in left, "MAT2 now removes absPath; §2 is stale"

    pivot = tmp_path / "b.xlsx"
    pivot.write_bytes(xc.build(1, loci=("pivot_records",)))
    subprocess.run(["mat2", str(pivot)], capture_output=True, timeout=120, check=False)
    assert not (tmp_path / "b.cleaned.xlsx").exists(), "MAT2 now cleans pivot caches"
