"""DOCX F3 — re-typeset through one engine, then canonicalise.

F2 closed the document model's *spelling* and left its *substance*: which parts a
producer emits, which styles it defines, what it records in `settings.xml`, how it
builds a paragraph (§2.14). None of that can be normalised by rewriting, because
changing it changes the document.

It can be **regenerated**. Opening the document in one engine and saving it again
produces that engine's part set, that engine's styles, that engine's paragraph idiom
— for every input. This is the DOCX analogue of PDF F3's rasterise, and it differs
from it in the way that matters: rasterising *relocates* the typesetter's geometry
into pixels, where E-PDF-RASTER then read the producer off the ink at 100%, while a
round-trip **rebuilds the symbolic model** rather than photographing it. So this is
the one tier in the project where F3 has a real chance of buying what F2 could not.

Whether it does is measured (E-DOCX at F3), not assumed.

**The cost is real and is not a compression artefact.** LibreOffice is not Word, and a
round-trip through it re-typesets the page: fonts substitute, spacing shifts, a
complex layout can move. F1 and F2 preserve the rendering exactly; this tier does not,
and that is stated wherever it is offered rather than after it is used.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

from ...errors import ContentError, FidelityError, ParseError
from ..ooxml import opc
from . import f2

ENGINE = "soffice"
# Pinned so the output is a function of the engine and the filter, not of whatever
# LibreOffice guesses from an extension.
FILTER = "docx:MS Word 2007 XML"
TIMEOUT = 300


def available() -> bool:
    return shutil.which(ENGINE) is not None


def _round_trip(data: bytes, workdir: str) -> bytes:
    src = os.path.join(workdir, "in.docx")
    with open(src, "wb") as f:
        f.write(data)
    out = os.path.join(workdir, "out")
    os.makedirs(out, exist_ok=True)
    try:
        proc = subprocess.run(
            [ENGINE, "--headless", "--convert-to", FILTER, "--outdir", out, src],
            capture_output=True, timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ContentError(f"{ENGINE} failed to re-typeset the document: {exc}") from exc

    produced = os.path.join(out, "in.docx")
    if not os.path.exists(produced) or os.path.getsize(produced) == 0:
        err = (proc.stderr or proc.stdout).decode("utf-8", "replace").strip()
        raise ContentError(
            f"{ENGINE} produced no document ({err or 'no output'}) — refusing rather "
            f"than returning the input unchanged, which would look like a scrub")
    with open(produced, "rb") as f:
        return f.read()


def scrub(data: bytes) -> bytes:
    """Re-typeset through one engine, then apply F2 to its output.

    F2 afterwards is not optional: LibreOffice writes its own `docProps/core.xml`,
    its own wall-clock ZIP timestamps and its own `Application` string, so a
    round-trip **adds** metadata. The engine regenerates the model; F2 removes what
    the engine put in and canonicalises what is left.
    """
    if not available():
        raise FidelityError(
            f"docx F3 needs {ENGINE} (LibreOffice) to re-typeset the document, and it "
            f"is not installed. F1 and F2 need no external engine.")

    pkg = opc.parse(data)
    if pkg.flavour != "docx":
        raise ParseError(f"not a DOCX package (looks like {pkg.flavour})")
    # The engine would happily open a macro-enabled or OLE-bearing package and drop
    # things silently. Refuse for the same reasons F1 and F2 do, before handing the
    # document to something we do not control.
    structural = [p for p in pkg.refusals()]
    if structural:
        raise ContentError("; ".join(sorted(set(structural))))

    with tempfile.TemporaryDirectory(prefix="docx_f3_") as workdir:
        retyped = _round_trip(data, workdir)
    return f2.scrub(retyped)


def residuals(data: bytes) -> list[str]:
    return f2.residuals(data)


def advisories(data: bytes) -> list[str]:
    out = list(f2.advisories(data))
    out.append(
        "F3 re-typeset the document through LibreOffice: fonts may have substituted "
        "and spacing may have shifted. F1 and F2 preserve the rendering exactly; this "
        "tier trades it for producer anonymity (limit #26)")
    return out
