"""Generate the plain-TIFF Pareto matrix from real harness runs.

Measured here:
  - A1@F1   differential leak oracle over hand-built TIFFs (`tiff_corpus.build`)
            whose planted metadata differs only in content, length kept: every IFD0
            string (DocumentName and PageName included), the XP fields, the ExifIFD,
            the GPS IFD, XMP, IPTC, Photoshop, the maker note, a personal ICC
            profile's header, a second page's name and the thumbnail's own EXIF.
            Both byte orders. Content identity is every page's decoded pixels.
  - A2@F1   E-TIFF: one picture through every TIFF writer here, pixels identical.
            Expected to FAIL -- F1 keeps the writer's layout -- and run for which
            keys survive, the specification for an F2.

Not measured, and said so rather than left blank:
  - F2      not built: re-storing the pixels losslessly through one canonical writer.
  - F3      not built: a lossy re-encode has no reason to exist for a format whose
            fingerprint an F2 could already remove.

Run:  ./.venv/bin/python -m tests.scrub.gen_matrix_tiff
"""
from __future__ import annotations

import os
import tempfile

from tests.harness import config
from tests.harness.contract import Cell, Leak, Locus, V
from tests.harness.oracle import fingerprint_guard, leak
from tests.harness.plugins.tiff import TiffPlugin
from tests.harness.runner import matrix
from tests.scrub import e_tiff
from tests.scrub import tiff_corpus as tc

TOOL = {
    "name": "irreversible_scrubber",
    "version": "0.1.0-p6",
    "invocation": "python -m src.scrub {in} {out} --fidelity {fidelity}",
}


def _scrubber():
    from tests.scrub.test_harness_a1 import InProcessScrubber
    return InProcessScrubber()


def _variants(tmpdir: str, order: str, n_variants: int = 3, n_repeats: int = 3):
    groups = []
    for i in range(n_variants):
        blob = tc.build(i + 1, order=order)
        paths = []
        for r in range(n_repeats):
            path = os.path.join(tmpdir, f"a1_{order == '<'}_v{i}_r{r}.tif")
            with open(path, "wb") as f:
                f.write(blob)
            paths.append(path)
        groups.append(paths)
    return groups


def _a1_cell(tmpdir: str) -> Cell:
    scrubber, plugin = _scrubber(), TiffPlugin()
    failed, leaks = [], []
    for order, label in (("<", "II"), (">", "MM")):
        cell = leak.evaluate_a1(scrubber, plugin, _variants(tmpdir, order), "F1",
                                n=3, sentinel_field="metadata_variant",
                                modality="bytes")
        if cell.verdict != V.PASS:
            failed.append(label)
            leaks += cell.leaks
    if failed:
        return Cell("A1", "F1", V.FAIL, reason="variant-correlated bytes survive in "
                    + ", ".join(failed), leaks=leaks)
    return Cell("A1", "F1", V.PASS,
                reason="no metadata-correlated byte survives, in either byte order "
                       "(IFD0 strings incl. DocumentName/PageName, XP fields, EXIF, "
                       "GPS, XMP, IPTC, Photoshop, maker note, a personal ICC "
                       "header, a second page, the thumbnail's EXIF)")


def _a2_cell(tmpdir: str) -> Cell:
    sources = e_tiff.build_sources(tmpdir)
    r = e_tiff.run_condition("F1", sources, tmpdir)
    raw = e_tiff.run_condition("raw", sources, tmpdir)
    prods = ", ".join(r["producers"])
    if len(r["producers"]) < 2:
        return Cell("A2", "F1", V.NOT_TESTED,
                    reason=f"tiff_a2_not_measured: one TIFF writer here ({prods})")
    survived = r["struct_fingerprints"]
    if not survived:
        return Cell("A2", "F1", V.PASS,
                    reason=f"no structural key separates the writers ({prods})")
    closed = sorted(set(raw["struct_fingerprints"]) - set(survived))
    leaks = [Leak("source_fingerprint", Locus("structural", feature_id=k),
                  "TIFF writer", f"{k} constant within a writer, differs across")
             for k in survived]
    return Cell("A2", "F1", V.FAIL, leaks=leaks,
                reason=("writer still identifiable: "
                        + ", ".join(k.removeprefix("struct:") for k in survived)
                        + (f"; F1 closed {', '.join(k.removeprefix('struct:') for k in closed)}"
                           if closed else "")
                        + f". Peer set = {prods}."))


def _diverse(tmpdir: str) -> list[str]:
    blobs = [tc.build(1, order="<", seed=1), tc.build(2, order=">", pages=1, seed=2,
                                                      icc="standard"),
             tc.build(3, order="<", pages=3, thumb=False, icc="none", seed=3)]
    paths = []
    for i, blob in enumerate(blobs):
        path = os.path.join(tmpdir, f"div_{i}.tif")
        with open(path, "wb") as f:
            f.write(blob)
        paths.append(path)
    return paths


def build_doc(tmpdir: str) -> dict:
    not_built = ("tiff_f2_not_built: re-storing the pixels losslessly through one "
                 "canonical writer is not built")
    no_f3 = ("tiff_f3_not_built: a lossy tier has no reason to exist where an F2 "
             "could remove the writer's fingerprint without loss")
    cells = [_a1_cell(tmpdir),
             Cell("A1", "F2", V.NOT_TESTED, reason=not_built),
             Cell("A1", "F3", V.NOT_TESTED, reason=no_f3),
             _a2_cell(tmpdir),
             Cell("A2", "F2", V.NOT_TESTED, reason=not_built),
             Cell("A2", "F3", V.NOT_TESTED, reason=no_f3)]
    plugin = TiffPlugin()
    verdict, signatures = fingerprint_guard.evaluate(
        _scrubber(), plugin, _diverse(tmpdir), "F1", min_len=config.MIN_SIG_LEN)
    excluded = [{"bytes_hex": c.hex(), "decoded": c.decode("latin-1", "replace")}
                for c in plugin.mandatory_constants()]
    return matrix.assemble("tiff", TOOL, cells,
                           matrix.fingerprint_block(verdict, signatures, excluded))


def main() -> str:
    doc = build_doc(tempfile.mkdtemp(prefix="tiff_matrix_"))
    out_path = os.path.join(str(config.RESULTS_DIR), f"tiff_{TOOL['name']}.json")
    matrix.write(doc, out_path)
    return out_path


if __name__ == "__main__":
    print(main())
