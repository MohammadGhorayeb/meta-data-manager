"""E-WEBP — can a cleaned WebP still be traced to the encoder that wrote it?

One picture through every lossless WebP encoder setting here -- pixels identical,
asserted -- three runs each. Lossless only: two lossy encodes of one picture are
two different pictures, and A2 compares producers of the SAME content. F1 copies the
coded image bit for bit, so whatever the encoder chose (its bitstream, and so the
file size) passes through; the cell says which keys.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

from tests.harness.oracle import fields, variance
from tests.harness.plugins.webp import WebpPlugin
from tests.scrub import webp_corpus as wc


def producers() -> dict:
    def pillow(method):
        def make(src_png, dst):
            from PIL import Image
            Image.open(src_png).save(dst, "WEBP", lossless=True, exact=True,
                                     method=method)
        return make
    out = {"pillow_m0": pillow(0), "pillow_m6": pillow(6)}
    if shutil.which("cwebp"):
        out["cwebp_lossless"] = lambda s, d: subprocess.run(
            ["cwebp", "-quiet", "-lossless", "-exact", s, "-o", d], check=True,
            capture_output=True)
    return out


def build_sources(tmpdir: str, repeats: int = 3) -> dict:
    src = os.path.join(tmpdir, "source.png")
    wc.picture().save(src)
    sources = {}
    for name, make in producers().items():
        paths = []
        for r in range(repeats):
            dst = os.path.join(tmpdir, f"{name}_r{r}.webp")
            make(src, dst)
            paths.append(dst)
        sources[name] = paths
    return sources


def run_condition(fidelity: str, sources: dict, tmpdir: str) -> dict:
    from src.scrub import cli
    plugin = WebpPlugin()
    prods = list(sources)
    repeats = list(range(min(len(v) for v in sources.values())))
    cells = {}
    for prod, paths in sources.items():
        for r, src in enumerate(paths):
            target = src
            if fidelity != "raw":
                target = os.path.join(tmpdir, f"{prod}_{r}_{fidelity}.webp")
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
