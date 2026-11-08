"""E-INSTALL (November plan N4): does a cleaned file show which install cleaned it?

The A2 passes collapse many producers into one output, but that output is made by the
user's own zlib, Pillow, lame, ffmpeg, jpegtran and LibreOffice. If two installs clean
one file into different bytes, the file tells an investigator which setup cleaned it:
a fingerprint of the tool's user that no other experiment can see, because each runs
on one machine.

Three steps, so the inputs are the same bytes everywhere:

    python scripts/e_install.py build DIR          # needs the test corpora
    python scripts/e_install.py run DIR OUT.json   # needs only the tool
    python scripts/e_install.py compare A.json B.json

`run` imports nothing from the tests, so it also proves the runtime requirements are
enough on a clean machine. Outcomes are hashes and refusal classes; no file content.
"""
from __future__ import annotations

import collections
import hashlib
import json
import os
import pathlib
import platform
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MANIFEST = "manifest.json"


def build(folder: str) -> None:
    from tests.scrub.test_fuzz import _corpus
    os.makedirs(folder, exist_ok=True)
    manifest = {}
    with tempfile.TemporaryDirectory() as td:
        for shape, (data, tiers) in _corpus(pathlib.Path(td)).items():
            levels = list(tiers)
            if shape == "docx":
                levels.append("F3")            # LibreOffice: the most version-bound
            name = f"{shape}.bin"
            with open(os.path.join(folder, name), "wb") as f:
                f.write(data)
            manifest[name] = levels
    with open(os.path.join(folder, MANIFEST), "w") as f:
        json.dump(manifest, f, indent=1, sort_keys=True)
    print(f"{len(manifest)} files in {folder}")


def run(folder: str, out_path: str) -> None:
    from src.scrub import cli, doctor
    from src.scrub.errors import ScrubError
    with open(os.path.join(folder, MANIFEST)) as f:
        manifest = json.load(f)
    results = {}
    with tempfile.TemporaryDirectory() as td:
        dst = os.path.join(td, "out")
        for name, levels in sorted(manifest.items()):
            for level in levels:
                if os.path.exists(dst):
                    os.unlink(dst)
                try:
                    cli.scrub_file(os.path.join(folder, name), dst, level, check_memory=False)
                    with open(dst, "rb") as f:
                        results[f"{name}/{level}"] = hashlib.sha256(f.read()).hexdigest()
                except ScrubError as exc:
                    results[f"{name}/{level}"] = f"refused:{type(exc).__name__}"
    programs, libraries = doctor.survey()
    import zlib
    with open(out_path, "w") as f:
        json.dump({"platform": f"{platform.system()} {platform.machine()}",
                   "python": platform.python_version(), "zlib": zlib.ZLIB_RUNTIME_VERSION,
                   "programs": programs, "libraries": libraries, "results": results},
                  f, indent=1, sort_keys=True)
    print(f"{len(results)} outcomes -> {out_path}")


def compare(a_path: str, b_path: str) -> int:
    a, b = json.load(open(a_path)), json.load(open(b_path))
    for side in (a, b):
        print(side["platform"], "python", side["python"], "zlib", side["zlib"],
              {k: v for k, v in {**side["programs"], **side["libraries"]}.items()})
    keys = sorted(set(a["results"]) | set(b["results"]))
    same = [k for k in keys if a["results"].get(k) == b["results"].get(k)]
    differ = [k for k in keys if a["results"].get(k) != b["results"].get(k)]
    print(f"\n{len(same)} of {len(keys)} outcomes identical; {len(differ)} differ")
    by_level = collections.defaultdict(list)
    for k in differ:
        name, level = k.rsplit("/", 1)
        by_level[level].append(name.removesuffix(".bin"))
    for level, names in sorted(by_level.items()):
        print(f"  {level}: {', '.join(sorted(names))}")
    for k in differ:
        x, y = a["results"].get(k), b["results"].get(k)
        if str(x).startswith("refused") or str(y).startswith("refused"):
            print(f"  outcome differs: {k}: {str(x)[:24]} vs {str(y)[:24]}")
    return 1 if differ else 0


if __name__ == "__main__":
    command, *rest = sys.argv[1:]
    if command == "build":
        build(*rest)
    elif command == "run":
        run(*rest)
    elif command == "compare":
        raise SystemExit(compare(*rest))
    else:
        raise SystemExit(__doc__)
