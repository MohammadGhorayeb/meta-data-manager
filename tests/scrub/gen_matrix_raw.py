"""Generate the camera-RAW Pareto matrix from real harness runs.

Honesty rule (CLAUDE.md): a cell is only pass/fail if we measured it.

Measured here:
  - A1@F1   differential leak oracle over hand-built DNGs (`raw_corpus.build`) whose
            planted metadata differs only in CONTENT, length kept: identity, dates,
            GPS, software, XMP, the preview's own EXIF, the maker-note dates and the
            DNG semantic mask all have to collapse. Run once per maker-note layout,
            because each maker hides its serials somewhere else. Content identity is
            decoded (LibRaw sensor data + camera-white-balance render), not parsed.

Not measured, and said so rather than left blank:
  - A2@F1   make, model and lens model are KEPT on purpose (the decoder picks the
            camera's colour profile by them), so the model survives by design. The
            question worth asking -- which camera BODY -- needs several files from
            two bodies of one model, which the corpus does not have (limit #47).
  - F2/F3   not built: there is no lossless re-encode of a sensor mosaic every raw
            decoder still reads, and a lossy one stops being a raw.

Run:  ./.venv/bin/python -m tests.scrub.gen_matrix_raw
"""
from __future__ import annotations

import os
import tempfile

from tests.harness import config
from tests.harness.contract import Cell, V
from tests.harness.oracle import fingerprint_guard, leak
from tests.harness.plugins.raw import RawPlugin
from tests.harness.runner import matrix
from tests.scrub import raw_corpus as rc

TOOL = {
    "name": "irreversible_scrubber",
    "version": "0.1.0-p4",
    "invocation": "python -m src.scrub {in} {out} --fidelity {fidelity}",
}
LAYOUTS = ("canon", "nikon", "olympus", "apple", "sony")


def _scrubber():
    from tests.scrub.test_harness_a1 import InProcessScrubber
    return InProcessScrubber()


def _a1_variants(tmpdir: str, note: str, n_variants: int = 3, n_repeats: int = 3):
    groups = []
    for i in range(n_variants):
        blob = rc.build(note, variant=i)
        paths = []
        for r in range(n_repeats):
            path = os.path.join(tmpdir, f"a1_{note}_v{i}_r{r}.dng")
            with open(path, "wb") as f:
                f.write(blob)
            paths.append(path)
        groups.append(paths)
    return groups


def _a1_cell(tmpdir: str) -> Cell:
    """A1@F1 across every maker-note layout: pass only if every layout passes."""
    scrubber, plugin = _scrubber(), RawPlugin()
    failed, leaks = [], []
    for note in LAYOUTS:
        cell = leak.evaluate_a1(scrubber, plugin, _a1_variants(tmpdir, note), "F1",
                                n=3, sentinel_field="metadata_variant",
                                modality="bytes")
        if cell.verdict != V.PASS:
            failed.append(note)
            leaks += cell.leaks
    if failed:
        return Cell("A1", "F1", V.FAIL, reason="variant-correlated bytes survive in "
                    + ", ".join(failed), leaks=leaks)
    return Cell("A1", "F1", V.PASS,
                reason="no metadata-correlated byte survives in any of the five "
                       "maker-note layouts (" + ", ".join(LAYOUTS) + ")")


def _diverse(tmpdir: str) -> list[str]:
    """Inputs sharing as little as possible, so a run common to every OUTPUT is
    ours: different makers, both byte orders, and the Panasonic layout."""
    shapes = [("canon", "<", 42, True), ("nikon", ">", 42, True),
              ("olympus", ">", 42, True), ("panasonic", "<", 0x0055, False),
              ("olympus", "<", 0x4F52, True)]
    paths = []
    for i, (note, order, magic, dng) in enumerate(shapes):
        path = os.path.join(tmpdir, f"div_{i}.raw")
        with open(path, "wb") as f:
            f.write(rc.build(note, order, magic=magic, dng=dng, seed=i + 1))
        paths.append(path)
    return paths


def build_doc(tmpdir: str) -> dict:
    a2_reason = ("raw_a2_not_measured: make, model and lens model are kept on "
                 "purpose -- the decoder picks the colour profile by them -- so the "
                 "model survives by design; whether a cleaned file still points to "
                 "one camera BODY needs several files from two bodies of one model, "
                 "which the corpus does not have (limit #47)")
    not_built = ("raw_f2_f3_not_built: no lossless re-encode of a sensor mosaic that "
                 "every raw decoder still reads exists, and a lossy one stops being "
                 "a raw")
    cells = [
        _a1_cell(tmpdir),
        Cell("A1", "F2", V.NOT_TESTED, reason=not_built),
        Cell("A1", "F3", V.NOT_TESTED, reason=not_built),
        Cell("A2", "F1", V.NOT_TESTED, reason=a2_reason),
        Cell("A2", "F2", V.NOT_TESTED, reason=not_built),
        Cell("A2", "F3", V.NOT_TESTED, reason=not_built),
    ]
    plugin = RawPlugin()
    guard_verdict, signatures = fingerprint_guard.evaluate(
        _scrubber(), plugin, _diverse(tmpdir), "F1", min_len=config.MIN_SIG_LEN)
    excluded = [{"bytes_hex": c.hex(), "decoded": c.decode("latin-1", "replace")}
                for c in plugin.mandatory_constants()]
    fp = matrix.fingerprint_block(guard_verdict, signatures, excluded)
    return matrix.assemble("raw", TOOL, cells, fp)


def main() -> str:
    doc = build_doc(tempfile.mkdtemp(prefix="raw_matrix_"))
    out_path = os.path.join(str(config.RESULTS_DIR), f"raw_{TOOL['name']}.json")
    matrix.write(doc, out_path)
    return out_path


if __name__ == "__main__":
    print(main())
