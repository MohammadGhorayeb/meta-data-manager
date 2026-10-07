"""E-GIF — can a cleaned GIF still be traced to the program that wrote it?

One animation (identical decoded colours, asserted) through every GIF writer here:
Pillow with and without palette optimisation, `gifsicle` and, on a Mac, `sips` for
the first frame's still. Three runs each. F1 keeps every image block as it was
encoded, so the writer's LZW encoding, palette and block layout pass through.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

from tests.harness.oracle import fields, variance
from tests.harness.plugins.gif import GifPlugin
from tests.scrub import gif_corpus as gc


def _still(path: str) -> str:
    gc.frame(0, 0).convert("RGB").save(path)
    return path


def producers() -> dict:
    def pillow(optimize: bool):
        def make(png, dst):
            from PIL import Image
            Image.open(png).convert("P", palette=Image.Palette.ADAPTIVE).save(
                dst, "GIF", optimize=optimize)
        return make
    out = {"pillow": pillow(False), "pillow_optimize": pillow(True)}
    if shutil.which("gifsicle"):
        def gifsicle(png, dst):
            pillow(False)(png, dst + ".src.gif")
            subprocess.run(["gifsicle", "-O2", dst + ".src.gif", "-o", dst],
                           check=True, capture_output=True)
            os.unlink(dst + ".src.gif")
        out["gifsicle"] = gifsicle
    if sys.platform == "darwin" and shutil.which("sips"):
        def sips(png, dst):
            pillow(False)(png, dst + ".src.gif")
            subprocess.run(["sips", "-s", "format", "gif", dst + ".src.gif", "--out",
                            dst], check=True, capture_output=True)
            os.unlink(dst + ".src.gif")
        out["macos_sips"] = sips
    return out


def build_sources(tmpdir: str, repeats: int = 3) -> dict:
    png = _still(os.path.join(tmpdir, "source.png"))
    sources = {}
    for name, make in producers().items():
        sources[name] = []
        for r in range(repeats):
            dst = os.path.join(tmpdir, f"{name}_r{r}.gif")
            make(png, dst)
            sources[name].append(dst)
    return sources


def run_condition(fidelity: str, sources: dict, tmpdir: str) -> dict:
    from src.scrub import cli
    plugin = GifPlugin()
    prods = list(sources)
    repeats = list(range(min(len(v) for v in sources.values())))
    cells = {}
    for prod, paths in sources.items():
        for r, src in enumerate(paths):
            target = src
            if fidelity != "raw":
                target = os.path.join(tmpdir, f"{prod}_{r}_{fidelity}.gif")
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
