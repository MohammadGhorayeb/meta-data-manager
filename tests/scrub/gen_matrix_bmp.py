"""Generate the BMP Pareto matrix from real harness runs.

Measured here:
  - A1@F1   differential leak oracle over files whose planted metadata differs only
            in content (`bmp_corpus.build`): the reserved fields, the resolution,
            the important-colours count, the palette's reserved bytes, a gap before
            the pixels, row padding, bytes after the picture, an unused palette, the
            colour-space fields an sRGB picture ignores, V5's reserved field, a
            personal ICC profile, a LINKED profile's path, RLE pad bytes and slack
            -- across seven layouts. A do-nothing scrubber is run through the same
            check first and must FAIL it, or the cell is not claimed.
  - A2@F1   E-BMP: one picture through every BMP writer here, decoded colours
            identical. F1 keeps the header version and the row order, so the cell
            names which keys still separate the writers.

Not measured, and said so: F2/F3 are not built (a canonical header and row order).

Run:  ./.venv/bin/python -m tests.scrub.gen_matrix_bmp
"""
from __future__ import annotations

import os
import shutil
import tempfile

from tests.harness import config
from tests.harness.contract import Cell, Leak, Locus, ScrubResult, V
from tests.harness.oracle import fingerprint_guard, leak
from tests.harness.plugins.bmp import BmpPlugin
from tests.harness.runner import matrix
from tests.scrub import bmp_corpus as bc
from tests.scrub import e_bmp

TOOL = {
    "name": "irreversible_scrubber",
    "version": "0.1.0-p6",
    "invocation": "python -m src.scrub {in} {out} --fidelity {fidelity}",
}

# label -> bmp_corpus.build() layout. Every planted locus appears in at least one.
LAYOUTS = {
    "V5 with a personal profile": dict(header="v5", bpp=24, icc="custom"),
    "V5 with a linked profile": dict(header="v5", bpp=32, compression="bitfields",
                                     icc="linked"),
    "V5 with a published profile before the pixels": dict(
        header="v5", bpp=8, icc="standard", profile_at="gap"),
    "V4 calibrated": dict(header="v4", bpp=24, icc="calibrated"),
    "8-bit palette": dict(header="info", bpp=8, colors=16),
    "RLE8": dict(header="info", bpp=8, compression="rle8"),
    "core header": dict(header="core", bpp=24),
}


def _scrubber():
    from tests.scrub.test_harness_a1 import InProcessScrubber
    return InProcessScrubber()


class DoNothingScrubber:
    """The control: writes its input back unchanged. A1 must fail it."""
    name, version = "do_nothing", "0"

    def run(self, in_path: str, out_path: str, fidelity: str) -> ScrubResult:
        shutil.copyfile(in_path, out_path)
        return ScrubResult(ok=True, out_path=out_path, fidelity=fidelity)


def _variants(tmpdir: str, label: str, layout: dict, n: int = 3, repeats: int = 3):
    groups = []
    for i in range(n):
        blob = bc.build(i + 1, **layout)
        paths = []
        for r in range(repeats):
            path = os.path.join(tmpdir, f"a1_{list(LAYOUTS).index(label)}_v{i}_r{r}.bmp")
            with open(path, "wb") as f:
                f.write(blob)
            paths.append(path)
        groups.append(paths)
    return groups


def _a1(scrubber, tmpdir: str, labels) -> tuple[list[str], list]:
    failed, leaks = [], []
    for label in labels:
        cell = leak.evaluate_a1(scrubber, BmpPlugin(),
                                _variants(tmpdir, label, LAYOUTS[label]), "F1", n=3,
                                sentinel_field="metadata_variant", modality="bytes")
        if cell.verdict != V.PASS:
            failed.append(label)
            leaks += cell.leaks
    return failed, leaks


def control_fails(tmpdir: str) -> bool:
    """Run the do-nothing scrubber through A1 on every layout: each must fail."""
    failed, _ = _a1(DoNothingScrubber(), tmpdir, LAYOUTS)
    return len(failed) == len(LAYOUTS)


def _a1_cell(tmpdir: str) -> Cell:
    if not control_fails(tmpdir):
        return Cell("A1", "F1", V.NOT_TESTED,
                    reason="the do-nothing control passed the check too, so the check "
                           "could not tell a scrubber from none")
    failed, leaks = _a1(_scrubber(), tmpdir, LAYOUTS)
    if failed:
        return Cell("A1", "F1", V.FAIL, reason="variant-correlated bytes survive in "
                    + ", ".join(failed), leaks=leaks)
    return Cell("A1", "F1", V.PASS,
                reason="no metadata-correlated byte survives in "
                       f"{len(LAYOUTS)} layouts ({', '.join(LAYOUTS)}): reserved "
                       "fields, resolution, important colours, palette reserved bytes, "
                       "a gap, row padding, trailing bytes, an unused palette, unused "
                       "colour-space fields, a personal ICC profile, a linked "
                       "profile's path, RLE pad bytes and slack. A do-nothing "
                       "scrubber fails the same check in every layout (control)")


def _a2_cell(tmpdir: str) -> Cell:
    sources = e_bmp.build_sources(tmpdir)
    r = e_bmp.run_condition("F1", sources, tmpdir)
    prods = ", ".join(r["producers"])
    survived = r["struct_fingerprints"]
    if len(r["producers"]) < 2:
        return Cell("A2", "F1", V.NOT_TESTED,
                    reason=f"only one BMP writer here ({prods}): no peer set")
    if not survived:
        return Cell("A2", "F1", V.PASS,
                    reason=f"no structural key separates the writers ({prods})")
    leaks = [Leak("source_fingerprint", Locus("structural", feature_id=k),
                  "BMP writer", f"{k} constant within a writer, differs across")
             for k in survived]
    return Cell("A2", "F1", V.FAIL, leaks=leaks,
                reason=("writer still identifiable: " + ", ".join(
                            k.removeprefix("struct:") for k in survived)
                        + f". Peer set = {prods} (decoded colours identical)."))


def _diverse(tmpdir: str) -> list[str]:
    blobs = [bc.build(1, header="v5", icc="custom", seed=1),
             bc.build(2, header="info", bpp=8, compression="rle8", seed=2),
             bc.build(3, header="core", bpp=4, seed=3),
             bc.build(4, header="v4", bpp=16, compression="bitfields", icc="none",
                      planted=False, seed=4, w=17, h=9)]
    paths = []
    for i, blob in enumerate(blobs):
        path = os.path.join(tmpdir, f"div_{i}.bmp")
        with open(path, "wb") as f:
            f.write(blob)
        paths.append(path)
    return paths


def build_doc(tmpdir: str) -> dict:
    not_built = ("bmp_f2_f3_not_built: a canonical bitmap (one header version, rows "
                 "bottom-up) is not built")
    cells = [_a1_cell(tmpdir),
             Cell("A1", "F2", V.NOT_TESTED, reason=not_built),
             Cell("A1", "F3", V.NOT_TESTED, reason=not_built),
             _a2_cell(tmpdir),
             Cell("A2", "F2", V.NOT_TESTED, reason=not_built),
             Cell("A2", "F3", V.NOT_TESTED, reason=not_built)]
    verdict, signatures = fingerprint_guard.evaluate(
        _scrubber(), BmpPlugin(), _diverse(tmpdir), "F1", min_len=config.MIN_SIG_LEN)
    return matrix.assemble("bmp", TOOL, cells,
                           matrix.fingerprint_block(verdict, signatures, []))


def main() -> str:
    doc = build_doc(tempfile.mkdtemp(prefix="bmp_matrix_"))
    out_path = os.path.join(str(config.RESULTS_DIR), f"bmp_{TOOL['name']}.json")
    matrix.write(doc, out_path)
    return out_path


if __name__ == "__main__":
    print(main())
