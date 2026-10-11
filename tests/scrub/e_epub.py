"""E-EPUB — can a cleaned book still be traced to the program that made it?

One Word document (`docx_corpus.synthetic`: the same words for everyone) made into
an EPUB by LibreOffice (libepubgen) and by pandoc writing EPUB 3 and EPUB 2. Three
runs each. An EPUB has three producer channels, reported separately (as DOCX's
two are): the **packager** (the ZIP), the **package metadata** (what the OPF and
NCX hold and how they are laid out), and the **content model** (file layout, how
each chapter's XHTML is built, the stylesheet). F1 rebuilds the first and cleans
the second; it only deletes from the third, which is the book as its producer
wrote it.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import zipfile

from tests.harness.oracle import fields, variance
from tests.harness.plugins.epub import CHANNELS, EpubPlugin
from tests.scrub import docx_corpus as dc

TEXT = ("Opening\nThis paragraph exists so that every producer has the same "
        "words to lay out.\nA second sentence gives the packager something to "
        "compress.\nPrepared for circulation.\n")


TITLE = "A Short Report"


def source_docx(path: str) -> str:
    """The Word document every producer is given: `docx_corpus`'s body, plus a
    title and a language in its properties, so each producer reads its metadata
    from the same place rather than from its own defaults or command line."""
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/'
            '2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/'
            f'1.1/"><dc:title>{TITLE}</dc:title><dc:language>en-US</dc:language>'
            '</cp:coreProperties>')
    types = dc._CONTENT_TYPES.replace(
        "</Types>", '<Override PartName="/docProps/core.xml" ContentType="application/'
        'vnd.openxmlformats-package.core-properties+xml"/></Types>')
    rels = dc._ROOT_RELS.replace(
        "</Relationships>", '<Relationship Id="rId2" Type="http://schemas.openxml'
        'formats.org/package/2006/relationships/metadata/core-properties" '
        'Target="docProps/core.xml"/></Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in (("[Content_Types].xml", types), ("_rels/.rels", rels),
                           ("docProps/core.xml", core),
                           ("word/document.xml", dc._document_xml(TEXT))):
            z.writestr(zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0)), body)
    return path


def producers() -> dict:
    out = {}
    if shutil.which("soffice"):
        def libreoffice(docx, dst):
            work = os.path.dirname(dst)
            subprocess.run(["soffice", "--headless", "--convert-to", "epub",
                            "--outdir", work, docx], check=True, capture_output=True,
                           timeout=180)
            shutil.move(os.path.join(work, os.path.splitext(
                os.path.basename(docx))[0] + ".epub"), dst)
        out["libreoffice"] = libreoffice
    if shutil.which("pandoc"):
        def pandoc(fmt):
            def make(docx, dst):
                subprocess.run(["pandoc", docx, "-t", fmt, "-o", dst],
                               check=True, capture_output=True, timeout=180)
            return make
        out["pandoc_epub3"] = pandoc("epub3")
        out["pandoc_epub2"] = pandoc("epub2")
    return out


def build_sources(tmpdir: str, repeats: int = 3) -> dict:
    sources = {}
    for name, make in producers().items():
        sources[name] = []
        for r in range(repeats):
            work = os.path.join(tmpdir, f"{name}_{r}")
            os.makedirs(work, exist_ok=True)
            docx = source_docx(os.path.join(work, "report.docx"))
            dst = os.path.join(work, f"{name}_r{r}.epub")
            make(docx, dst)
            sources[name].append(dst)
    return sources


def run_condition(fidelity: str, sources: dict, tmpdir: str) -> dict:
    """Per channel, the keys that still separate producers."""
    from src.scrub import cli
    plugin = EpubPlugin()
    prods = list(sources)
    repeats = list(range(min(len(v) for v in sources.values())))
    cells = {}
    for prod, paths in sources.items():
        for r, src in enumerate(paths):
            target = src
            if fidelity != "raw":
                target = os.path.join(tmpdir, f"{prod}_{r}_{fidelity}.epub")
                cli.scrub_file(src, target, fidelity)
            cells[(prod, r)] = fields.extract(target, plugin, "F1")
    verdicts = variance.decompose(cells, prods, repeats)
    leaked = sorted(f for f, v in verdicts.items() if v.leak)
    by_channel = {c: [k for k in leaked if k.removeprefix("struct:").startswith(c)]
                  for c in CHANNELS}
    # ExifTool reads the package document; its keys go to the channel they show:
    # the manifest, the spine and the identifier (a hash of the content, after
    # F1) are the content model; the rest is how the metadata was written.
    for k in leaked:
        if k.startswith("field:"):
            content = re.search(r"Manifest|Spine|:Identifier$", k) is not None
            by_channel["content_" if content else "meta_"].append(k)
    return {"producers": prods, "leaked": leaked, "by_channel": by_channel}


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        sources = build_sources(td)
        for cond in ("raw", "F1"):
            r = run_condition(cond, sources, td)
            print(cond, ", ".join(r["producers"]))
            for ch, keys in r["by_channel"].items():
                print(f"  {ch}: {', '.join(keys) or 'closed'}")


if __name__ == "__main__":
    main()
