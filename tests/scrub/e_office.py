"""E-XLSX and E-PPTX — can a cleaned workbook or deck be traced to the program that
wrote it, and through which half?

The E-DOCX design (`e_docx.py`) for Excel and PowerPoint. The same content is
written by each real producer this machine and CI can both run, three times, and
every key that is constant within a producer and differs across them is a leak,
reported by channel: the **packager** (the ZIP), the **model** (the SpreadsheetML or
PresentationML as that program writes it), the **size**, and anything ExifTool
reads that still separates them (`fields`).

Peer sets:
  - workbooks: LibreOffice Calc (from a CSV), openpyxl and XlsxWriter -- pandas' two
    Excel engines, so the source of most machine-written workbooks;
  - decks: python-pptx, and LibreOffice Impress re-saving that deck.

**Microsoft Office is not in either set, and the cells say so.** Excel 16.114 is
installed on the Mac this was built on and opens files when scripted, but refused
every save (`-50`, four ways of asking, measured 2026-11-07; PowerPoint was not
tried); and CI could not run either anyway, while a published cell must re-measure
there. Keynote exports PPTX but
exists only on macOS for the same reason. An absent producer makes a cell say what
it did not test, never that it is clean (the limit #12 precedent).

Run:  ./.venv/bin/python -m tests.scrub.e_office
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import tempfile

from src.scrub.formats.docx import f3
from tests.harness.oracle import fields, variance

ROWS = [("Item", "Q1", "Q2"), ("Widgets", 42, 84), ("Gadgets", 7, 9)]
SHEET = "Summary"
TITLE = "Quarterly review"
BODY = "Widgets 42, gadgets 7"

ABSENT = {"xlsx": ("Microsoft Excel (scripted saves refused here; not on CI)",),
          "pptx": ("Microsoft PowerPoint (not scripted here; not on CI)",
                   "Keynote (macOS only)")}


def _soffice(src: str, ext: str, work: str) -> str:
    """Convert `src` with LibreOffice into `work`; return the output path."""
    out = os.path.join(work, "lo_out")
    subprocess.run(["soffice", "--headless", f3.profile_arg(work), "--convert-to", ext,
                    "--outdir", out, src], check=True, capture_output=True, timeout=240)
    path = os.path.join(out, os.path.splitext(os.path.basename(src))[0] + "." + ext)
    if not os.path.exists(path):
        raise RuntimeError(f"LibreOffice wrote no {ext} for {src}")
    return path


def has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


# --------------------------------------------------------------------------- #
# Writers. Each takes (rows or deck text, metadata dict, destination) so the A1
# shapes in gen_matrix_xlsx/pptx can reuse them with planted properties.
# --------------------------------------------------------------------------- #
def openpyxl_write(rows, dst: str, meta: dict | None = None) -> None:
    import openpyxl
    from openpyxl.packaging.custom import StringProperty
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = SHEET
    for r in rows:
        ws.append(list(r))
    meta = meta or {}
    p = wb.properties
    for attr in ("creator", "lastModifiedBy", "title", "subject", "description",
                 "keywords", "category"):
        if attr in meta:
            setattr(p, attr, meta[attr])
    for name, value in meta.get("custom", {}).items():
        wb.custom_doc_props.append(StringProperty(name=name, value=value))
    wb.save(dst)


def xlsxwriter_write(rows, dst: str, meta: dict | None = None) -> None:
    import xlsxwriter
    wb = xlsxwriter.Workbook(dst)
    ws = wb.add_worksheet(SHEET)
    for i, r in enumerate(rows):
        ws.write_row(i, 0, r)
    meta = meta or {}
    props = {k: meta[src] for k, src in (("author", "creator"), ("title", "title"),
                                         ("subject", "subject"), ("manager", "manager"),
                                         ("company", "company"),
                                         ("comments", "description"),
                                         ("keywords", "keywords")) if src in meta}
    if props:
        wb.set_properties(props)
    for name, value in meta.get("custom", {}).items():
        wb.set_custom_property(name, value)
    wb.close()


def calc_write(rows, dst: str, work: str) -> None:
    csv = os.path.join(work, f"{SHEET}.csv")
    with open(csv, "w", encoding="utf-8") as f:
        f.write("\n".join(",".join(map(str, r)) for r in rows) + "\n")
    shutil.move(_soffice(csv, "xlsx", work), dst)


def pptx_write(dst: str, meta: dict | None = None, title: str = TITLE,
               body: str = BODY, more: tuple = (), picture: bytes | None = None) -> None:
    """One title-and-text slide (and `more` of them), optionally with a picture."""
    from pptx import Presentation
    prs = Presentation()
    for t, b in ((title, body),) + tuple(more):
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = t
        slide.placeholders[1].text = b
    if picture is not None:
        import io
        prs.slides[0].shapes.add_picture(io.BytesIO(picture), 0, 0)
    cp = prs.core_properties
    for attr, value in (meta or {}).items():
        setattr(cp, attr, value)
    prs.save(dst)


def impress_resave(src: str, dst: str, work: str) -> None:
    staged = os.path.join(work, "deck.pptx")
    shutil.copy(src, staged)
    shutil.move(_soffice(staged, "pptx", work), dst)


def xlsx_producers() -> dict:
    out = {}
    if shutil.which("soffice"):
        out["libreoffice"] = lambda dst, work: calc_write(ROWS, dst, work)
    if has("openpyxl"):
        out["openpyxl"] = lambda dst, work: openpyxl_write(ROWS, dst)
    if has("xlsxwriter"):
        out["xlsxwriter"] = lambda dst, work: xlsxwriter_write(ROWS, dst)
    return out


def pptx_producers() -> dict:
    out = {}
    if has("pptx"):
        out["python-pptx"] = lambda dst, work: pptx_write(dst)
        if shutil.which("soffice"):
            def libreoffice(dst, work):
                src = os.path.join(work, "source.pptx")
                pptx_write(src)
                impress_resave(src, dst, work)
            out["libreoffice"] = libreoffice
    return out


PRODUCERS = {"xlsx": xlsx_producers, "pptx": pptx_producers}


def build_sources(kind: str, tmpdir: str, repeats: int = 3) -> dict:
    sources = {}
    for name, make in PRODUCERS[kind]().items():
        sources[name] = []
        for r in range(repeats):
            work = os.path.join(tmpdir, f"{kind}_{name}_{r}")
            os.makedirs(work, exist_ok=True)
            dst = os.path.join(work, f"{name}_r{r}.{kind}")
            make(dst, work)
            sources[name].append(dst)
    return sources


def _plugin(kind: str):
    if kind == "xlsx":
        from tests.harness.plugins import xlsx as mod
        return mod.XlsxPlugin(), mod.CHANNELS
    from tests.harness.plugins import pptx as mod
    return mod.PptxPlugin(), mod.CHANNELS


def run_condition(kind: str, fidelity: str, sources: dict, tmpdir: str) -> dict:
    """Per channel, the keys that still separate producers. `fidelity="raw"` is the
    untouched control."""
    from src.scrub import cli
    plugin, channels = _plugin(kind)
    producers = list(sources)
    repeats = list(range(min(len(v) for v in sources.values())))
    cells = {}
    for prod, paths in sources.items():
        for r, src in enumerate(paths):
            target = src
            if fidelity != "raw":
                target = os.path.join(tmpdir, f"{kind}_{prod}_{r}_{fidelity}.{kind}")
                cli.scrub_file(src, target, fidelity)
            cells[(prod, r)] = fields.extract(target, plugin, "F1")
    verdicts = variance.decompose(cells, producers, repeats)
    leaking = sorted(f for f, v in verdicts.items() if v.leak)
    by_channel = {name: sorted(k for k in leaking if k in keys)
                  for name, keys in channels.items()}
    # ExifTool's reading of the package is not ignored. Its ZIP fields describe
    # the FIRST member only, `[Content_Types].xml`, whose bytes are the list of
    # parts kept -- so a CRC or size there is the model's part set again, filed
    # with it. Anything else it still shows is a channel of its own.
    first_member = {"field:ZIP:ZipCRC", "field:ZIP:ZipCompressedSize",
                    "field:ZIP:ZipUncompressedSize"}
    by_channel["model"] = sorted(by_channel["model"]
                                 + [k for k in leaking if k in first_member])
    by_channel["fields"] = sorted(k for k in leaking if k.startswith("field:")
                                  and k not in first_member)
    claimed = {k for keys in channels.values() for k in keys}
    by_channel["unclassified"] = sorted(k for k in leaking if k.startswith("struct:")
                                        and k not in claimed)
    return {"fidelity": fidelity, "producers": producers, "leaking": leaking,
            "by_channel": by_channel}


def evaluate_cell(kind: str, fidelity: str, sources: dict, tmpdir: str):
    from tests.harness.contract import Cell, Leak, Locus, V
    label = {"xlsx": "workbook", "pptx": "presentation"}[kind]
    raw = run_condition(kind, "raw", sources, tmpdir)
    result = run_condition(kind, fidelity, sources, tmpdir)
    peers = ", ".join(result["producers"])
    gap = " Not in the peer set: " + "; ".join(ABSENT[kind]) + "."
    separable = [c for c, keys in raw["by_channel"].items() if keys]
    if len(sources) < 2 or not separable:
        return Cell("A2", fidelity, V.NOT_TESTED,
                    reason=f"controls invalid: the peer set ({peers}) does not separate "
                           f"producers on any channel even unscrubbed.{gap}")
    open_channels = {c: keys for c, keys in result["by_channel"].items() if keys}
    closed = [c for c in separable if c not in open_channels]
    if not open_channels:
        return Cell("A2", fidelity, V.PASS,
                    reason=f"no key separates the producers; peer set = {peers}; "
                           f"separable unscrubbed on: {', '.join(separable)}.{gap}")
    leaks = [Leak("source_fingerprint", Locus("structural", feature_id=k),
                  f"{label} producer ({c})",
                  f"{k} constant within a producer, differs across")
             for c, keys in open_channels.items() for k in keys]
    named = "; ".join(f"{c}: " + ", ".join(k.removeprefix("struct:") for k in keys)
                      for c, keys in open_channels.items())
    return Cell("A2", fidelity, V.FAIL, leaks=leaks,
                reason=(f"closed at {fidelity}: {', '.join(closed) or 'none'}. "
                        f"Still separating -- {named}. Peer set = {peers} (the same "
                        f"{'cells' if kind == 'xlsx' else 'slide text'} from each).{gap}"))


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        for kind in ("xlsx", "pptx"):
            sources = build_sources(kind, td)
            for cond in ("raw", "F1"):
                r = run_condition(kind, cond, sources, td)
                print(kind, cond, ", ".join(r["producers"]))
                for ch, keys in r["by_channel"].items():
                    print(f"  {ch}: {', '.join(keys) or '-'}")


if __name__ == "__main__":
    main()
