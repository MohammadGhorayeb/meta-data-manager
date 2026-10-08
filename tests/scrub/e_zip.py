"""E-ZIP — can a cleaned archive still be traced to the program that made it?

One folder (a photo with EXIF, a PNG, text, an executable script, an empty folder;
fixed modification times) through every archiver here: Info-ZIP `zip -r`, Python's
`shutil.make_archive`, libarchive's `bsdtar --format zip` and, on a Mac, `ditto`
(what Finder's *Compress* runs). Three runs each. The A2 channel is the container
only (`ZipPlugin.structural_features` and ExifTool's ZIP fields); the members are
files of their own formats, judged in their own matrices.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

from tests.harness.oracle import fields, variance
from tests.harness.plugins.zip import ZipPlugin
from tests.scrub import zip_corpus as zc

STAMP = 1_000_000_000


def payload(root: str) -> str:
    """The folder every archiver is given."""
    top = os.path.join(root, "payload")
    for d in ("docs", "photos", "tools", "empty"):
        os.makedirs(os.path.join(top, d), exist_ok=True)
    files = {"docs/notes.txt": zc.text(), "photos/photo.jpg": zc.photo(1),
             "photos/icon.png": zc.png(1), "tools/run.sh": zc.script()}
    for name, body in files.items():
        path = os.path.join(top, name)
        with open(path, "wb") as f:
            f.write(body)
        os.chmod(path, 0o755 if name.endswith(".sh") else 0o644)
    for dirpath, dirnames, filenames in os.walk(top, topdown=False):
        for n in filenames + dirnames:
            os.utime(os.path.join(dirpath, n), (STAMP, STAMP))
    os.utime(top, (STAMP, STAMP))
    return top


def producers() -> dict:
    def run(cmd, cwd):
        subprocess.run(cmd, cwd=cwd, check=True, capture_output=True)

    out = {}
    if shutil.which("zip"):
        out["infozip"] = lambda top, dst: run(["zip", "-qr", dst, "payload"],
                                              os.path.dirname(top))
    out["python_shutil"] = lambda top, dst: shutil.move(
        shutil.make_archive(dst[:-4] + ".tmp", "zip", os.path.dirname(top),
                            "payload"), dst)
    if shutil.which("bsdtar"):
        out["libarchive_bsdtar"] = lambda top, dst: run(
            ["bsdtar", "--format", "zip", "-cf", dst, "payload"], os.path.dirname(top))
    if sys.platform == "darwin" and shutil.which("ditto"):
        out["macos_ditto"] = lambda top, dst: run(
            ["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", top, dst],
            os.path.dirname(top))
    return out


def build_sources(tmpdir: str, repeats: int = 3) -> dict:
    top = payload(tmpdir)
    sources = {}
    for name, make in producers().items():
        sources[name] = []
        for r in range(repeats):
            dst = os.path.join(tmpdir, f"{name}_r{r}.zip")
            make(top, dst)
            sources[name].append(dst)
    return sources


def run_condition(fidelity: str, sources: dict, tmpdir: str) -> dict:
    from src.scrub import cli
    plugin = ZipPlugin()
    prods = list(sources)
    repeats = list(range(min(len(v) for v in sources.values())))
    cells = {}
    for prod, paths in sources.items():
        for r, src in enumerate(paths):
            target = src
            if fidelity != "raw":
                target = os.path.join(tmpdir, f"{prod}_{r}_{fidelity}.zip")
                cli.scrub_file(src, target, fidelity)
            cells[(prod, r)] = fields.extract(target, plugin, "F1")
    verdicts = variance.decompose(cells, prods, repeats)
    return {"producers": prods,
            "struct_fingerprints": sorted(f for f, v in verdicts.items()
                                          if v.leak and (f.startswith("struct:")
                                                         or f.startswith("field:")))}


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        sources = build_sources(td)
        for cond in ("raw", "F1"):
            r = run_condition(cond, sources, td)
            print(cond, ", ".join(r["producers"]), "->",
                  ", ".join(r["struct_fingerprints"]) or "nothing")


if __name__ == "__main__":
    main()
