"""Generate the PDF Pareto matrix from real harness runs.

Honesty rule (CLAUDE.md): a cell is only pass/fail if we measured it.

Measured here:
  - A1@F1   differential leak oracle over same-page / different-metadata PDFs.
  - A2@F1   producer peer set (experiment E-PDF), reported **per channel**: F1
            closes the serializer channel outright — our own writer decides the
            header, `/ID`, xref style, object streams and `/Length` — while the
            layout channel (operators, number precision, font subsets, glyph
            geometry) is untouched, because F1 never rewrites a content stream.
  - F2, F3  not_tested: the tiers do not exist yet (M4/M5). Recorded as untested
            rather than as not_applicable — they are planned, not inapplicable, and
            the difference matters to anyone reading the matrix.

The A2@F1 cell is the point of running this before F2 is designed. "A2 fails" on its
own would tell M4 nothing; "A2 fails on the layout channel only, with the serializer
channel already closed" tells it exactly what remains — and W5's warning is that only
*part* of what remains can be normalised without re-typesetting the page.

Run:  ./.venv/bin/python -m tests.scrub.gen_matrix_pdf
"""
from __future__ import annotations

import os
import tempfile

from tests.harness import config
from tests.harness.contract import Cell, V
from tests.harness.oracle import fingerprint_guard, leak
from tests.harness.plugins.pdf import PdfPlugin
from tests.harness.runner import matrix
from tests.scrub import e_pdf
from tests.scrub import pdf_corpus as pc

TOOL = {
    "name": "irreversible_scrubber",
    "version": "0.1.0-p3",
    "invocation": "python -m src.scrub {in} {out} --fidelity {fidelity}",
}

_UNBUILT = ("F2 is Phase 3 M4 (canonical re-serialisation plus content-stream "
            "canonicalisation) and F3 is M5 (rasterise). Neither exists, so this "
            "cell is untested rather than inapplicable.")


def _scrubber():
    from tests.scrub.test_harness_a1 import InProcessScrubber
    return InProcessScrubber()


def build_doc(tmpdir: str) -> dict:
    plugin = PdfPlugin()
    scrubber = _scrubber()
    cells = []

    # --- A1: same page, metadata differing only by a sentinel ---
    variants = pc.a1_variants(tmpdir, n_variants=3, n_repeats=5)
    cells.append(leak.evaluate_a1(scrubber, plugin, variants, "F1", n=5,
                                  sentinel_field="metadata_variant",
                                  modality="bytes"))
    for fid in ("F2", "F3"):
        cells.append(Cell("A1", fid, V.NOT_TESTED, reason=_UNBUILT))

    # --- A2: producer peer set, per channel ---
    sources = e_pdf.build_sources(tmpdir, repeats=3)
    raw = e_pdf.run_condition("raw", sources, tmpdir)
    cells.append(e_pdf.evaluate_cell("F1", sources, tmpdir, raw=raw))
    for fid in ("F2", "F3"):
        cells.append(Cell("A2", fid, V.NOT_TESTED, reason=_UNBUILT))

    # --- fingerprint guard over small, diverse inputs ---
    diverse = pc.diverse_inputs(tmpdir, n=4)
    gv, gsig = fingerprint_guard.evaluate(scrubber, plugin, diverse, "F1",
                                          min_len=config.MIN_SIG_LEN)
    excluded = [{"bytes_hex": c.hex(), "decoded": c.decode("latin-1", "replace")}
                for c in plugin.mandatory_constants()]
    fp = matrix.fingerprint_block(gv, gsig, excluded)
    return matrix.assemble("pdf", TOOL, cells, fp)


def main() -> str:
    tmpdir = tempfile.mkdtemp(prefix="pdf_matrix_")
    doc = build_doc(tmpdir)
    out_path = os.path.join(str(config.RESULTS_DIR), f"pdf_{TOOL['name']}.json")
    matrix.write(doc, out_path)
    return out_path


if __name__ == "__main__":
    print(main())
