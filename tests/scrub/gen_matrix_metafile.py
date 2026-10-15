"""Generate the EMF/WMF ("metafile") Pareto matrix from real harness runs.

Measured here:
  - A1@F1   differential leak oracle over files whose planted metadata differs only
            in value (`metafile_corpus`): every locus the survey of 409 real Word
            metafiles found -- the header description, private and string comments,
            a group's description, the hidden WMF copy, EMF+ comments, EMF+
            pictures' own EXIF (one split across continuation records), bytes after
            the end; for WMF, private comments, the embedded source EMF and trailing
            bytes. Content identity is the drawing records plus the pictures'
            decoded pixels.

Not measured, and said so:
  - A2: Office writes these files and cannot run here; the one EMF/WMF writer
    available (LibreOffice) cannot make a peer set on its own. The writer's traits
    F1 keeps are listed as the F2 specification instead.
  - F2/F3 are not built.

Run:  ./.venv/bin/python -m tests.scrub.gen_matrix_metafile
"""
from __future__ import annotations

import os
import tempfile

from tests.harness import config
from tests.harness.contract import Cell, V
from tests.harness.oracle import fingerprint_guard, leak
from tests.harness.plugins.metafile import MetafilePlugin
from tests.harness.runner import matrix
from tests.scrub import metafile_corpus as mc

TOOL = {
    "name": "irreversible_scrubber",
    "version": "0.1.0-w2b",
    "invocation": "python -m src.scrub {in} {out} --fidelity {fidelity}",
}

KEPT_TRAITS = ("the EMF header's size and version, the reference device (a screen's "
               "or printer's pixels and millimetres), EMF+ DPI and flags, the kinds "
               "and order of records a writer emits, a WMF's units per inch")


def _scrubber():
    from tests.scrub.test_harness_a1 import InProcessScrubber
    return InProcessScrubber()


def _variants(tmpdir: str, kind: str, n: int = 3, repeats: int = 3):
    groups = []
    for i in range(n):
        blob = mc.emf(i + 1, chunk=300) if kind == "emf" else mc.wmf(i + 1)
        paths = []
        for r in range(repeats):
            path = os.path.join(tmpdir, f"a1_{kind}_v{i}_r{r}.{kind}")
            with open(path, "wb") as f:
                f.write(blob)
            paths.append(path)
        groups.append(paths)
    return groups


def _a1_cell(tmpdir: str) -> Cell:
    scrubber, plugin = _scrubber(), MetafilePlugin()
    failed, leaks = [], []
    for kind in ("emf", "wmf"):
        cell = leak.evaluate_a1(scrubber, plugin, _variants(tmpdir, kind), "F1",
                                n=3, sentinel_field="metadata_variant",
                                modality="bytes")
        if cell.verdict != V.PASS:
            failed.append(kind.upper())
            leaks += cell.leaks
    if failed:
        return Cell("A1", "F1", V.FAIL, reason="variant-correlated bytes survive in "
                    + ", ".join(failed), leaks=leaks)
    return Cell("A1", "F1", V.PASS,
                reason="no metadata-correlated byte survives in EMF (header "
                       "description, private/string/group comments, the hidden WMF "
                       "copy, EMF+ comments, EMF+ pictures' EXIF -- one split across "
                       "records -- trailing bytes) or WMF (private comments, the "
                       "embedded source EMF, trailing bytes)")


def _diverse(tmpdir: str) -> list[str]:
    blobs = [("emf", mc.emf(1, seed=1)),
             ("emf", mc.emf(2, loci=("description", "private_comment"), seed=2)),
             ("wmf", mc.wmf(3, seed=3)),
             ("wmf", mc.wmf(4, loci=("comment",), seed=4, placeable=False))]
    paths = []
    for i, (kind, blob) in enumerate(blobs):
        path = os.path.join(tmpdir, f"div_{i}.{kind}")
        with open(path, "wb") as f:
            f.write(blob)
        paths.append(path)
    return paths


def build_doc(tmpdir: str) -> dict:
    not_built = ("metafile_f2_f3_not_built: a canonical rewrite (one header layout, "
                 "one reference device, records re-emitted by one writer) is not built")
    no_peers = ("metafile_a2_no_peer_set: Office writes these and does not run here; "
                "LibreOffice alone is no peer set. Kept by F1, so the F2 "
                f"specification: {KEPT_TRAITS}")
    cells = [_a1_cell(tmpdir),
             Cell("A1", "F2", V.NOT_TESTED, reason=not_built),
             Cell("A1", "F3", V.NOT_TESTED, reason=not_built),
             Cell("A2", "F1", V.NOT_TESTED, reason=no_peers),
             Cell("A2", "F2", V.NOT_TESTED, reason=not_built),
             Cell("A2", "F3", V.NOT_TESTED, reason=not_built)]
    verdict, signatures = fingerprint_guard.evaluate(
        _scrubber(), MetafilePlugin(), _diverse(tmpdir), "F1",
        min_len=config.MIN_SIG_LEN)
    return matrix.assemble("metafile", TOOL, cells,
                           matrix.fingerprint_block(verdict, signatures, []))


def main() -> str:
    doc = build_doc(tempfile.mkdtemp(prefix="metafile_matrix_"))
    out_path = os.path.join(str(config.RESULTS_DIR), f"metafile_{TOOL['name']}.json")
    matrix.write(doc, out_path)
    return out_path


if __name__ == "__main__":
    print(main())
