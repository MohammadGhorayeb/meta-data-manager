"""PNG through the Phase-0 differential oracle: A1 (no metadata leak) at F1 and
F2, and A2 (producer fingerprint) fail-at-raw -> pass-at-F2 — the lossless A2
win, confirmed at the harness level via the same variance engine E3 uses.
"""
from __future__ import annotations

import io
import os

from PIL import Image, PngImagePlugin

from src.scrub import cli
from tests.harness.contract import V
from tests.harness.oracle import fields, leak, variance
from tests.harness.plugins.png import PngPlugin
from tests.scrub import corpus
from tests.scrub.test_harness_a1 import InProcessScrubber


def _fixed_img(seed=0):
    return corpus.make_pixels(64, 48, seed=seed)


def _png_a1_variants(tmpdir, n_variants=3, n_repeats=5):
    """Content-identical PNGs (same pixels + same encoder settings => identical
    IDAT) whose only difference is a variant-specific tEXt sentinel. A correct
    scrub collapses them to identical bytes -> A1 pass."""
    base = _fixed_img()
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
            with open(p, "wb") as f:
                f.write(variant)
            paths.append(p)
        groups.append(paths)
    return groups


def _png_peers(tmpdir, repeats=3):
    """Two producers (compress levels 9 and 1 => different deflate fingerprint)
    of ONE shared image. PNG's IDAT is content-dependent (unlike JPEG's DQT), so
    the peer set holds content constant and varies only the producer — the clean
    way to attribute a surviving difference to the encoder, not the scene."""
    img = _fixed_img(seed=0)
    sources = {"level9": [], "level1": []}
    for prod, lvl in (("level9", 9), ("level1", 1)):
        for r in range(repeats):
            p = os.path.join(tmpdir, f"{prod}__r{r}.png")
            img.save(p, "PNG", compress_level=lvl)
            sources[prod].append(p)
    return sources


def test_png_a1_no_leak_f1(tmp_path):
    variants = _png_a1_variants(str(tmp_path))
    cell = leak.evaluate_a1(InProcessScrubber(), PngPlugin(), variants, "F1",
                            n=5, sentinel_field="metadata_variant")
    assert cell.verdict == V.PASS, [l.correlates_with for l in cell.leaks]
    assert cell.floor is not None and cell.floor.deterministic


def test_png_a1_no_leak_f2(tmp_path):
    variants = _png_a1_variants(str(tmp_path))
    cell = leak.evaluate_a1(InProcessScrubber(), PngPlugin(), variants, "F2",
                            n=5, sentinel_field="metadata_variant")
    assert cell.verdict == V.PASS, [l.correlates_with for l in cell.leaks]
    assert cell.floor is not None and cell.floor.deterministic


def _idat_leaks(sources, fidelity, tmpdir):
    """True iff the IDAT deflate fingerprint separates producers at `fidelity`
    ('raw' = no scrub)."""
    plugin = PngPlugin()
    producers = list(sources)
    repeats = list(range(min(len(v) for v in sources.values())))
    cells = {}
    for prod, paths in sources.items():
        for r, src in enumerate(paths):
            if fidelity == "raw":
                target = src
            else:
                target = os.path.join(tmpdir, f"{prod}_{r}_{fidelity}.png")
                cli.scrub_file(src, target, fidelity)
            cells[(prod, r)] = fields.extract(target, plugin, "F3")
    verdicts = variance.decompose(cells, producers, repeats)
    return verdicts["struct:idat"].leak


def test_png_a2_fingerprint_present_raw(tmp_path):
    sources = _png_peers(str(tmp_path))
    assert _idat_leaks(sources, "raw", str(tmp_path)), \
        "raw producers must be separable by deflate fingerprint"


def test_png_a2_fingerprint_erased_by_f2(tmp_path):
    """The headline: F2 normalizes the deflate fingerprint -> producers no longer
    separable -> A2@F2 pass, and losslessly."""
    sources = _png_peers(str(tmp_path))
    assert not _idat_leaks(sources, "F2", str(tmp_path)), \
        "F2 must erase the deflate fingerprint (A2 pass)"
