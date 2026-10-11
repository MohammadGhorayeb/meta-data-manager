"""Folder mode: a directory in, a cleaned directory and one results sheet out.

Phase 6 M7 (`docs/p6_tail_plan.md` §4 D6, §12). A folder is treated the way the ZIP
handler treats an archive, because it is the same thing on a disk: every file is
cleaned by its own format's handler, through the same single-file path the CLI
uses (dispatch, scrub, residual check, report, the ExifTool cross-check), and
written to the same relative path under the output folder.

What decides each file's row in the sheet:

| outcome | when | written? |
|---|---|---|
| cleaned | a handler scrubbed it and its residual check passed | yes |
| kept as text | no handler, and it is plain text (ZIP's rule, limit #58) | yes, as is |
| kept unscrubbed | no handler, binary, and `--keep-unknown-members` | yes, as is |
| not supported | no handler, binary | no |
| refused | its handler refused it (the reason is in the row) | no |
| dropped | a sidecar: Finder's `.DS_Store`, AppleDouble `._*`, `Thumbs.db` | no |
| skipped | a symbolic link (never followed) or a special file | no |
| error | anything unexpected; the run goes on | no |

**One bad file never stops the run**, and nothing is written for a file that was
not cleaned (fail closed, per file). Rows are written as files finish, so an
interrupted run still leaves a sheet of what it did.

**The sheet names fields, never values.** The single-file report echoes the values
it removed, which is useful on a terminal and a disclosure in a file; a results
sheet saved beside cleaned files and shared with them would hand the author's name
and the GPS position straight back. So the sheet says *that* `EXIF:GPSLatitude`
was removed from `photos/a.jpg`, not where the photo was taken.

**Files are written new**, so nothing the file system kept beside the input --
macOS extended attributes such as the download address and quarantine record -- is
copied (measured, §12). Permissions follow `cli.output_mode`: the user's default,
plus the executable bit where the input had one.
"""
from __future__ import annotations

import csv
import os
import stat
from dataclasses import dataclass, field

from . import cli
from . import report as rep
from .dispatch import default_dispatcher
from .errors import ScrubError, UnsupportedFormatError
from .formats.zip import f1 as zip_f1

SHEET = "scrub-results.csv"
COLUMNS = ["path", "outcome", "format", "fidelity", "bytes_before", "bytes_after",
           "removed", "removed_fields", "changed_fields", "kept_fields",
           "independent_check", "notes"]

CLEANED, TEXT, UNSCRUBBED = "cleaned", "kept as text", "kept unscrubbed"
UNSUPPORTED, REFUSED, DROPPED = "not supported", "refused", "dropped"
SKIPPED, ERROR = "skipped", "error"
WRITTEN = {CLEANED, TEXT, UNSCRUBBED}


@dataclass
class Summary:
    rows: list[dict] = field(default_factory=list)
    sheet: str = ""

    def count(self, outcome: str) -> int:
        return sum(1 for r in self.rows if r["outcome"] == outcome)

    @property
    def complete(self) -> bool:
        """Every file that is not a sidecar was written out."""
        return all(r["outcome"] in WRITTEN | {DROPPED} for r in self.rows)


def check_paths(src: str, dst: str) -> None:
    """Refuse a layout that could overwrite or recurse: the output may not exist
    or must be empty, and neither folder may contain the other."""
    if not os.path.isdir(src):
        raise ValueError(f"{src} is not a folder")
    a, b = os.path.realpath(src), os.path.realpath(dst)
    if a == b or b.startswith(a + os.sep) or a.startswith(b + os.sep):
        raise ValueError("the output folder may not be the input folder, inside it, "
                         "or around it")
    if os.path.exists(dst) and (not os.path.isdir(dst) or os.listdir(dst)):
        raise ValueError(f"{dst} exists and is not an empty folder; nothing was "
                         "written")


def walk(src: str):
    """Every entry under `src`, as (relative POSIX path, absolute path), in sorted
    order, without following links."""
    for root, dirs, files in os.walk(src, followlinks=False):
        dirs.sort()
        rel_root = os.path.relpath(root, src)
        for name in sorted(files + [d for d in dirs
                                    if os.path.islink(os.path.join(root, d))]):
            rel = name if rel_root == "." else f"{rel_root}/{name}"
            yield rel.replace(os.sep, "/"), os.path.join(root, name)


def _row(path: str, outcome: str, **kw) -> dict:
    row = dict.fromkeys(COLUMNS, "")
    row.update(path=path, outcome=outcome, **{k: v for k, v in kw.items() if v != ""})
    return row


def _fields(report: rep.Report, status: str) -> str:
    return "; ".join(i.locus for i in report.items if i.status == status)


def _check(report: rep.Report) -> str:
    c = report.check
    if c is None:
        return "not run"
    if not c.available:
        return "ExifTool not available"
    if c.leaked:
        return (f"DISAGREES: {len(c.leaked)} removed value(s) still readable by "
                "ExifTool -- treat this file as not cleaned")
    return f"ExifTool {c.version}: none of the removed values found"


def clean_one(rel: str, src_path: str, dst_path: str, fidelity: str, *,
              dispatcher, keep_unknown: bool, check_memory: bool,
              verify: bool) -> dict:
    """One file's row; writes the cleaned file when there is one to write."""
    st = os.lstat(src_path)
    if stat.S_ISLNK(st.st_mode):
        return _row(rel, SKIPPED, notes="a symbolic link; not followed")
    if not stat.S_ISREG(st.st_mode):
        return _row(rel, SKIPPED, notes="not a regular file")
    with open(src_path, "rb") as f:
        head = f.read(64)                  # enough for every sidecar's magic
    what = zip_f1.sidecar(rel, head)       # decided by the bytes, as in a ZIP
    if what:
        return _row(rel, DROPPED, format="", bytes_before=st.st_size,
                    notes=f"{what}: never the files themselves")
    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    if st.st_size == 0:
        cli._write_atomic(dst_path, b"", cli.output_mode(src_path))
        return _row(rel, CLEANED, bytes_before=0, bytes_after=0, removed=0,
                    notes="empty: nothing to remove")
    try:
        _adv, report = cli.scrub_file_reported(
            src_path, dst_path, fidelity, dispatcher,
            verify_with_exiftool=verify, check_memory=check_memory,
            keep_unknown_members=keep_unknown)
    except UnsupportedFormatError:
        with open(src_path, "rb") as f:
            data = f.read()
        if zip_f1.is_text(data):
            cli._write_atomic(dst_path, data, cli.output_mode(src_path))
            return _row(rel, TEXT, bytes_before=len(data), bytes_after=len(data),
                        notes="plain text has no metadata container; what it says "
                              "is content (limit #58)")
        if keep_unknown:
            cli._write_atomic(dst_path, data, cli.output_mode(src_path))
            return _row(rel, UNSCRUBBED, bytes_before=len(data),
                        bytes_after=len(data),
                        notes="a type no handler reads, copied byte for byte and "
                              "NOT scrubbed, as asked (--keep-unknown-members)")
        return _row(rel, UNSUPPORTED, bytes_before=len(data),
                    notes="a type no handler reads; not copied "
                          "(--keep-unknown-members copies such files unscrubbed)")
    except ScrubError as exc:
        return _row(rel, REFUSED, bytes_before=st.st_size,
                    notes=f"{type(exc).__name__}: {exc}")
    except Exception as exc:                              # noqa: BLE001
        return _row(rel, ERROR, bytes_before=st.st_size,
                    notes=f"unexpected {type(exc).__name__}: {exc}")
    if report.check is not None and report.check.leaked:
        # The independent reader disagrees: the file was written, so say it loudly
        # in the outcome as well as the check column.
        outcome = REFUSED
        os.unlink(dst_path)
    else:
        outcome = CLEANED
    return _row(rel, outcome, format=report.format_id, fidelity=report.fidelity,
                bytes_before=report.size_before, bytes_after=report.size_after,
                removed=len(report.removed), removed_fields=_fields(report, rep.REMOVED),
                changed_fields=_fields(report, rep.CHANGED),
                kept_fields=_fields(report, rep.KEPT), independent_check=_check(report),
                notes=" | ".join(report.advisories))


def clean_folder(src: str, dst: str, fidelity: str = "F1", *,
                 keep_unknown: bool = False, check_memory: bool = True,
                 verify: bool = True, progress=None) -> Summary:
    """Clean every file under `src` into `dst`, writing the sheet as it goes."""
    check_paths(src, dst)
    os.makedirs(dst, exist_ok=True)
    entries = sorted(walk(src))             # one order, whatever the disk's
    taken = {rel for rel, _ in entries}
    name, n = SHEET, 1
    while name in taken:
        n += 1
        name = f"scrub-results-{n}.csv"
    summary = Summary(sheet=os.path.join(dst, name))
    d = default_dispatcher(keep_unknown_members=keep_unknown, check_memory=check_memory)
    with open(summary.sheet, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for rel, path in entries:
            row = clean_one(rel, path, os.path.join(dst, *rel.split("/")), fidelity,
                            dispatcher=d, keep_unknown=keep_unknown,
                            check_memory=check_memory, verify=verify)
            summary.rows.append(row)
            writer.writerow(row)
            f.flush()
            if progress is not None:
                progress(row)
    return summary
