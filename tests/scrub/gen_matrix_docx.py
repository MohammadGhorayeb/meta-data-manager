"""Generate the DOCX Pareto matrix from real harness runs.

Honesty rule (CLAUDE.md): a cell is only pass/fail if we measured it.

Measured here:
  - A1@F1  differential leak oracle over same-document / different-metadata packages,
           with the sentinel in `core.xml`, `app.xml`, `custom.xml` and `people.xml`
           at once, because clearing one of four is the classic half-scrub.
  - A2@F1  producer peer set (experiment E-DOCX), reported **per channel**. A DOCX has
           two producers in one file — the packager that laid out the ZIP and the
           document model that wrote the WordprocessingML — so the cell names which
           one leaked instead of averaging them. F1 closes the packager channel
           outright.
  - A1@F2 / A2@F2  the same two oracles after canonical re-serialisation. F2 closes
           the model's *spelling* (namespace set, self-closing style, XML declaration)
           and leaves its *substance* (which parts exist, which styles are defined,
           what `settings.xml` contains, how a paragraph is represented) — reported
           per key, because a channel verdict alone would read the same whether F2 had
           collapsed most of the channel or none of it.
  - A1@F3 / A2@F3  after a re-typeset through one engine, which *regenerates* the
           model rather than relocating it into pixels as PDF F3 does. It collapses
           the model channel to a single key, and that key is a **primary-production
           trace** — the source document's own styles, which the engine preserves
           rather than rebuilds — not a trait of the immediate producer. The cell
           still fails, and says which key.

F2 and F3 are `not_tested` rather than absent, because the tiers do not exist yet
(M13, M14) and an empty cell reads like a measurement nobody took rather than a
feature nobody built.

**Word is not in the peer set, and the matrix says so in the cell's own reason.** It
has no CLI on any platform, so it cannot be made to render *our* document; putting an
unrelated Word file in the set would have the classifier separating documents while
appearing to separate producers. That is the limit-#12 precedent — an absent producer
makes a cell say *not measured*, never *clean*.

Run:  ./.venv/bin/python -m tests.scrub.gen_matrix_docx
"""
from __future__ import annotations

import os
import tempfile

from src.scrub.formats.docx import f3 as docx_f3
from tests.harness import config
from tests.harness.contract import Cell, V
from tests.harness.oracle import fingerprint_guard, leak
from tests.harness.plugins.docx import DocxPlugin
from tests.harness.runner import matrix
from tests.scrub import docx_corpus as C
from tests.scrub import e_docx

TOOL = {
    "name": "irreversible_scrubber",
    "version": "0.1.0-p3",
    "invocation": "python -m src.scrub {in} {out} --fidelity {fidelity}",
}

_NO_ENGINE = ("DOCX F3 re-typesets the document through LibreOffice, which is not "
              "installed on this machine, so the tier cannot run and nothing about "
              "it can be measured. Untested, never clean (limit #12).")


def _scrubber():
    from tests.scrub.test_harness_a1 import InProcessScrubber
    return InProcessScrubber()


def build_doc(tmpdir: str) -> dict:
    plugin = DocxPlugin()
    scrubber = _scrubber()
    cells = []

    # --- A1: same document, metadata differing only by a sentinel ---
    variants = C.a1_variants(tmpdir, n_variants=3, n_repeats=5)
    tiers = ("F1", "F2", "F3") if docx_f3.available() else ("F1", "F2")
    for fid in tiers:
        cells.append(leak.evaluate_a1(scrubber, plugin, variants, fid, n=5,
                                      sentinel_field="metadata_variant",
                                      modality="bytes"))

    # --- A2: producer peer set, per channel ---
    sources = e_docx.build_sources(tmpdir, repeats=3)
    raw = e_docx.run_condition("raw", sources, tmpdir)
    for fid in tiers:
        cells.append(e_docx.evaluate_cell(fid, sources, tmpdir, raw=raw))

    if not docx_f3.available():
        for adv in ("A1", "A2"):
            cells.append(Cell(adv, "F3", V.NOT_TESTED, reason=_NO_ENGINE))

    # --- fingerprint guard over small, diverse inputs ---
    diverse = C.diverse_inputs(tmpdir, n=4)
    # Run on F2, the strongest tier that exists: it rewrites every part through one
    # writer, which is exactly where a scrubber-wide constant would be introduced.
    gv, gsig = fingerprint_guard.evaluate(scrubber, plugin, diverse, "F2",
                                          min_len=config.MIN_SIG_LEN)
    excluded = [{"bytes_hex": c.hex(), "decoded": c.decode("latin-1", "replace")}
                for c in plugin.mandatory_constants()]
    fp = matrix.fingerprint_block(gv, gsig, excluded)
    return matrix.assemble("docx", TOOL, cells, fp)


def main() -> str:
    tmpdir = tempfile.mkdtemp(prefix="docx_matrix_")
    doc = build_doc(tmpdir)
    out_path = os.path.join(str(config.RESULTS_DIR), f"docx_{TOOL['name']}.json")
    matrix.write(doc, out_path)
    return out_path


if __name__ == "__main__":
    print(main())
