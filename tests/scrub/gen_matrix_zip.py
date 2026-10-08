"""Generate the ZIP Pareto matrix from real harness runs.

Measured here:
  - A1@F1   differential leak oracle over archives whose planted metadata differs
            only in content (`zip_corpus.build`), in four producer shapes: archive
            and entry comments, DOS times, Unix times and uid/gid, NTFS times,
            umask-revealing modes, Finder's `__MACOSX` sidecars (download URL,
            quarantine), `.DS_Store`, `Thumbs.db`, and members with metadata of
            their own (photo EXIF/GPS, PNG text, SVG comment, a nested archive).
  - A2@F1   E-ZIP: one folder through every archiver here, content identical. The
            channel is the container; the cell names the producers.

Not measured, and said so: F2/F3 are not built (an archive's F2/F3 would be its
members' own F2/F3 tiers, applied inside it).

Run:  ./.venv/bin/python -m tests.scrub.gen_matrix_zip
"""
from __future__ import annotations

import os
import tempfile

from tests.harness import config
from tests.harness.contract import Cell, Leak, Locus, V
from tests.harness.oracle import fingerprint_guard, leak
from tests.harness.plugins.zip import ZipPlugin
from tests.harness.runner import matrix
from tests.scrub import e_zip
from tests.scrub import zip_corpus as zc

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
        blob = zc.build(i + 1, shape=shape)
        paths = []
        for r in range(repeats):
            path = os.path.join(tmpdir, f"a1_{shape}_v{i}_r{r}.zip")
            with open(path, "wb") as f:
                f.write(blob)
            paths.append(path)
        groups.append(paths)
    return groups


def _a1_cell(tmpdir: str) -> Cell:
    scrubber, plugin = _scrubber(), ZipPlugin()
    failed, leaks = [], []
    for shape in zc.SHAPES:
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
                reason="no metadata-correlated byte survives in any of "
                       f"{len(zc.SHAPES)} producer shapes ({', '.join(zc.SHAPES)}): "
                       "comments, entry times, Unix and NTFS times, uid/gid, modes, "
                       "__MACOSX sidecars (download URL, quarantine), .DS_Store, "
                       "Thumbs.db, and members' own metadata to a nested archive")


def _a2_cell(tmpdir: str) -> Cell:
    sources = e_zip.build_sources(tmpdir)
    r = e_zip.run_condition("F1", sources, tmpdir)
    prods = ", ".join(r["producers"])
    survived = r["struct_fingerprints"]
    if not survived:
        return Cell("A2", "F1", V.PASS,
                    reason=f"no container key separates the archivers ({prods}); the "
                           "members are files of their own formats, judged in their "
                           "own matrices")
    leaks = [Leak("source_fingerprint", Locus("structural", feature_id=k),
                  "ZIP archiver", f"{k} constant within an archiver, differs across")
             for k in survived]
    return Cell("A2", "F1", V.FAIL, leaks=leaks,
                reason=("archiver still identifiable: " + ", ".join(
                            k.removeprefix("struct:") for k in survived)
                        + f". Peer set = {prods} (content identical)."))


def _diverse(tmpdir: str) -> list[str]:
    blobs = [zc.build(1, shape="infozip", seed=1), zc.build(2, shape="ditto", seed=2),
             zc.build(3, shape="windows", seed=3)]
    paths = []
    for i, blob in enumerate(blobs):
        path = os.path.join(tmpdir, f"div_{i}.zip")
        with open(path, "wb") as f:
            f.write(blob)
        paths.append(path)
    return paths


def build_doc(tmpdir: str) -> dict:
    not_built = ("zip_f2_f3_not_built: an archive's F2/F3 would apply each member's "
                 "own F2/F3 inside it; not built")
    cells = [_a1_cell(tmpdir),
             Cell("A1", "F2", V.NOT_TESTED, reason=not_built),
             Cell("A1", "F3", V.NOT_TESTED, reason=not_built),
             _a2_cell(tmpdir),
             Cell("A2", "F2", V.NOT_TESTED, reason=not_built),
             Cell("A2", "F3", V.NOT_TESTED, reason=not_built)]
    plugin = ZipPlugin()
    verdict, signatures = fingerprint_guard.evaluate(
        _scrubber(), plugin, _diverse(tmpdir), "F1", min_len=config.MIN_SIG_LEN)
    return matrix.assemble("zip", TOOL, cells,
                           matrix.fingerprint_block(verdict, signatures, []))


def main() -> str:
    doc = build_doc(tempfile.mkdtemp(prefix="zip_matrix_"))
    out_path = os.path.join(str(config.RESULTS_DIR), f"zip_{TOOL['name']}.json")
    matrix.write(doc, out_path)
    return out_path


if __name__ == "__main__":
    print(main())
