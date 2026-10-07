"""Generate the SVG Pareto matrix from real harness runs.

Measured here:
  - A1@F1   differential leak oracle over documents shaped like Inkscape's,
            Illustrator's, Sketch's and LibreOffice's exports (`svg_corpus.build`),
            planted metadata differing only in content: editor namespaces (file
            name, version, an export path), RDF metadata, generator comments and
            descriptions, processing instructions, Illustrator's private data and
            DOCTYPE entities -- and the metadata of every embedded picture (a photo's
            EXIF, a PNG text chunk, a nested SVG's own metadata). Content identity is
            the rendering through librsvg.
  - A2@F1   E-SVG: one drawing written by cairo (a real serializer, via
            rsvg-convert) and three corpus models of real producers' idioms.
            Expected to fail -- F1 only deletes, so a producer's vocabulary stays.

Not measured, and said so: F2/F3 are not built (a canonical re-serialisation, a
re-drawing).

Run:  ./.venv/bin/python -m tests.scrub.gen_matrix_svg
"""
from __future__ import annotations

import os
import tempfile

from tests.harness import config
from tests.harness.contract import Cell, Leak, Locus, V
from tests.harness.oracle import fingerprint_guard, leak
from tests.harness.plugins.svg import SvgPlugin
from tests.harness.runner import matrix
from tests.scrub import e_svg
from tests.scrub import svg_corpus as sc

TOOL = {
    "name": "irreversible_scrubber",
    "version": "0.1.0-p6",
    "invocation": "python -m src.scrub {in} {out} --fidelity {fidelity}",
}


def _scrubber():
    from tests.scrub.test_harness_a1 import InProcessScrubber
    return InProcessScrubber()


def _variants(tmpdir: str, shape: str, n: int = 3, repeats: int = 3):
    groups = []
    for i in range(n):
        blob = sc.build(i + 1, shape=shape)
        paths = []
        for r in range(repeats):
            path = os.path.join(tmpdir, f"a1_{shape}_v{i}_r{r}.svg")
            with open(path, "wb") as f:
                f.write(blob)
            paths.append(path)
        groups.append(paths)
    return groups


def _a1_cell(tmpdir: str) -> Cell:
    scrubber, plugin = _scrubber(), SvgPlugin()
    failed, leaks = [], []
    for shape in sc.SHAPES:
        cell = leak.evaluate_a1(scrubber, plugin, _variants(tmpdir, shape), "F1",
                                n=3, sentinel_field="metadata_variant",
                                modality="bytes")
        if cell.verdict != V.PASS:
            failed.append(shape)
            leaks += cell.leaks
    if failed:
        return Cell("A1", "F1", V.FAIL, reason="variant-correlated bytes survive in "
                    + ", ".join(failed), leaks=leaks)
    return Cell("A1", "F1", V.PASS,
                reason="no metadata-correlated byte survives in documents shaped like "
                       "Inkscape's, Illustrator's, Sketch's and LibreOffice's exports, "
                       "embedded pictures included (a photo's EXIF, a PNG text chunk, "
                       "a nested SVG's metadata)")


def _a2_cell(tmpdir: str) -> Cell:
    sources = e_svg.build_sources(tmpdir)
    r = e_svg.run_condition("F1", sources, tmpdir)
    prods = ", ".join(r["producers"])
    modelled = [p for p in r["producers"] if p.endswith("_model")]
    survived = r["struct_fingerprints"]
    peer = (f"Peer set = {prods}; " + (f"{', '.join(modelled)} are corpus models of "
            "those producers' idioms, " if modelled else "")
            + ("cairo is a real serializer." if "cairo" in r["producers"]
               else "no real second serializer was available here."))
    if not survived:
        return Cell("A2", "F1", V.PASS,
                    reason=f"no structural key separates the producers. {peer}")
    leaks = [Leak("source_fingerprint", Locus("structural", feature_id=k),
                  "SVG producer", f"{k} constant within a producer, differs across")
             for k in survived]
    return Cell("A2", "F1", V.FAIL, leaks=leaks,
                reason=("producer still identifiable -- F1 only deletes, so how a "
                        "producer writes XML stays: " + ", ".join(
                            k.removeprefix("struct:") for k in survived) + ". " + peer))


def _diverse(tmpdir: str) -> list[str]:
    paths = []
    for i, shape in enumerate(sc.SHAPES):
        path = os.path.join(tmpdir, f"div_{i}.svg")
        with open(path, "wb") as f:
            f.write(sc.build(i + 5, shape=shape, seed=i + 1))
        paths.append(path)
    return paths


def build_doc(tmpdir: str) -> dict:
    not_built = ("svg_f2_f3_not_built: a canonical re-serialisation (F2) and a "
                 "re-drawing (F3) are not built")
    cells = [_a1_cell(tmpdir),
             Cell("A1", "F2", V.NOT_TESTED, reason=not_built),
             Cell("A1", "F3", V.NOT_TESTED, reason=not_built),
             _a2_cell(tmpdir),
             Cell("A2", "F2", V.NOT_TESTED, reason=not_built),
             Cell("A2", "F3", V.NOT_TESTED, reason=not_built)]
    plugin = SvgPlugin()
    verdict, signatures = fingerprint_guard.evaluate(
        _scrubber(), plugin, _diverse(tmpdir), "F1", min_len=config.MIN_SIG_LEN)
    return matrix.assemble("svg", TOOL, cells,
                           matrix.fingerprint_block(verdict, signatures, []))


def main() -> str:
    doc = build_doc(tempfile.mkdtemp(prefix="svg_matrix_"))
    out_path = os.path.join(str(config.RESULTS_DIR), f"svg_{TOOL['name']}.json")
    matrix.write(doc, out_path)
    return out_path


if __name__ == "__main__":
    print(main())
