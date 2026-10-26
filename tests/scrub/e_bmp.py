"""E-BMP — can a cleaned BMP still be traced to the program that wrote it?

One opaque 24-bit picture (identical decoded colours, asserted) through every BMP
writer here: Pillow, ffmpeg, ImageMagick when installed (a V5 header) and, on a Mac,
`sips` (rows stored top-down). Three runs each. Opaque and 24-bit on purpose: a
palette or an alpha channel is a second picture-level choice, and A2 compares
writers of the SAME content. F1 keeps the header version and the row order --
changing either rewrites the pixel array or the drawing fields -- so whatever the
writer chose there passes through; the cell says which keys.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

from tests.harness.oracle import fields, variance
from tests.harness.plugins.bmp import BmpPlugin
from tests.scrub import bmp_corpus as bc


def _source(path: str) -> str:
    from PIL import Image
    img = Image.new("RGB", (bc.W, bc.H))
    img.putdata(bc.expected_rgba(24))
    img.save(path)
    return path


def imagemagick() -> str | None:
    """`magick` (ImageMagick 7) or `convert` (6, what Ubuntu packages)."""
    if shutil.which("magick"):
        return "magick"
    if sys.platform != "win32" and shutil.which("convert"):
        return "convert"
    return None


def producers() -> dict:
    def pillow(png, dst):
        from PIL import Image
        Image.open(png).convert("RGB").save(dst, "BMP")
    out = {"pillow": pillow}
    if shutil.which("ffmpeg"):
        out["ffmpeg"] = lambda png, dst: subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", png, "-frames:v", "1", "-c:v", "bmp",
             "-pix_fmt", "bgr24", dst], check=True, capture_output=True)
    im = imagemagick()
    if im:
        out["imagemagick"] = lambda png, dst: subprocess.run(
            [im, png, dst], check=True, capture_output=True)
    if sys.platform == "darwin" and shutil.which("sips"):
        out["macos_sips"] = lambda png, dst: subprocess.run(
            ["sips", "-s", "format", "bmp", png, "--out", dst], check=True,
            capture_output=True)
    return out


def build_sources(tmpdir: str, repeats: int = 3) -> dict:
    png = _source(os.path.join(tmpdir, "source.png"))
    sources = {}
    for name, make in producers().items():
        sources[name] = []
        for r in range(repeats):
            dst = os.path.join(tmpdir, f"{name}_r{r}.bmp")
            make(png, dst)
            sources[name].append(dst)
    return sources


def run_condition(fidelity: str, sources: dict, tmpdir: str) -> dict:
    from src.scrub import cli
    plugin = BmpPlugin()
    prods = list(sources)
    repeats = list(range(min(len(v) for v in sources.values())))
    cells = {}
    for prod, paths in sources.items():
        for r, src in enumerate(paths):
            target = src
            if fidelity != "raw":
                target = os.path.join(tmpdir, f"{prod}_{r}_{fidelity}.bmp")
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
