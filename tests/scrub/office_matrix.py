"""The Excel and PowerPoint Pareto matrices, from real harness runs (O5,
`docs/office_plan.md` §6). `gen_matrix_xlsx` and `gen_matrix_pptx` are the entry
points `scripts/check_evidence.py` finds; this module holds what they share.

Measured here:
  - A1@F1   differential leak oracle, in every producer shape this machine can write:
            the Excel-wired corpus with every removed and kept locus planted
            (`xlsx_corpus` / `pptx_corpus`); that corpus RE-SAVED by LibreOffice (a
            real producer's rewrite of the same planted metadata -- it keeps 11 of
            the workbook's 27 planted values and 10 of the deck's 26); and files
            written natively by openpyxl and XlsxWriter (workbooks) or python-pptx
            (decks), with their own document properties set per variant. Variants
            differ only in metadata; every shape must come out byte-identical.
  - A2@F1   E-XLSX / E-PPTX (`e_office.py`), reported per channel: packager, model,
            size, ExifTool's fields.

Not measured, and said so: F2 and F3 are not built (`not_tested`). Microsoft Office
is not a producer in either set (`e_office.ABSENT`): the cells say what was not
tested rather than implying it was.
"""
from __future__ import annotations

import os
import shutil

from tests.harness import config
from tests.harness.contract import Cell, V
from tests.harness.oracle import fingerprint_guard, leak
from tests.harness.runner import matrix
from tests.scrub import e_office as E
from tests.scrub import pptx_corpus as pc
from tests.scrub import xlsx_corpus as xc

TOOL = {
    "name": "irreversible_scrubber",
    "version": "0.1.0-o5",
    "invocation": "python -m src.scrub {in} {out} --fidelity {fidelity}",
}
CORPUS = {"xlsx": xc, "pptx": pc}
# The planted corpora: wired the way Office wires them, not written by Office.
NATIVE = {"xlsx": "excel-layout", "pptx": "powerpoint-layout"}


def _scrubber():
    from tests.scrub.test_harness_a1 import InProcessScrubber
    return InProcessScrubber()


def plugin(kind: str):
    return E._plugin(kind)[0]


def _meta(v: int, kind: str) -> dict:
    s = xc.secret
    if kind == "pptx":
        return {"author": s("AUTHOR", v), "last_modified_by": s("EDITOR", v),
                "title": s("TITLE", v), "subject": s("SUBJECT", v),
                "comments": s("NOTE", v), "keywords": s("KEYWORDS", v),
                "category": s("CATEGORY", v), "content_status": s("STATUS", v),
                "identifier": s("IDENT", v), "revision": 10 + v}
    return {"creator": s("AUTHOR", v), "lastModifiedBy": s("EDITOR", v),
            "title": s("TITLE", v), "subject": s("SUBJECT", v),
            "description": s("NOTE", v), "keywords": s("KEYWORDS", v),
            "category": s("CATEGORY", v), "manager": s("MANAGER", v),
            "company": s("COMPANY", v), "custom": {"Client": s("CLIENT", v)}}


def shapes(kind: str) -> dict:
    """shape -> writer(variant, dst, work). Only what this machine can run."""
    corpus = CORPUS[kind]
    out = {NATIVE[kind]: lambda v, dst, work: open(dst, "wb").write(corpus.build(v))}
    if shutil.which("soffice"):
        def resaved(v, dst, work):
            src = os.path.join(work, f"source.{kind}")
            with open(src, "wb") as f:
                f.write(corpus.build(v))
            shutil.move(E._soffice(src, kind, work), dst)
        out["libreoffice"] = resaved
    if kind == "xlsx":
        if E.has("openpyxl"):
            out["openpyxl"] = lambda v, dst, work: E.openpyxl_write(
                E.ROWS, dst, _meta(v, kind))
        if E.has("xlsxwriter"):
            out["xlsxwriter"] = lambda v, dst, work: E.xlsxwriter_write(
                E.ROWS, dst, _meta(v, kind))
    elif E.has("pptx"):
        out["python-pptx"] = lambda v, dst, work: E.pptx_write(dst, _meta(v, kind))
    return out


def _variants(kind: str, tmpdir: str, shape: str, make, n: int = 3, repeats: int = 3):
    groups = []
    for i in range(n):
        work = os.path.join(tmpdir, f"a1_{kind}_{shape}_v{i}")
        os.makedirs(work, exist_ok=True)
        first = os.path.join(work, f"v{i}_r0.{kind}")
        make(i + 1, first, work)
        paths = [first]
        for r in range(1, repeats):
            path = os.path.join(work, f"v{i}_r{r}.{kind}")
            shutil.copy(first, path)
            paths.append(path)
        groups.append(paths)
    return groups


def a1_cell(kind: str, tmpdir: str) -> Cell:
    scrubber, plug = _scrubber(), plugin(kind)
    measured, failed, leaks = [], [], []
    for shape, make in shapes(kind).items():
        cell = leak.evaluate_a1(scrubber, plug, _variants(kind, tmpdir, shape, make),
                                "F1", n=3, sentinel_field="metadata_variant",
                                modality="bytes")
        measured.append(shape)
        if cell.verdict != V.PASS:
            failed.append(shape)
            leaks += cell.leaks
    if failed:
        return Cell("A1", "F1", V.FAIL, leaks=leaks,
                    reason="variant-correlated bytes survive in " + ", ".join(failed))
    what = ("document properties, the save folder, revision and session IDs, "
            "protection hashes, window and selection state, personal views, unused "
            "strings, printer settings, pictures' names and metadata, persons, "
            "pivot refresh records, XML comments" if kind == "xlsx" else
            "document properties, the thumbnail, shape/table creation IDs, revision "
            "and co-authoring logs, recent colours, view settings, add-in tags, "
            "printer settings, pictures' names and metadata, comment authors, XML "
            "comments, text-field IDs")
    return Cell("A1", "F1", V.PASS,
                reason=f"no metadata-correlated byte survives in any of "
                       f"{len(measured)} producer shapes ({', '.join(measured)}): {what}")


def _diverse(kind: str, tmpdir: str) -> list[str]:
    """One output per producer, each with its own content: the guard must find
    nothing common to all of them but declared constants and echoed input."""
    paths = []

    def place(name: str) -> tuple[str, str]:
        work = os.path.join(tmpdir, f"div_{kind}_{name}")
        os.makedirs(work, exist_ok=True)
        return os.path.join(work, f"{name}.{kind}"), work

    dst, _ = place("corpus")
    with open(dst, "wb") as f:
        f.write(CORPUS[kind].build(1))
    paths.append(dst)
    if kind == "xlsx":
        # Sizes far apart on purpose: members that land at similar offsets share
        # the high bytes of their header fields, which reads as a mark (the ZIP
        # guard's lesson, docs/p6_tail_plan.md §10).
        tables = {"libreoffice": [("Region", "Sales"), ("North", 1200), ("South", 875)],
                  "openpyxl": [("Name", "Count", "Unit")]
                              + [(f"Part {i:03d}", i * 7, "box") for i in range(60)],
                  "xlsxwriter": [("Month", "Visitors", "Note")]
                                + [(f"Day {i}", i * i, f"Observation number {i} of 400, "
                                    f"written out at length") for i in range(400)]}
        for name, rows in tables.items():
            dst, work = place(name)
            if name == "libreoffice" and shutil.which("soffice"):
                E.calc_write(rows, dst, work)
            elif name == "openpyxl" and E.has("openpyxl"):
                E.openpyxl_write(rows, dst)
            elif name == "xlsxwriter" and E.has("xlsxwriter"):
                E.xlsxwriter_write(rows, dst)
            else:
                continue
            paths.append(dst)
    elif E.has("pptx"):
        from tests.scrub import zip_corpus
        dst, _ = place("python-pptx")
        E.pptx_write(dst, title="Field notes", body="Three sites, eleven samples",
                     picture=zip_corpus.photo(7, seed=40))
        paths.append(dst)
        if shutil.which("soffice"):
            dst, work = place("libreoffice")
            src = os.path.join(work, "source.pptx")
            E.pptx_write(src, title="Budget 2027", body="Hold spending flat",
                         more=tuple((f"Item {i}", "Detail " * (i + 3)) for i in range(6)))
            E.impress_resave(src, dst, work)
            paths.append(dst)
    return paths


def build_doc(kind: str, tmpdir: str) -> dict:
    not_built = (f"{kind}_f2_f3_not_built: a canonical {kind.upper()} model (the "
                 "keys A2@F1 names) is F2's specification; not built")
    sources = E.build_sources(kind, tmpdir)
    cells = [a1_cell(kind, tmpdir),
             Cell("A1", "F2", V.NOT_TESTED, reason=not_built),
             Cell("A1", "F3", V.NOT_TESTED, reason=not_built),
             E.evaluate_cell(kind, "F1", sources, tmpdir),
             Cell("A2", "F2", V.NOT_TESTED, reason=not_built),
             Cell("A2", "F3", V.NOT_TESTED, reason=not_built)]
    verdict, signatures = fingerprint_guard.evaluate(
        _scrubber(), plugin(kind), _diverse(kind, tmpdir), "F1",
        min_len=config.MIN_SIG_LEN)
    return matrix.assemble(kind, TOOL, cells,
                           matrix.fingerprint_block(verdict, signatures, []))


def main(kind: str) -> str:
    import tempfile
    doc = build_doc(kind, tempfile.mkdtemp(prefix=f"{kind}_matrix_"))
    out_path = os.path.join(str(config.RESULTS_DIR), f"{kind}_{TOOL['name']}.json")
    matrix.write(doc, out_path)
    return out_path
