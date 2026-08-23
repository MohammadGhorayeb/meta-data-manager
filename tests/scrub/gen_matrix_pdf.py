"""Generate the PDF Pareto matrix from real harness runs.

Honesty rule (CLAUDE.md): a cell is only pass/fail if we measured it.

Measured here:
  - A1@F1/F2/F3  differential leak oracle over same-page / different-metadata PDFs.
  - A2@F1/F2     producer peer set (experiment E-PDF), reported **per channel**. F1
                 closes the serializer channel outright — our own writer decides the
                 header, `/ID`, xref style, object streams and `/Length` — while the
                 layout channel is untouched, because F1 never rewrites a content
                 stream. F2 rewrites every content stream through one writer, which
                 closes the text machine's *spelling* but not its geometry.
  - A2@F3        comes from **E-PDF-RASTER, not E-PDF**, and that choice is the whole
                 point of the cell. Structurally F3 passes almost trivially: the file
                 is entirely our own output, so there is barely anything left for a
                 structural comparison to separate producers with. Publishing that as
                 the verdict would be exactly the overclaim this project exists not to
                 make — the page is an image now, and the typesetter's geometry is
                 painted into it. So the cell is decided by attacking the rendered
                 pixels, and it fails.

The per-channel reporting is why running E-PDF before F2 was designed was worth it.
"A2 fails" on its own would have told M4 nothing; "A2 fails on the layout channel
only, with the serializer channel already closed" told it exactly what remained — and
W5's warning, since confirmed, is that only *part* of what remains can be normalised
without re-typesetting the page.

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
from tests.scrub import e_pdf, e_pdf_raster
from tests.scrub import pdf_corpus as pc

TOOL = {
    "name": "irreversible_scrubber",
    "version": "0.1.0-p3",
    "invocation": "python -m src.scrub {in} {out} --fidelity {fidelity}",
}

# The number of distinct documents E-PDF-RASTER classifies over. Six per producer is
# 30 samples across five producers, enough that a one-sided binomial test against a
# 0.20 chance rate can reach significance, and small enough that generating the corpus
# (Chrome, LibreOffice and cupsfilter, six times each) stays inside a matrix build.
_RASTER_DOCS = 6

# Without poppler, **no** PDF cell may claim anything — not just the F3 ones.
# `pdftotext` is how this format checks that a scrub preserved the document's
# content, which is hard constraint #1; a cell that passed its leak test while
# content preservation went unverified would be claiming the wrong thing entirely.
# So the whole matrix reports untested rather than partially measured, and an absent
# tool is never reported as a clean result (limit #12).
_NO_POPPLER = ("poppler (pdftotext/pdftoppm) is not installed on this machine. "
               "pdftotext is how a PDF scrub's content preservation is verified and "
               "pdftoppm is the whole of F3, so nothing here can be measured. "
               "Untested, never clean.")


def _scrubber():
    from tests.scrub.test_harness_a1 import InProcessScrubber
    return InProcessScrubber()


def build_doc(tmpdir: str) -> dict:
    plugin = PdfPlugin()
    scrubber = _scrubber()
    cells = []

    if not pc.HAVE_POPPLER:
        return matrix.assemble(
            "pdf", TOOL,
            [Cell(a, f, V.NOT_TESTED, reason=_NO_POPPLER)
             for a in ("A1", "A2") for f in ("F1", "F2", "F3")],
            matrix.fingerprint_block(V.NOT_TESTED.value, None, [],
                                     checked=False))

    # --- A1: same page, metadata differing only by a sentinel ---
    variants = pc.a1_variants(tmpdir, n_variants=3, n_repeats=5)
    for fid in ("F1", "F2", "F3"):
        cells.append(leak.evaluate_a1(scrubber, plugin, variants, fid, n=5,
                                      sentinel_field="metadata_variant",
                                      modality="bytes"))

    # --- A2: producer peer set, per channel ---
    sources = e_pdf.build_sources(tmpdir, repeats=3)
    raw = e_pdf.run_condition("raw", sources, tmpdir)
    for fid in ("F1", "F2"):
        cells.append(e_pdf.evaluate_cell(fid, sources, tmpdir, raw=raw))

    # --- A2 at F3: decided on the PIXELS, not on the structure ---
    # Structurally F3 passes trivially — the file is entirely our own output, so
    # E-PDF finds almost nothing left to separate producers with. Publishing that as
    # the A2@F3 verdict would be the overclaim this project exists not to make: the
    # page is now an image, and the typesetter's geometry is painted into it. So the
    # cell comes from E-PDF-RASTER, which attacks the rendered page directly.
    raster_dir = os.path.join(tmpdir, "raster")
    os.makedirs(raster_dir, exist_ok=True)
    docs = pc.documents(raster_dir, n_docs=_RASTER_DOCS)
    control = e_pdf_raster.run_condition(docs, raster_dir, dpi=None)
    scrubbed = e_pdf_raster.run_condition(docs, raster_dir,
                                          dpi=e_pdf_raster.f3.DEFAULT_DPI)
    cells.append(e_pdf_raster.evaluate_cell(control, scrubbed))

    # --- fingerprint guard over small, diverse inputs ---
    diverse = pc.diverse_inputs(tmpdir, n=4)
    # Run on F2, the strongest tier that exists: it rewrites every content stream
    # through one writer, which is exactly where a scrubber-wide constant would be
    # introduced if we had introduced one.
    gv, gsig = fingerprint_guard.evaluate(scrubber, plugin, diverse, "F2",
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
