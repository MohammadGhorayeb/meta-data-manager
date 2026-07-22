"""Generate the JPEG Pareto matrix for our scrubber from real harness runs.

Honesty rule (CLAUDE.md): a cell is only `pass`/`fail` if we measured it. Right
now that is **A1@F1 and A1@F2** (validated by the differential oracle).
Everything else is `not_tested` with a reason:
  - A1@F3              handler not built yet (M4)
  - A2@F1/F2/F3        needs the DQT peer-set experiment E3 (M3) — we do NOT
                       pre-assert the expected "A2 requires F3" result
The A3 row is auto-emitted as not_tested/out_of_scope by matrix.assemble().

Run:  ./.venv/bin/python -m tests.scrub.gen_matrix
Writes tests/harness/results/jpeg_irreversible_scrubber.json (schema-validated).
"""
from __future__ import annotations

import os
import tempfile

from tests.harness import config
from tests.harness.contract import Cell, V
from tests.harness.oracle import fingerprint_guard, leak
from tests.harness.runner import matrix
from tests.harness.plugins.jpeg import JpegPlugin
from tests.scrub import corpus

TOOL = {
    "name": "irreversible_scrubber",
    "version": "0.1.0-p1",
    "invocation": "python -m src.scrub {in} {out} --fidelity {fidelity}",
}


def _scrubber():
    # imported here to avoid a hard test dependency at module import time
    from tests.scrub.test_harness_a1 import InProcessScrubber
    return InProcessScrubber()


def build_doc(tmpdir: str, a2_corpus_dir: str | None = None) -> dict:
    plugin = JpegPlugin()
    scrubber = _scrubber()

    # --- measured cells: A1 @ F1 and A1 @ F2 ---
    variants = corpus.make_jpeg_a1_variants(tmpdir, n_variants=3, n_repeats=5)
    a1f1 = leak.evaluate_a1(scrubber, plugin, variants, "F1", n=5,
                            sentinel_field="metadata_variant")
    cells = [a1f1]

    import shutil
    if shutil.which("jpegtran") is not None:
        variants_f2 = corpus.make_jpeg_a1_variants(tmpdir, n_variants=3, n_repeats=5)
        a1f2 = leak.evaluate_a1(scrubber, plugin, variants_f2, "F2", n=5,
                                sentinel_field="metadata_variant")
        cells.append(a1f2)
    else:
        cells.append(Cell("A1", "F2", V.NOT_TESTED, reason="jpegtran_unavailable"))

    cells.append(Cell("A1", "F3", V.NOT_TESTED, reason="handler_not_implemented"))

    # --- A2: measured by experiment E3 (DQT peer-set) when a peer corpus is
    #     present; otherwise honest not_tested. F3 handler not built yet. ---
    from tests.scrub import e3_dqt
    corpus_dir = a2_corpus_dir or e3_dqt.DEFAULT_CORPUS
    have_corpus = len(e3_dqt.group_corpus(corpus_dir)) >= 2
    for f in ("F1", "F2"):
        if have_corpus:
            cells.append(e3_dqt.evaluate_cell(f, corpus_dir))
        else:
            cells.append(Cell("A2", f, V.NOT_TESTED,
                              reason="pending_dqt_peerset_experiment_E3"))
    cells.append(Cell("A2", "F3", V.NOT_TESTED, reason="handler_not_implemented"))

    # --- fingerprint guard over diverse inputs ---
    diverse = corpus.diverse_jpeg_inputs(tmpdir, n=4)
    gv, gsig = fingerprint_guard.evaluate(scrubber, plugin, diverse, "F1",
                                          min_len=config.MIN_SIG_LEN)
    excluded = [{"bytes_hex": c.hex(), "decoded": c.decode("latin-1", "replace")}
                for c in plugin.mandatory_constants()]
    fp = matrix.fingerprint_block(gv, gsig, excluded)

    return matrix.assemble("jpeg", TOOL, cells, fp)


def main() -> str:
    tmpdir = tempfile.mkdtemp(prefix="jpeg_matrix_")
    doc = build_doc(tmpdir)
    out_path = os.path.join(str(config.RESULTS_DIR),
                            f"jpeg_{TOOL['name']}.json")
    matrix.write(doc, out_path)   # validates against the schema
    return out_path


if __name__ == "__main__":
    print(main())
