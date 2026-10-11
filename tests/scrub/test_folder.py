"""Folder mode (Phase 6 M7): a directory in, a cleaned directory and one results
sheet out; one bad file never stops the run; the sheet names fields, not values."""
from __future__ import annotations

import csv
import os
import shutil
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from src.scrub import cli, folder  # noqa: E402
from tests.scrub import epub_corpus as ec  # noqa: E402
from tests.scrub import zip_corpus as zc  # noqa: E402


def _tree(root) -> dict[str, bytes]:
    files = {
        "photos/trip/beach.jpg": zc.photo(1),
        "photos/trip/broken.jpg": zc.photo(1)[:300],
        "photos/icon.png": zc.png(1),
        "photos/.DS_Store": zc.ds_store(1),
        "photos/._beach.jpg": zc.appledouble({"com.apple.quarantine": zc.quarantine(1)}),
        "docs/book.epub": ec.build(1, shape="calibre"),
        "docs/archive.zip": zc.build(1, shape="infozip"),
        "docs/blob.bin": bytes(range(256)),
        "docs/empty.txt": b"",
        "Café notes/todo list.txt": b"buy milk\n",
        "run.sh": b"#!/bin/sh\necho hi\n",
    }
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    (root / "run.sh").chmod(0o755)
    os.symlink("photos/icon.png", root / "link.png")
    return files


def _rows(summary) -> dict[str, dict]:
    return {r["path"]: r for r in summary.rows}


@pytest.fixture
def run(tmp_path):
    src, dst = tmp_path / "in", tmp_path / "out"
    _tree(src)
    summary = folder.clean_folder(str(src), str(dst), verify=False)
    return src, dst, summary


def test_every_file_gets_a_row_and_its_outcome(run):
    _src, _dst, summary = run
    outcome = {p: r["outcome"] for p, r in _rows(summary).items()}
    assert outcome == {
        "Café notes/todo list.txt": folder.TEXT,
        "docs/archive.zip": folder.CLEANED,
        "docs/blob.bin": folder.UNSUPPORTED,
        "docs/book.epub": folder.CLEANED,
        "docs/empty.txt": folder.CLEANED,
        "link.png": folder.SKIPPED,
        "photos/._beach.jpg": folder.DROPPED,
        "photos/.DS_Store": folder.DROPPED,
        "photos/icon.png": folder.CLEANED,
        "photos/trip/beach.jpg": folder.CLEANED,
        "photos/trip/broken.jpg": folder.REFUSED,
        "run.sh": folder.TEXT,
    }
    assert [r["path"] for r in summary.rows] == sorted(outcome)
    assert not summary.complete


def test_only_what_was_written_out_is_in_the_output(run):
    _src, dst, summary = run
    written = sorted(str(p.relative_to(dst)) for p in dst.rglob("*") if p.is_file())
    expected = sorted([r["path"] for r in summary.rows
                       if r["outcome"] in folder.WRITTEN] + [folder.SHEET])
    assert written == expected


def test_the_cleaned_files_are_clean(run):
    _src, dst, _summary = run
    planted = [zc.secret(n, 1).encode() for n in ("PHOTO-ARTIST", "PHOTO-SOFT",
                                                   "PNG-AUTHOR", "CREATOR")]
    for p in dst.rglob("*"):
        if p.is_file():
            data = p.read_bytes()
            assert not [s for s in planted if any(s in b for b in zc.expanded(data))], p


def test_the_sheet_names_fields_never_values(run):
    _src, dst, summary = run
    text = (dst / folder.SHEET).read_text(encoding="utf-8-sig")
    assert "EXIF:Artist" in text                       # the field, named
    for value in (zc.secret("PHOTO-ARTIST", 1), "40° 42'", zc.secret("CREATOR", 1),
                  "buy milk"):
        assert value not in text
    rows = list(csv.DictReader(text.splitlines()))
    assert [r["path"] for r in rows] == [r["path"] for r in summary.rows]


def test_executable_bits_follow_the_input(run):
    _src, dst, _summary = run
    assert os.stat(dst / "run.sh").st_mode & 0o111
    assert not os.stat(dst / "photos/icon.png").st_mode & 0o111


def test_keep_unknown_copies_binaries_unscrubbed(tmp_path):
    src = tmp_path / "in"
    _tree(src)
    summary = folder.clean_folder(str(src), str(tmp_path / "out"), verify=False,
                                  keep_unknown=True)
    assert _rows(summary)["docs/blob.bin"]["outcome"] == folder.UNSCRUBBED
    assert (tmp_path / "out/docs/blob.bin").read_bytes() == bytes(range(256))


def test_one_unexpected_error_does_not_stop_the_run(tmp_path, monkeypatch):
    src = tmp_path / "in"
    _tree(src)
    real = cli.scrub_file_reported

    def boom(in_path, *a, **k):
        if in_path.endswith("icon.png"):
            raise RuntimeError("handler crashed")
        return real(in_path, *a, **k)
    monkeypatch.setattr(cli, "scrub_file_reported", boom)
    rows = _rows(folder.clean_folder(str(src), str(tmp_path / "out"), verify=False))
    assert rows["photos/icon.png"]["outcome"] == folder.ERROR
    assert rows["photos/trip/beach.jpg"]["outcome"] == folder.CLEANED
    assert not (tmp_path / "out/photos/icon.png").exists()


def test_an_independent_disagreement_fails_the_file_closed(tmp_path, monkeypatch):
    """If ExifTool still reads a value the report says is gone, the file is not
    shipped, whatever our own check said."""
    src = tmp_path / "in"
    (src / "a").mkdir(parents=True)
    (src / "a/p.jpg").write_bytes(zc.photo(1))
    from src.scrub import crosscheck

    def disagree(*_a, **_k):
        return crosscheck.CrossCheck(available=True, version="x",
                                     leaked=[("value", "EXIF")])
    monkeypatch.setattr(crosscheck, "run", disagree)
    rows = _rows(folder.clean_folder(str(src), str(tmp_path / "out")))
    assert rows["a/p.jpg"]["outcome"] == folder.REFUSED
    assert "DISAGREES" in rows["a/p.jpg"]["independent_check"]
    assert not (tmp_path / "out/a/p.jpg").exists()


@pytest.mark.parametrize("layout", ["inside", "around", "same", "not_empty"])
def test_dangerous_layouts_are_refused_before_anything_is_written(tmp_path, layout):
    src = tmp_path / "in"
    _tree(src)
    dst = {"inside": src / "out", "around": tmp_path, "same": src,
           "not_empty": tmp_path / "full"}[layout]
    if layout == "not_empty":
        dst.mkdir()
        (dst / "x").write_bytes(b"x")
    before = sorted(str(p) for p in tmp_path.rglob("*"))
    with pytest.raises(ValueError):
        folder.clean_folder(str(src), str(dst))
    assert sorted(str(p) for p in tmp_path.rglob("*")) == before


def test_the_sheet_never_overwrites_an_input_of_the_same_name(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    (src / folder.SHEET).write_text("a,b\n1,2\n")
    summary = folder.clean_folder(str(src), str(tmp_path / "out"), verify=False)
    assert os.path.basename(summary.sheet) == "scrub-results-2.csv"
    assert (tmp_path / "out" / folder.SHEET).read_text() == "a,b\n1,2\n"


def test_cli_folder_mode_exit_codes(tmp_path, capsys):
    src = tmp_path / "in"
    (src / "p").mkdir(parents=True)
    (src / "p/a.jpg").write_bytes(zc.photo(1))
    assert cli.main([str(src), str(tmp_path / "ok"), "--no-verify"]) == 0
    out = capsys.readouterr().out
    assert "cleaned" in out and "results sheet" in out
    assert zc.secret("PHOTO-ARTIST", 1) not in out          # fields, never values
    (src / "p/b.bin").write_bytes(bytes(range(256)))
    assert cli.main([str(src), str(tmp_path / "partial"), "--no-verify"]) == 8
    assert cli.main([str(src), str(src / "inside")]) == 2


def test_a_single_cleaned_program_stays_executable(tmp_path):
    from tests.scrub import elf_corpus as elc
    src = tmp_path / "tool"
    src.write_bytes(elc.build("alice"))
    src.chmod(0o755)
    cli.scrub_file(str(src), str(tmp_path / "clean"), "F1")
    assert os.stat(tmp_path / "clean").st_mode & 0o111


@pytest.mark.skipif(sys.platform != "darwin" or shutil.which("xattr") is None,
                    reason="macOS extended attributes")
def test_macos_download_attributes_do_not_follow_the_file(tmp_path):
    """Measured (§12): the input's download address and quarantine record stay
    with the input. (macOS stamps its own provenance tag on every new file, ours
    included; that is the session's, not the input's -- limit #64.)"""
    src = tmp_path / "in"
    src.mkdir()
    photo = src / "beach.jpg"
    photo.write_bytes(zc.photo(1))
    subprocess.run(["xattr", "-w", "com.apple.metadata:kMDItemWhereFroms",
                    "https://example.invalid/beach.jpg", str(photo)], check=True)
    subprocess.run(["xattr", "-w", "com.apple.quarantine", "0083;0;Safari;X",
                    str(photo)], check=True)
    folder.clean_folder(str(src), str(tmp_path / "out"), verify=False)
    names = subprocess.run(["xattr", str(tmp_path / "out/beach.jpg")],
                           capture_output=True, text=True).stdout.split()
    assert "com.apple.quarantine" not in names
    assert "com.apple.metadata:kMDItemWhereFroms" not in names
