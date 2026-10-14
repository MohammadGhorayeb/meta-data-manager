"""Census of real .docx files: who made them, what Word-only parts they hold, and
what F1 does with each (`docs/word_plan.md`, W6 measurement).

Reads a folder of .docx (downloaded public test documents, or your own), keeps the
ones Microsoft Word made (by `docProps/app.xml`), and reports aggregates only:

- the parts they hold (media by type, embedded fonts, charts, embedded objects,
  add-ins, printer settings, glossary, custom XML, comments);
- what Word wrote as each picture's name and description: generic ("Picture 1"),
  a FILE NAME (the inserted file's own name), or other text;
- linked-picture and other external targets that are file paths;
- embedded-font keys, and whether one font file appears under different keys;
- F1's outcome per document: cleaned, or refused and why.

No value from any document is printed -- counts and categories only.

Usage:  ./.venv/bin/python scripts/docx_census.py <folder> [<folder>...]
"""
from __future__ import annotations

import collections
import hashlib
import os
import re
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.scrub.errors import ScrubError  # noqa: E402
from src.scrub.formats.docx import f1  # noqa: E402

IMAGE_EXT = re.compile(rb"\.(jpe?g|png|gif|bmp|tiff?|emf|wmf|heic|webp|svg)\b", re.I)
GENERIC = re.compile(rb"^(Picture|Image|Grafik|Imagen|Immagine|Afbeelding|Obraz|"
                     rb"Billede|Bild|Kuva|Figure|Graphic|Text Box|Rectangle|Chart|"
                     rb"Diagram|Object|Group|Straight|Oval|Canvas)[\s\d]*$", re.I)


def producer(z: zipfile.ZipFile) -> str:
    try:
        app = z.read("docProps/app.xml")
    except KeyError:
        return "(no app.xml)"
    m = re.search(rb"<(?:\w+:)?Application>([^<]*)<", app)
    return m.group(1).decode("utf-8", "replace") if m else "(no Application)"


def is_word(name: str) -> bool:
    return "Microsoft" in name and "Word" in name


def classify_name(value: bytes) -> str:
    v = value.strip()
    if not v:
        return "empty"
    if IMAGE_EXT.search(v):
        return "FILE NAME"
    if GENERIC.match(v):
        return "generic"
    return "other text"


def main(folders: list[str]) -> None:
    producers = collections.Counter()
    parts = collections.Counter()
    names = collections.Counter()
    external = collections.Counter()
    fonts_by_digest: dict[str, set[bytes]] = collections.defaultdict(set)
    outcomes = collections.Counter()
    reasons = collections.Counter()
    word_docs = 0
    for folder in folders:
        for fn in sorted(os.listdir(folder)):
            if not fn.lower().endswith(".docx"):
                continue
            path = os.path.join(folder, fn)
            try:
                z = zipfile.ZipFile(path)
            except zipfile.BadZipFile:
                producers["(not a zip)"] += 1
                continue
            try:
                prod = producer(z)
                if z.testzip() is not None:
                    raise ValueError("bad CRC")
            except Exception:                             # noqa: BLE001
                producers["(damaged archive)"] += 1
                continue
            # The program, not its build: "LibreOffice/7.5.5.2$Linux..." -> LibreOffice
            producers[re.sub(r"\s*[\d.]+$", "", re.split(r"[/$]", prod)[0])] += 1
            if not is_word(prod):
                continue
            word_docs += 1
            members = z.namelist()
            kinds = set()
            for n in members:
                if n.startswith("word/media/"):
                    kinds.add("media " + os.path.splitext(n)[1].lower())
                for label, pat in (("embedded fonts", r"^word/fonts/"),
                                   ("charts", r"^word/charts/"),
                                   ("embedded objects", r"^word/embeddings/"),
                                   ("add-ins", r"^word/webextensions/"),
                                   ("printer settings", r"^word/printerSettings/"),
                                   ("glossary", r"^word/glossary/"),
                                   ("custom XML", r"^customXml/"),
                                   ("comments", r"^word/comments"),
                                   ("people", r"^word/people\.xml$"),
                                   ("macros", r"vbaProject\.bin$"),
                                   ("ActiveX", r"^word/activeX/"),
                                   ("thumbnail", r"^docProps/thumbnail")):
                    if re.search(pat, n):
                        kinds.add(label)
            parts.update(kinds)
            for n in members:
                if not n.endswith(".xml"):
                    continue
                body = z.read(n)
                for tag, attr in ((b"pic:cNvPr", b"name"), (b"wp:docPr", b"name"),
                                  (b"wp:docPr", b"descr"), (b"wp:docPr", b"title"),
                                  (b"v:imagedata", b"o:title")):
                    for m in re.finditer(rb"<" + re.escape(tag) + rb"\s[^>]*?\b"
                                         + re.escape(attr) + rb'="([^"]*)"', body):
                        names[f"{tag.decode()}@{attr.decode()}: "
                              f"{classify_name(m.group(1))}"] += 1
                if n.endswith(".rels"):
                    continue
                if n == "word/fontTable.xml":
                    parts["font keys"] += len(re.findall(rb'w:fontKey="[^"]+"', body))
            for n in members:
                if n.endswith(".rels"):
                    body = z.read(n)
                    for m in re.finditer(rb'Target="([^"]+)"[^>]*TargetMode="External"',
                                         body):
                        t = m.group(1)
                        kind = ("file path" if re.match(rb"(file:|[A-Za-z]:\\|\\\\|/)", t)
                                else "url" if re.match(rb"https?:", t) else "other")
                        external[kind] += 1
                if n.startswith("word/fonts/"):
                    fonts_by_digest[hashlib.sha256(z.read(n)).hexdigest()].add(
                        path.encode())
            data = open(path, "rb").read()
            try:
                out = f1.scrub(data)
                outcomes["cleaned" + (" (residuals!)" if f1.residuals(out) else "")] += 1
            except ScrubError as exc:
                outcomes["refused"] += 1
                reason = re.sub(r"^[^:]*: ", "", str(exc).split(";")[0])
                reasons[re.sub(r"\d+", "#", reason)[:90]] += 1
            except Exception as exc:                      # noqa: BLE001
                outcomes["ESCAPE"] += 1
                reasons[f"ESCAPE {type(exc).__name__}"] += 1
    print(f"documents by producer: {dict(producers.most_common())}")
    print(f"\nWord-made: {word_docs}")
    print("parts present (documents):")
    for k, v in sorted(parts.items(), key=lambda x: -x[1]):
        print(f"  {v:4}  {k}")
    print("picture names and descriptions Word wrote (occurrences):")
    for k, v in sorted(names.items()):
        print(f"  {v:4}  {k}")
    print(f"external relationship targets: {dict(external)}")
    print(f"embedded font files seen in more than one document: "
          f"{sum(1 for v in fonts_by_digest.values() if len(v) > 1)} of "
          f"{len(fonts_by_digest)}")
    print(f"F1 outcomes: {dict(outcomes)}")
    for r, c in reasons.most_common(12):
        print(f"  {c:4}  {r}")


if __name__ == "__main__":
    main(sys.argv[1:])
