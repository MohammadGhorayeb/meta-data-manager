"""Generate the WebP Pareto matrix from real harness runs.

Measured here:
  - A1@F1   differential leak oracle over files whose planted metadata differs only
            in content (`webp_corpus.build`): EXIF, XMP, a personal ICC profile's
            header, an unknown chunk at the top level and inside an animation frame,
            and bytes after the RIFF's end -- still and animated. Content identity
            is every frame's decoded pixels.
  - A2@F1   E-WEBP: one picture through every lossless encoder setting here, pixels
            identical. F1 copies the bitstream, so whatever the encoder chose passes
            through; the cell names the keys.

Not measured, and said so: F2/F3 are not built (a canonical re-encode).

Run:  ./.venv/bin/python -m tests.scrub.gen_matrix_webp
"""
from __future__ import annotations

import os
import tempfile

from tests.harness import config
from tests.harness.contract import Cell, Leak, Locus, V
from tests.harness.oracle import fingerprint_guard, leak
from tests.harness.plugins.webp import WebpPlugin
from tests.harness.runner import matrix
from tests.scrub import e_webp
from tests.scrub import webp_corpus as wc

TOOL = {
    "name": "irreversible_scrubber",
    "version": "0.1.0-p6",
    "invocation": "python -m src.scrub {in} {out} --fidelity {fidelity}",
}


def _scrubber():
    from tests.scrub.test_harness_a1 import InProcessScrubber
    return InProcessScrubber()


def _variants(tmpdir: str, frames: int, n: int = 3, repeats: int = 3):
    groups = []
    for i in range(n):
        blob = wc.build(i + 1, frames=frames)
        paths = []
        for r in range(repeats):
            path = os.path.join(tmpdir, f"a1_{frames}_v{i}_r{r}.webp")
            with open(path, "wb") as f:
                f.write(blob)
            paths.append(path)
        groups.append(paths)
    return groups


def _a1_cell(tmpdir: str) -> Cell:
    scrubber, plugin = _scrubber(), WebpPlugin()
    failed, leaks = [], []
    for frames, label in ((1, "still"), (3, "animated")):
        cell = leak.evaluate_a1(scrubber, plugin, _variants(tmpdir, frames), "F1",
                                n=3, sentinel_field="metadata_variant",
                                modality="bytes")
        if cell.verdict != V.PASS:
            failed.append(label)
            leaks += cell.leaks
    if failed:
        return Cell("A1", "F1", V.FAIL, reason="variant-correlated bytes survive in "
                    + ", ".join(failed), leaks=leaks)
    return Cell("A1", "F1", V.PASS,
                reason="no metadata-correlated byte survives, still or animated "
                       "(EXIF, XMP, a personal ICC header, unknown chunks at the top "
                       "level and inside frames, bytes after the RIFF's end)")


def _a2_cell(tmpdir: str) -> Cell:
    sources = e_webp.build_sources(tmpdir)
    r = e_webp.run_condition("F1", sources, tmpdir)
    prods = ", ".join(r["producers"])
    survived = r["struct_fingerprints"]
    if not survived:
        return Cell("A2", "F1", V.PASS,
                    reason=f"no structural key separates the encoders ({prods})")
    leaks = [Leak("source_fingerprint", Locus("structural", feature_id=k),
                  "WebP encoder", f"{k} constant within an encoder, differs across")
             for k in survived]
    only_size = survived == ["struct:size"]
    return Cell("A2", "F1", V.FAIL, leaks=leaks,
                reason=("encoder still identifiable"
                        + (" only through FILE SIZE: the container is identical, and "
                           "the encoder shows only in how well it compressed -- the "
                           "bitstream F1 copies by definition"
                           if only_size else ": " + ", ".join(
                               k.removeprefix("struct:") for k in survived))
                        + f". Peer set = {prods} (lossless, pixels identical)."))


def _diverse(tmpdir: str) -> list[str]:
    blobs = [wc.build(1, seed=1), wc.build(2, frames=3, icc="standard", seed=2),
             wc.build(3, icc="none", unknown=False, trailing=False, seed=3)]
    paths = []
    for i, blob in enumerate(blobs):
        path = os.path.join(tmpdir, f"div_{i}.webp")
        with open(path, "wb") as f:
            f.write(blob)
        paths.append(path)
    return paths


def build_doc(tmpdir: str) -> dict:
    not_built = ("webp_f2_f3_not_built: a canonical re-encode (lossless for VP8L, "
                 "lossy for VP8) is not built")
    cells = [_a1_cell(tmpdir),
             Cell("A1", "F2", V.NOT_TESTED, reason=not_built),
             Cell("A1", "F3", V.NOT_TESTED, reason=not_built),
             _a2_cell(tmpdir),
             Cell("A2", "F2", V.NOT_TESTED, reason=not_built),
             Cell("A2", "F3", V.NOT_TESTED, reason=not_built)]
    plugin = WebpPlugin()
    verdict, signatures = fingerprint_guard.evaluate(
        _scrubber(), plugin, _diverse(tmpdir), "F1", min_len=config.MIN_SIG_LEN)
    return matrix.assemble("webp", TOOL, cells,
                           matrix.fingerprint_block(verdict, signatures, []))


def main() -> str:
    doc = build_doc(tempfile.mkdtemp(prefix="webp_matrix_"))
    out_path = os.path.join(str(config.RESULTS_DIR), f"webp_{TOOL['name']}.json")
    matrix.write(doc, out_path)
    return out_path


if __name__ == "__main__":
    print(main())
