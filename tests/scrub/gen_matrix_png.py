"""Generate the PNG Pareto matrix for our scrubber from real harness runs.

Honesty rule (CLAUDE.md): a cell is only pass/fail if we measured it.

Measured here:
  - A1@F1, A1@F2  differential leak oracle over content-identical/metadata-variant
                  PNGs (PngPlugin).
  - A2@F1, A2@F2  deflate-fingerprint peer set: several producers (zlib compress
                  levels) of one image; F1 keeps each producer's IDAT (fail), F2
                  normalizes it to one canonical deflate (pass) — losslessly.
  - A2@F3, A1@F3  not_applicable: PNG is lossless, so F3 adds nothing over F2.

Run:  ./.venv/bin/python -m tests.scrub.gen_matrix_png
Writes tests/harness/results/png_irreversible_scrubber.json (schema-validated).
"""
from __future__ import annotations

import io
import os
import tempfile

from PIL import Image, PngImagePlugin

from tests.harness import config
from tests.harness.contract import Cell, Leak, Locus, V
from tests.harness.oracle import fields, fingerprint_guard, leak, variance
from tests.harness.plugins.png import PngPlugin
from tests.harness.runner import matrix
from tests.scrub import corpus

TOOL = {
    "name": "irreversible_scrubber",
    "version": "0.1.0-p1",
    "invocation": "python -m src.scrub {in} {out} --fidelity {fidelity}",
}


def _scrubber():
    from tests.scrub.test_harness_a1 import InProcessScrubber
    return InProcessScrubber()


def _png_a1_variants(tmpdir, n_variants=3, n_repeats=5):
    """Same pixels + same encoder settings (identical IDAT), differing only by a
    variant-specific tEXt sentinel -> a correct scrub collapses them."""
    base = corpus.make_pixels(64, 48)
    groups = []
    for i in range(n_variants):
        meta = PngImagePlugin.PngInfo()
        meta.add_text("Author", "SENTINEL-" + chr(65 + i) * 8)
        meta.add_text("GPS", f"{i}.0N")
        buf = io.BytesIO()
        base.save(buf, "PNG", pnginfo=meta, compress_level=9)
        variant = buf.getvalue()
        paths = []
        for r in range(n_repeats):
            p = os.path.join(tmpdir, f"png_a1_v{i}_r{r}.png")
            open(p, "wb").write(variant)
            paths.append(p)
        groups.append(paths)
    return groups


def _png_peers(tmpdir, repeats=3):
    """Producers = zlib compress levels (distinct deflate fingerprint) of ONE
    shared image; PNG's IDAT is content-dependent, so content is held constant."""
    img = corpus.make_pixels(64, 48, seed=2)
    sources = {}
    for lvl in (1, 6, 9):
        paths = []
        for r in range(repeats):
            p = os.path.join(tmpdir, f"level{lvl}__r{r}.png")
            img.save(p, "PNG", compress_level=lvl)
            paths.append(p)
        sources[f"zlib_L{lvl}"] = paths
    return sources


def _a2_cell(fidelity: str, sources, tmpdir) -> Cell:
    """Deflate-fingerprint A2: fail if the IDAT still separates producers."""
    plugin = PngPlugin()
    from src.scrub import cli
    producers = list(sources)
    repeats = list(range(min(len(v) for v in sources.values())))
    cells = {}
    for prod, paths in sources.items():
        for r, src in enumerate(paths):
            out = os.path.join(tmpdir, f"{prod}_{r}_{fidelity}.png")
            cli.scrub_file(src, out, fidelity)
            cells[(prod, r)] = fields.extract(out, plugin, "F3")
    verdicts = variance.decompose(cells, producers, repeats)
    fps = {fid: v for fid, v in verdicts.items()
           if v.leak and fid.startswith("struct:")}
    if fps:
        leaks = [Leak("source_fingerprint", Locus("structural", feature_id=fid),
                      f"producer (across {v.n_between})",
                      f"{fid} constant within producer, differs across")
                 for fid, v in fps.items()]
        return Cell("A2", fidelity, V.FAIL, leaks=leaks,
                    reason="deflate_fingerprint_survives: " + ", ".join(sorted(fps)))
    return Cell("A2", fidelity, V.PASS,
                reason="deflate fingerprint normalized losslessly (E5) — no "
                       "producer separation; PNG residuals none beyond content")


def build_doc(tmpdir: str) -> dict:
    plugin = PngPlugin()
    scrubber = _scrubber()

    variants1 = _png_a1_variants(tmpdir)
    a1f1 = leak.evaluate_a1(scrubber, plugin, variants1, "F1", n=5,
                            sentinel_field="metadata_variant")
    variants2 = _png_a1_variants(tmpdir)
    a1f2 = leak.evaluate_a1(scrubber, plugin, variants2, "F2", n=5,
                            sentinel_field="metadata_variant")

    peers = _png_peers(tmpdir)
    a2f1 = _a2_cell("F1", peers, tmpdir)
    a2f2 = _a2_cell("F2", peers, tmpdir)

    cells = [
        a1f1, a1f2,
        Cell("A1", "F3", V.NOT_APPLICABLE, reason="lossless_format_f3_adds_nothing"),
        a2f1, a2f2,
        Cell("A2", "F3", V.NOT_APPLICABLE, reason="lossless_format_f3_adds_nothing"),
    ]

    diverse = _png_diverse(tmpdir, n=6)
    gv, gsig = fingerprint_guard.evaluate(scrubber, plugin, diverse, "F2",
                                          min_len=config.MIN_SIG_LEN)
    excluded = [{"bytes_hex": c.hex(), "decoded": c.decode("latin-1", "replace")}
                for c in plugin.mandatory_constants()]
    fp = matrix.fingerprint_block(gv, gsig, excluded)
    return matrix.assemble("png", TOOL, cells, fp)


def _png_diverse(tmpdir, n=6):
    paths = []
    kinds = ("noise", "rings", "weave", "grad")
    for i in range(n):
        img = corpus._textured_pixels(96 + i * 8, 72 + i * 4, kinds[i % 4], seed=i + 1)
        meta = PngImagePlugin.PngInfo()
        meta.add_text("Author", f"person-{i}")
        meta.add_text("Comment", f"diverse-{i}")
        p = os.path.join(tmpdir, f"png_diverse_{i}.png")
        img.save(p, "PNG", pnginfo=meta, compress_level=(6 + i % 4))
        paths.append(p)
    return paths


def main() -> str:
    tmpdir = tempfile.mkdtemp(prefix="png_matrix_")
    doc = build_doc(tmpdir)
    out_path = os.path.join(str(config.RESULTS_DIR), f"png_{TOOL['name']}.json")
    matrix.write(doc, out_path)
    return out_path


if __name__ == "__main__":
    print(main())
