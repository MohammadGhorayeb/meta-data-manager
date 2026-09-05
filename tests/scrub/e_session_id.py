"""E-SESSION-ID (W13/M12) — do editing-session identifiers survive the tools?

This project inherited a claim from the literature: *"OOXML RSIDs survive every
surveyed scrubber including MAT2"* (Müller). It was never measured here, and a
scouting probe (§2.6) contradicted it. This is the measurement.

The question is deliberately **wider than RSIDs**, because the probe suggested the
named leak had been fixed upstream while a family nobody names had not. The family:

- `w:rsid*` attributes and the `w:rsids` pool — the literature's target;
- a standalone `<w:rsid>` inside every `w:style`, which neither of those reaches;
- `w14:paraId` / `w14:textId` — **per-paragraph** ids that travel with a paragraph
  pasted into another document, so they link *files to each other*;
- `w14:docId` / `w15:docId` — a persistent per-document GUID, in two namespaces.

Controls come first: a tool cannot be shown to have missed something the input never
contained, so every run asserts the input carries the family before asking what came
out. And the corpus is built two ways on purpose — a synthetic package that CI can
always build, and real Word documents where the machine has them — because the
synthetic one proves what a tool *does* and only the real one proves that Word writes
these things in the first place.

Run:  ./.venv/bin/python -m tests.scrub.e_session_id
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile

from src.scrub.formats.ooxml import opc
from tests.scrub import docx_corpus as C

HAVE_EXIFTOOL = shutil.which("exiftool") is not None
HAVE_MAT2 = shutil.which("mat2") is not None

# family name -> how to find it in a part's bytes
FAMILIES: dict[str, re.Pattern] = {
    "rsid_attrs": re.compile(rb'\sw:rsid[A-Za-z]*="'),
    "rsids_pool": re.compile(rb"<w:rsids[\s/>]"),
    "rsid_in_style": re.compile(rb"<w:rsid[\s/>]"),
    "paraId": re.compile(rb'\sw1[0-9]:paraId="'),
    "textId": re.compile(rb'\sw1[0-9]:textId="'),
    "docId": re.compile(rb"<w1[0-9]:docId[\s/>]"),
}


def present(path: str) -> dict[str, int]:
    """Which families the package contains, and how many times."""
    counts = dict.fromkeys(FAMILIES, 0)
    try:
        pkg = opc.parse(open(path, "rb").read())
    except Exception:
        return counts
    for entry in pkg.archive.entries:
        if entry.is_dir or not entry.name.endswith(".xml"):
            continue
        try:
            body = entry.content()
        except Exception:
            continue
        for name, rx in FAMILIES.items():
            counts[name] += len(rx.findall(body))
    return counts


# --------------------------------------------------------------------------- #
# The tools under test
# --------------------------------------------------------------------------- #
def _ours(src: str, out: str) -> tuple[bool, str]:
    from src.scrub import cli
    try:
        cli.scrub_file(src, out, "F1")
    except Exception as exc:
        return False, f"refused: {type(exc).__name__}"
    return os.path.exists(out), ""


def _exiftool(src: str, out: str) -> tuple[bool, str]:
    """ExifTool is the project's measuring stick and **cannot write this format at
    all** — it answers `Can't write DOCX files` and produces nothing.

    That is reported as a capability gap rather than as a failure, and the two are
    not the same thing: a tool that declines has left the file untouched, which is
    honest, while a tool that writes an output still carrying the ids has told the
    user their document is clean when it is not.
    """
    if not HAVE_EXIFTOOL:
        return False, "not installed"
    r = subprocess.run(["exiftool", "-all=", "-o", out, src], capture_output=True)
    if not os.path.exists(out):
        msg = (r.stderr or r.stdout).decode("utf-8", "replace").strip()
        return False, msg.splitlines()[0] if msg else "no output"
    return True, ""


def _mat2(src: str, out: str) -> tuple[bool, str]:
    if not HAVE_MAT2:
        return False, "not installed"
    shutil.copy(src, out)
    subprocess.run(["mat2", "--inplace", out], capture_output=True)
    return os.path.exists(out), ""


TOOLS = (("ours_F1", _ours), ("mat2", _mat2), ("exiftool", _exiftool))


def word_like(path: str) -> str:
    """A synthetic package carrying every member of the family, the way Word writes
    them — settings pool, per-paragraph ids, per-style ids, both docId namespaces.

    Synthetic because CI has no Word. It measures what a *tool* does; it does not and
    cannot show that Word writes these, which is what the real corpus is for.
    """
    from src.scrub.formats.ooxml import zipwrite
    doc = C._torture_document()
    for tag in ("w:ins", "w:del", "w:commentRangeStart", "w:commentRangeEnd",
                "w:commentReference"):
        doc = re.sub(rf"<{tag}[^>]*>.*?</{tag}>|<{tag}[^>]*/>", "", doc, flags=re.S)
    parts = {
        "[Content_Types].xml": C._torture_content_types().encode(),
        "_rels/.rels": C._ROOT_RELS.encode(),
        "word/document.xml": doc.encode(),
        "word/settings.xml": C._TORTURE_SETTINGS.encode(),
        "word/styles.xml": C._TORTURE_STYLES.encode(),
    }
    with open(path, "wb") as f:
        f.write(zipwrite.write(parts))
    return path


def run(sources: dict[str, str], tmpdir: str) -> dict:
    """source -> tool -> {family: count_after}, plus the control counts."""
    out: dict[str, dict] = {}
    for sname, src in sources.items():
        before = present(src)
        row = {"_before": before, "_control_valid": any(before.values())}
        for tname, fn in TOOLS:
            dst = os.path.join(tmpdir, f"{sname}__{tname}.docx")
            ok, why = fn(src, dst)
            row[tname] = present(dst) if ok else None
            row[f"{tname}__why"] = why
        out[sname] = row
    return out


def build_sources(tmpdir: str) -> dict[str, str]:
    sources = {"synthetic": word_like(os.path.join(tmpdir, "wordlike.docx"))}
    for i, p in enumerate(C.word_samples()):
        sources[f"msword{i}"] = p
    return sources


def main() -> None:
    tmpdir = tempfile.mkdtemp(prefix="e_session_")
    sources = build_sources(tmpdir)
    results = run(sources, tmpdir)

    print("# E-SESSION-ID — do editing-session identifiers survive?\n")
    if not C.HAVE_WORD:
        print("NOTE: no Word samples on this machine, so only the synthetic corpus "
              "ran. That measures what each TOOL does; it cannot show that Word "
              "writes these ids in the first place. See "
              "tests/corpus/docx/README.md.\n")

    fams = list(FAMILIES)
    for sname, row in results.items():
        print(f"## {sname}")
        if not row["_control_valid"]:
            print("  CONTROL INVALID: the input carries none of the family, so "
                  "nothing can be concluded about any tool.\n")
            continue
        head = f"| {'tool':10s} | " + " | ".join(f"{f:12s}" for f in fams) + " |"
        print(head)
        print("|" + "---|" * (len(fams) + 1))
        print(f"| {'(input)':10s} | "
              + " | ".join(f"{row['_before'][f]:12d}" for f in fams) + " |")
        for tname, _ in TOOLS:
            got = row[tname]
            if got is None:
                why = row.get(f"{tname}__why") or "no output"
                cells = " | ".join(f"{'—':>12s}" for _ in fams)
                print(f"| {tname:10s} | {cells} |   ({why})")
            else:
                cells = " | ".join(f"{got[f]:12d}" for f in fams)
                print(f"| {tname:10s} | {cells} |")
        print()


if __name__ == "__main__":
    main()
