"""Generate the EPUB Pareto matrix from real harness runs.

Measured here:
  - A1@F1   differential leak oracle over books whose planted metadata differs only
            in content (`epub_corpus.build`), in five producer shapes: the package
            document's creator, contributor, publisher, rights, dates, subjects,
            description, generator and tool metas, calibre's library identifier and
            columns, the per-export identifier (OPF and NCX), the NCX's author and
            generator, chapters' author and generator metas, comments, calibre's
            bookmarks, an unlisted iTunes record, ZIP times and fields, a cover
            photo's EXIF/GPS and an SVG's comment -- and two obfuscated fonts, whose
            stored bytes depend on the identifier and must come out re-keyed.
  - A2@F1   E-EPUB: one Word document through LibreOffice and pandoc (EPUB 3 and
            EPUB 2); the cell names which of the three channels -- packager,
            package metadata, content model -- still separates them.

Not measured, and said so: F2/F3 are not built. The metadata layout A2@F1 leaves
(element order, id names, the NCX head, package attributes) is F2's specification.

Run:  ./.venv/bin/python -m tests.scrub.gen_matrix_epub
"""
from __future__ import annotations

import os
import tempfile

from tests.harness import config
from tests.harness.contract import Cell, Leak, Locus, V
from tests.harness.oracle import fingerprint_guard, leak
from tests.harness.plugins.epub import EpubPlugin
from tests.harness.runner import matrix
from tests.scrub import e_epub
from tests.scrub import epub_corpus as ec

TOOL = {
    "name": "irreversible_scrubber",
    "version": "0.1.0-p6",
    "invocation": "python -m src.scrub {in} {out} --fidelity {fidelity}",
}
CHANNEL_NAMES = {"pkg_": "packager", "meta_": "package metadata",
                 "content_": "content model"}


def _scrubber():
    from tests.scrub.test_harness_a1 import InProcessScrubber
    return InProcessScrubber()


def _variants(tmpdir: str, shape: str, n: int = 3, repeats: int = 3):
    groups = []
    for i in range(n):
        blob = ec.build(i + 1, shape=shape)
        paths = []
        for r in range(repeats):
            path = os.path.join(tmpdir, f"a1_{shape}_v{i}_r{r}.epub")
            with open(path, "wb") as f:
                f.write(blob)
            paths.append(path)
        groups.append(paths)
    return groups


def _a1_cell(tmpdir: str) -> Cell:
    scrubber, plugin = _scrubber(), EpubPlugin()
    failed, leaks = [], []
    for shape in ec.SHAPES:
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
                reason=f"no metadata-correlated byte survives in any of "
                       f"{len(ec.SHAPES)} producer shapes ({', '.join(ec.SHAPES)}): "
                       "the package document's people, dates and tool lines, the "
                       "per-export identifier in OPF and NCX, chapter metas, "
                       "bookmarks, unlisted files, pictures' own metadata; "
                       "obfuscated fonts re-keyed to the derived identifier")


def _a2_cell(tmpdir: str) -> Cell:
    sources = e_epub.build_sources(tmpdir)
    r = e_epub.run_condition("F1", sources, tmpdir)
    prods = ", ".join(r["producers"])
    open_channels = {c: keys for c, keys in r["by_channel"].items() if keys}
    closed = [CHANNEL_NAMES[c] for c in CHANNEL_NAMES if c not in open_channels]
    if not open_channels:
        return Cell("A2", "F1", V.PASS,
                    reason=f"no key separates the producers ({prods})")
    leaks = [Leak("source_fingerprint", Locus("structural", feature_id=k),
                  f"EPUB producer ({CHANNEL_NAMES[c]})",
                  f"{k} constant within a producer, differs across")
             for c, keys in open_channels.items() for k in keys]
    parts = [f"{CHANNEL_NAMES[c]}: " + ", ".join(
                 k.removeprefix("struct:").removeprefix(c) for k in keys)
             for c, keys in open_channels.items()]
    return Cell("A2", "F1", V.FAIL, leaks=leaks,
                reason=("closed at F1: " + (", ".join(closed) or "none")
                        + ". Still separating -- " + "; ".join(parts)
                        + f". Peer set = {prods} (one Word document, the same "
                          "title and language in its properties)."))


def _diverse(tmpdir: str) -> list[str]:
    blobs = [ec.build(1, shape="pandoc", seed=1), ec.build(2, shape="calibre", seed=2),
             ec.build(3, shape="sigil", seed=3)]
    paths = []
    for i, blob in enumerate(blobs):
        path = os.path.join(tmpdir, f"div_{i}.epub")
        with open(path, "wb") as f:
            f.write(blob)
        paths.append(path)
    return paths


def build_doc(tmpdir: str) -> dict:
    not_built = ("epub_f2_f3_not_built: a canonical package document (one element "
                 "order, one id scheme, one NCX head) is F2's specification, from "
                 "A2@F1's surviving keys; not built")
    cells = [_a1_cell(tmpdir),
             Cell("A1", "F2", V.NOT_TESTED, reason=not_built),
             Cell("A1", "F3", V.NOT_TESTED, reason=not_built),
             _a2_cell(tmpdir),
             Cell("A2", "F2", V.NOT_TESTED, reason=not_built),
             Cell("A2", "F3", V.NOT_TESTED, reason=not_built)]
    plugin = EpubPlugin()
    verdict, signatures = fingerprint_guard.evaluate(
        _scrubber(), plugin, _diverse(tmpdir), "F1", min_len=config.MIN_SIG_LEN)
    return matrix.assemble("epub", TOOL, cells,
                           matrix.fingerprint_block(verdict, signatures, []))


def main() -> str:
    doc = build_doc(tempfile.mkdtemp(prefix="epub_matrix_"))
    out_path = os.path.join(str(config.RESULTS_DIR), f"epub_{TOOL['name']}.json")
    matrix.write(doc, out_path)
    return out_path


if __name__ == "__main__":
    print(main())
