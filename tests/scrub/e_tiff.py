"""E-TIFF — can a cleaned TIFF still be traced to the library that wrote it?

One picture, written by every TIFF producer this machine has, with identical pixels
(the test asserts it rather than assuming): Pillow uncompressed, Pillow LZW, libtiff
`tiffcp` and, on a Mac, `sips`. Three runs each (the repeats: TIFF writers are
deterministic, so a feature varying within one is noise). Scrubbed at F1, the
surviving structure is decomposed into within- and across-producer variation.

F1 moves nothing, so the writer's choices -- which tags it writes, in which IFD
layout, how it strips the image, whether it embeds a colour profile, its
compression -- pass through. A2@F1 failing is expected; the cell names the keys,
which specify an F2 (one canonical writer, pixels re-stored losslessly).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

from tests.harness.oracle import fields, variance
from tests.harness.plugins.tiff import TiffPlugin


def picture(path: str) -> str:
    from PIL import Image
    img = Image.new("RGB", (64, 40))
    img.putdata([((x * 4) & 0xFF, (y * 6) & 0xFF, ((x ^ y) * 3) & 0xFF)
                 for y in range(40) for x in range(64)])
    img.save(path)                                         # PNG: the common source
    return path


def _pillow(src: str, dst: str, **kw) -> None:
    from PIL import Image
    Image.open(src).save(dst, **kw)


def producers() -> dict:
    out = {"pillow": lambda s, d: _pillow(s, d),
           "pillow_lzw": lambda s, d: _pillow(s, d, compression="tiff_lzw")}
    if shutil.which("tiffcp"):
        def tiffcp(s, d):
            mid = d + ".src.tif"
            _pillow(s, mid)
            subprocess.run(["tiffcp", "-c", "none", mid, d], check=True,
                           capture_output=True)
            os.unlink(mid)
        out["libtiff_tiffcp"] = tiffcp
    if sys.platform == "darwin" and shutil.which("sips"):
        out["macos_sips"] = lambda s, d: subprocess.run(
            ["sips", "-s", "format", "tiff", s, "--out", d], check=True,
            capture_output=True)
    return out


def build_sources(tmpdir: str, repeats: int = 3) -> dict:
    src = picture(os.path.join(tmpdir, "source.png"))
    sources = {}
    for name, make in producers().items():
        paths = []
        for r in range(repeats):
            dst = os.path.join(tmpdir, f"{name}_r{r}.tif")
            make(src, dst)
            paths.append(dst)
        sources[name] = paths
    return sources


def run_condition(fidelity: str, sources: dict, tmpdir: str) -> dict:
    from src.scrub import cli
    plugin = TiffPlugin()
    prods = list(sources)
    repeats = list(range(min(len(v) for v in sources.values())))
    cells = {}
    for prod, paths in sources.items():
        for r, src in enumerate(paths):
            target = src
            if fidelity != "raw":
                target = os.path.join(tmpdir, f"{prod}_{r}_{fidelity}.tif")
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
