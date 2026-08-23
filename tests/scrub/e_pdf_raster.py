"""E-PDF-RASTER — after rasterising, do the *pixels* still name the typesetter?

F3 renders every page through one renderer at one resolution, so every structural
trace of the original producer is gone: E-PDF at F3 shows the serializer channel
closed and the layout channel down to page geometry. The obvious conclusion — "F3
defeats A2" — is exactly the claim this project is not allowed to make without
measuring it, because **glyph geometry is not structure**. Where each glyph sits, how
far it advances and where the lines break are decisions the original typesetter made,
and rasterising paints them rather than removing them.

So the attack moves into pixel space. Given a rendered page and a peer corpus of
pages from candidate producers, can the producer still be identified?

**The corpus has to be several documents per producer.** With one document, "which
producer made this page" degenerates into "which of these five images have I seen
before", and a nearest-neighbour classifier scores 100% on memory. Every reference
page from the held-out sample's *own* document is therefore excluded, so the
classifier has to generalise across content — which is what an investigator with a
peer corpus actually has to do.

**Verdicts are significance tests, not thresholds.** Phase 2 settled this: an
accuracy compared against a fixed margin turned noise into a verdict once already and
disagreed between macOS and Linux. So the statistic is leave-one-document-out
nearest-neighbour accuracy, and the verdict is a one-sided binomial test against
chance (1/P for P producers).

**Controls first.** If the attack cannot identify the producer of an *unscrubbed*
page, its failure on a scrubbed one proves nothing at all.

**The DPI knob is the point** — and the measurement corrected the reasoning behind it.
W6 predicted the trade might exist because *sub-pixel* glyph position was assumed to
carry the signal, in which case rendering coarser would blur it away. It does not.
The ablation below shows the signal is the **column profile** — left margin, right
edge, the horizontal extent of the text block — which is coarse-scale layout geometry,
not sub-pixel detail, and downsampling leaves it entirely intact. The sweep therefore
runs down to resolutions where the body text is unreadable, because that is what it
takes to show the knob does not help.
"""
from __future__ import annotations

import hashlib
import math
import os
import subprocess
import tempfile

from src.scrub.formats.pdf import f3
from tests.scrub import pdf_corpus as pc

# The resolution the *attacker* renders at. Fixed across every condition so a change
# in the score is a change in the file, never a change in how we looked at it.
ANALYSIS_DPI = 150

# The render resolutions F3 is swept over. 150 is the shipped default and 300 is print
# quality; the low end goes all the way to 18 DPI, where the body text cannot be read
# at all. That is deliberate — a knob that only anonymises a document by destroying it
# is not a Pareto trade, and the only way to say so honestly is to measure past the
# point of usefulness rather than stopping at the first resolution that still works.
SWEEP_DPI = (18, 36, 72, 150, 300)

# What each resolution costs the reader, so the sweep table states the trade rather
# than leaving a DPI number to speak for itself.
LEGIBILITY = {18: "body text unreadable", 36: "barely legible",
              72: "legible on screen", 150: "legible in print",
              300: "print quality"}

# Profile length. Coarse enough that two producers setting the same page in the same
# font are not separated by a single antialiased pixel, fine enough to keep line
# spacing and left margin — which is the geometry actually under test.
_BINS = 128


# --------------------------------------------------------------------------- #
# Features
# --------------------------------------------------------------------------- #
def _render_png(path: str, dpi: int, workdir: str) -> str:
    stem = os.path.join(workdir, hashlib.sha1(path.encode()).hexdigest()[:12])
    subprocess.run(["pdftoppm", "-r", str(dpi), "-png", "-singlefile", path, stem],
                   check=True, capture_output=True)
    return stem + ".png"


def _resample(values: list[float], bins: int) -> list[float]:
    """Mean-pool a profile to a fixed length, so pages of different pixel heights are
    comparable. Pooling rather than sampling: a sampled row profile can miss a text
    line entirely and reports its absence as a producer difference."""
    if not values:
        return [0.0] * bins
    out = []
    for i in range(bins):
        lo = i * len(values) // bins
        hi = max(lo + 1, (i + 1) * len(values) // bins)
        window = values[lo:hi]
        out.append(sum(window) / len(window))
    return out


def ink_features(png_path: str) -> list[float]:
    """Where the ink is: a row profile, a column profile, and how much there is.

    The row profile is line spacing and baseline placement; the column profile is the
    left margin and the advance of the text block. Both are glyph geometry, and
    neither survives a re-typesetting — which is precisely why F3 cannot remove them.
    Each profile is normalised to its own maximum, so overall darkness (a renderer
    trait, and one we already hold constant) does not swamp the geometry.
    """
    from PIL import Image

    with Image.open(png_path) as im:
        grey = im.convert("L")
        width, height = grey.size
        pixels = list(grey.getdata())

    rows = [0.0] * height
    cols = [0.0] * width
    for y in range(height):
        base = y * width
        for x in range(width):
            ink = (255 - pixels[base + x]) / 255.0
            if ink:
                rows[y] += ink
                cols[x] += ink

    total = sum(rows)
    row_max = max(rows) or 1.0
    col_max = max(cols) or 1.0
    return (_resample([v / row_max for v in rows], _BINS)
            + _resample([v / col_max for v in cols], _BINS)
            + [total / (width * height)])


# --------------------------------------------------------------------------- #
# The attack
# --------------------------------------------------------------------------- #
def _distance(a: list[float], b: list[float]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b, strict=True)))


def classify(samples: list[tuple[str, int, list[float]]]) -> tuple[int, int]:
    """Leave-one-document-out nearest neighbour. Returns (correct, total).

    The held-out page's own document is removed from the reference set entirely — not
    just the page itself. Leaving the same document in under a different producer
    still lets the classifier match on content (the same paragraph has the same shape
    however it is set), and that is not a producer trait.
    """
    correct = 0
    for producer, doc, vector in samples:
        others = [(p, v) for p, d, v in samples if d != doc]
        if not others:
            continue
        nearest = min(others, key=lambda pv: _distance(vector, pv[1]))
        correct += nearest[0] == producer
    return correct, len(samples)


def binomial_p(correct: int, total: int, chance: float) -> float:
    """One-sided P(X >= correct) under the null that the attack is guessing."""
    if total == 0:
        return 1.0
    return sum(math.comb(total, k) * chance ** k * (1 - chance) ** (total - k)
               for k in range(correct, total + 1))


# --------------------------------------------------------------------------- #
# Conditions
# --------------------------------------------------------------------------- #
def _samples(paths: dict[str, list[str]], workdir: str) -> list[tuple]:
    out = []
    for producer, docs in sorted(paths.items()):
        for doc_index, path in enumerate(docs):
            png = _render_png(path, ANALYSIS_DPI, workdir)
            out.append((producer, doc_index, ink_features(png)))
    return out


def run_condition(paths: dict[str, list[str]], workdir: str,
                  dpi: int | None = None) -> dict:
    """`dpi=None` is the untouched control; any integer scrubs at F3 first."""
    if dpi is not None:
        scrubbed: dict[str, list[str]] = {}
        for producer, docs in paths.items():
            out_paths = []
            for i, src in enumerate(docs):
                with open(src, "rb") as f:
                    data = f.read()
                target = os.path.join(workdir, f"{producer}_d{i}_f3_{dpi}.pdf")
                with open(target, "wb") as f:
                    f.write(f3.scrub(data, dpi=dpi))
                out_paths.append(target)
            scrubbed[producer] = out_paths
        paths = scrubbed

    samples = _samples(paths, workdir)
    producers = sorted({p for p, _, _ in samples})
    chance = 1.0 / len(producers) if producers else 1.0
    correct, total = classify(samples)
    accuracy = correct / total if total else 0.0
    return {
        "dpi": dpi,
        "producers": producers,
        "n": total,
        "correct": correct,
        "accuracy": accuracy,
        "chance": chance,
        "p_value": binomial_p(correct, total, chance),
    }


def controls_valid(control: dict, alpha: float = 0.05) -> bool:
    """Does the attack identify the producer of an **untouched** page at all?

    If not, every scrubbed result below is unreadable — not a pass, not a fail, just
    an attack that was never shown to work. Same gate E-ENGINE applies.
    """
    return control["p_value"] < alpha and control["accuracy"] > control["chance"]


def evaluate_cell(control: dict, scrubbed: dict, alpha: float = 0.05):
    from tests.harness.contract import Cell, Leak, Locus, V

    peers = ", ".join(control["producers"])
    if not controls_valid(control):
        return Cell("A2", "F3", V.NOT_TESTED,
                    reason=f"controls invalid: the ink-geometry attack does not "
                           f"identify the producer of an unscrubbed page "
                           f"({control['accuracy']:.2f} vs chance "
                           f"{control['chance']:.2f}, p={control['p_value']:.3f}), so "
                           f"nothing can be concluded from the scrubbed pages. "
                           f"Peer set = {peers}")

    identified = scrubbed["p_value"] < alpha and scrubbed["accuracy"] > scrubbed["chance"]
    detail = (f"leave-one-document-out nearest neighbour on ink geometry: "
              f"unscrubbed {control['accuracy']:.2f} (p={control['p_value']:.3f}), "
              f"after F3 at {scrubbed['dpi']} DPI {scrubbed['accuracy']:.2f} "
              f"(p={scrubbed['p_value']:.3f}), chance {scrubbed['chance']:.2f}, "
              f"n={scrubbed['n']}. Peer set = {peers}")
    if identified:
        return Cell(
            "A2", "F3", V.FAIL,
            leaks=[Leak("source_fingerprint", Locus("pixel", feature_id="ink_geometry"),
                        f"producer (across {len(scrubbed['producers'])})",
                        "glyph positions, advances and line breaks survive "
                        "rasterisation into the rendered page")],
            reason=f"the typesetter is still identifiable from the pixels — {detail}")
    return Cell("A2", "F3", V.PASS,
                reason=f"the ink-geometry attack falls to chance after F3 — {detail}")


def ablate(samples: list[tuple]) -> dict[str, tuple[int, int]]:
    """Which half of the feature vector actually identifies the producer?

    A classifier at 100% proves the producer is identifiable; it does not prove *what
    from*. Without this, "glyph geometry survives rasterisation" would be an
    interpretation laid over a number that could equally have come from page size —
    LibreOffice sets A4 where the others set Letter, and that alone would separate it.

    The answer is that the **ink-density scalar sits at chance** while the column
    profile alone reaches 100%, so the attack is reading the geometry of the text
    block and not the shape of the paper. Restricting the peer set to the Letter-size
    producers, which holds page size constant outright, changes nothing.
    """
    b = _BINS
    slices = {
        "full vector": lambda v: v,
        "row profile (line spacing, baselines)": lambda v: v[:b],
        "column profile (margins, text extent)": lambda v: v[b:2 * b],
        "ink density alone": lambda v: v[2 * b:],
    }
    out = {}
    for label, take in slices.items():
        out[label] = classify([(p, d, take(v)) for p, d, v in samples])
    letter = [s for s in samples if s[0] != "libreoffice"]
    if letter:
        out["full vector, page size held constant"] = classify(letter)
    return out


def main() -> None:
    workdir = tempfile.mkdtemp(prefix="e_pdf_raster_")
    paths = pc.documents(workdir, n_docs=6)
    absent = [n for n, ok in pc.available_producers().items()
              if not ok or n not in paths]
    print(f"E-PDF-RASTER peer set: {sorted(paths)} x "
          f"{min((len(v) for v in paths.values()), default=0)} documents each")
    if absent:
        print(f"  not available here (reported as untested, never as clean): "
              f"{', '.join(absent)}")
    print(f"  attacker renders every condition at {ANALYSIS_DPI} DPI\n")

    control = run_condition(paths, workdir, dpi=None)
    print(f"{'condition':>16}  {'accuracy':>8}  {'chance':>7}  {'p':>7}   verdict")
    print(f"{'unscrubbed':>16}  {control['accuracy']:>8.2f}  "
          f"{control['chance']:>7.2f}  {control['p_value']:>7.4f}   "
          f"{'control VALID' if controls_valid(control) else 'control INVALID'}")
    if not controls_valid(control):
        print("\nThe attack cannot identify the producer even unscrubbed, so every "
              "row below would be unreadable. Stopping.")
        return

    for dpi in SWEEP_DPI:
        r = run_condition(paths, workdir, dpi=dpi)
        beat = r["p_value"] < 0.05 and r["accuracy"] > r["chance"]
        print(f"{'F3 @ ' + str(dpi) + ' DPI':>16}  {r['accuracy']:>8.2f}  "
              f"{r['chance']:>7.2f}  {r['p_value']:>7.4f}   "
              f"{'still identifiable' if beat else 'falls to chance':<19}"
              f"  {LEGIBILITY.get(dpi, '')}")

    print("\nWhat is the attack actually reading? (unscrubbed pages)")
    control_samples = _samples(paths, workdir)
    for label, (correct, total) in ablate(control_samples).items():
        share = correct / total if total else 0.0
        print(f"  {label:<42} {correct:>2}/{total} = {share:.2f}")


if __name__ == "__main__":
    main()
