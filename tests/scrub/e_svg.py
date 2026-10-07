"""E-SVG — can a cleaned SVG still be traced to the program that wrote it?

One drawing, written by: cairo (`rsvg-convert -f svg`, a real SVG serializer,
re-writing the drawing), and three corpus documents shaped like Inkscape's, Sketch's
and LibreOffice's exports of the same drawing (`svg_corpus` -- models of their
idioms, said so in the cell). Three runs each. F1 only deletes, so how a producer
writes XML -- its namespaces, root attributes, element and attribute vocabulary,
whitespace -- survives; the cell names which keys. The specification for an F2 is a
canonical re-serialisation, which cannot change the vocabulary a producer chose.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

from tests.harness.oracle import fields, variance
from tests.harness.plugins.svg import SvgPlugin
from tests.scrub import svg_corpus as sc

MODELLED = ("inkscape", "sketch", "libreoffice")


def _model(shape: str):
    def make(dst: str) -> None:
        with open(dst, "wb") as f:
            f.write(sc.build(0, shape=shape))
    return make


def producers() -> dict:
    out = {f"{s}_model": _model(s) for s in MODELLED}
    if shutil.which("rsvg-convert"):
        def cairo(dst):
            src = dst + ".src.svg"
            open(src, "wb").write(sc.build(0, shape="inkscape"))
            subprocess.run(["rsvg-convert", "-f", "svg", src, "-o", dst], check=True,
                           capture_output=True)
            os.unlink(src)
        out["cairo"] = cairo
    return out


def build_sources(tmpdir: str, repeats: int = 3) -> dict:
    sources = {}
    for name, make in producers().items():
        sources[name] = []
        for r in range(repeats):
            dst = os.path.join(tmpdir, f"{name}_r{r}.svg")
            make(dst)
            sources[name].append(dst)
    return sources


def run_condition(fidelity: str, sources: dict, tmpdir: str) -> dict:
    from src.scrub import cli
    plugin = SvgPlugin()
    prods = list(sources)
    repeats = list(range(min(len(v) for v in sources.values())))
    cells = {}
    for prod, paths in sources.items():
        for r, src in enumerate(paths):
            target = src
            if fidelity != "raw":
                target = os.path.join(tmpdir, f"{prod}_{r}_{fidelity}.svg")
                cli.scrub_file(src, target, fidelity)
            cells[(prod, r)] = fields.extract(target, plugin, "F1")
    verdicts = variance.decompose(cells, prods, repeats)
    return {"producers": prods,
            "struct_fingerprints": sorted(f for f, v in verdicts.items()
                                          if v.leak and f.startswith("struct:"))}


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        sources = build_sources(td)
        for cond in ("raw", "F1"):
            r = run_condition(cond, sources, td)
            print(cond, ", ".join(r["producers"]), "->",
                  ", ".join(r["struct_fingerprints"]) or "nothing")


if __name__ == "__main__":
    main()
