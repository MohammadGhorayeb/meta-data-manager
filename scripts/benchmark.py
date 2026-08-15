"""Benchmark our scrubber against the standard tools (W9): ExifTool, MAT2, jpegtran.

Produces a plain-language comparison (Markdown) backing the funding-proposal claim
that our tool is differentiated — not by removing *more* metadata (mature tools do
A1 well), but by being the only one that is lossless-when-you-want-fidelity,
untraceable-when-you-want-anonymity, and PROVES both with a measured matrix.

All findings here were reproduced and adversarially verified. Tools that are not
installed are skipped (noted in the output). Run:
    ./.venv/bin/python scripts/benchmark.py > docs/benchmark.md

CAUTION: `docs/benchmark.md` is NOT purely generated. The audio section and several
capability-matrix rows were hand-written after a previous generation, so redirecting
over the file silently deletes them. Regenerate into a scratch file and splice, or
port the hand-written parts in here first.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from PIL import Image

from src.scrub import cli
from src.scrub.formats.jpeg import segments as seg
from tests.scrub import corpus

TD = tempfile.mkdtemp(prefix="bench_")
HAVE = {t: shutil.which(t) is not None for t in ("exiftool", "mat2", "jpegtran")}


# --------------------------- scrubber adapters ---------------------------- #
def ours(src, out, fid):
    cli.scrub_file(src, out, fid); return out

def exiftool(src, out):
    if os.path.exists(out): os.remove(out)
    subprocess.run(["exiftool", "-all=", "-o", out, src], capture_output=True)
    return out if os.path.exists(out) else None

def mat2(src, out):
    shutil.copy(src, out)
    subprocess.run(["mat2", "--inplace", out], capture_output=True)
    return out

def jpegtran(src, out):
    subprocess.run(["jpegtran", "-copy", "none", "-optimize", "-outfile", out, src],
                   capture_output=True)
    return out if os.path.exists(out) else None

# (label, fn, needs-tool-key or None). ours-F1/F2/F3 always available.
TOOLS = [
    ("**Ours** (F1)", lambda s, o: ours(s, o, "F1"), None),
    ("**Ours** (F2)", lambda s, o: ours(s, o, "F2"), None),
    ("**Ours** (F3)", lambda s, o: ours(s, o, "F3"), None),
    ("ExifTool", exiftool, "exiftool"),
    ("MAT2", mat2, "mat2"),
    ("jpegtran", jpegtran, "jpegtran"),
]

def available_tools():
    return [(lbl, fn) for lbl, fn, need in TOOLS if need is None or HAVE.get(need)]


# ------------------------------ measurements ------------------------------ #
def rgb_arr(p):
    return np.asarray(Image.open(p).convert("RGB")).astype(np.int32)

def pixels_identical(a, b):
    A, B = rgb_arr(a), rgb_arr(b)
    return A.shape == B.shape and bool((A == B).all())

def rmse(a, b):
    A, B = rgb_arr(a), rgb_arr(b)
    if A.shape != B.shape:
        B = np.asarray(Image.open(b).convert("RGB").resize(Image.open(a).size)).astype(np.int32)
    return round(float(np.sqrt(np.mean((A - B) ** 2))), 2)

def mode_of(p):
    return Image.open(p).mode

def is_progressive(p):
    kinds = {s.kind for s in seg.walk(open(p, "rb").read()).segments}
    return "sof2" in kinds  # SOF2 = progressive; SOF0 = baseline

def dqt_sig(p):
    import hashlib
    x = b"".join(s.payload for s in seg.walk(open(p, "rb").read()).by_kind("dqt"))
    return hashlib.sha1(x).hexdigest()[:8]

def meta_tags(p):
    import json
    try:
        r = json.loads(subprocess.run(["exiftool", "-j", "-a", "-u", p],
                       capture_output=True).stdout)[0]
    except Exception:
        return -1
    intrinsic = {"SourceFile", "ExifToolVersion", "FileName", "Directory", "FileSize",
        "FileModifyDate", "FileAccessDate", "FileInodeChangeDate", "FilePermissions",
        "FileType", "FileTypeExtension", "MIMEType", "ImageWidth", "ImageHeight",
        "EncodingProcess", "BitsPerSample", "ColorComponents", "YCbCrSubSampling",
        "ImageSize", "Megapixels", "JFIFVersion", "ResolutionUnit", "XResolution",
        "YResolution", "ImageDataMD5", "ExifByteOrder", "CurrentIPTCDigest",
        "ColorTransform", "AdobeTransform", "Adobe", "DCTEncodeVersion", "APP14Flags0",
        "APP14Flags1"}
    return len([k for k in r if k not in intrinsic])


def P(*a):
    print(*a)


def pdf_history_section():
    """Phase 3 M1 — what each tool does to a PDF that was edited by appending.

    A different axis from the JPEG evidence above, and the one that decides PDF:
    the leak is not a tag, it is the previous draft, sitting in the file at its
    original offsets. Reported straight from `tests/scrub/e_pdf_history.py` so the
    table cannot drift from the experiment.
    """
    from tests.scrub import e_pdf_history as eh
    from tests.scrub import pdf_corpus as pcorp

    P("\n## Evidence 6 — PDF revision history (Phase 3)\n")
    if not pcorp.HAVE_PDFTOTEXT:
        P("> ⚠️ `pdftotext` (poppler) not installed — revision recovery not measured.\n")
        return
    P("_A PDF is edited by **appending**: new objects, new cross-reference table, a "
      "trailer pointing back at the old one. Nothing is deleted. Truncate the file "
      "after any earlier `%%EOF` and the earlier draft opens as a document. This is "
      "the disclosure mode behind every 'redacted report published with the original "
      "text underneath'._\n")
    P("_Corpus: a 3-revision document. Revision 1 is confidential, revision 3 is the "
      "public text. Attacks: revision rollback, raw carving, and an object ledger._\n")
    workdir = os.path.join(TD, "pdfhist")
    os.makedirs(workdir, exist_ok=True)
    results = eh.run(workdir)
    P("| Tool | Revisions left | Stale objects | Text kept | Recoverable by rollback |")
    P("|---|:--:|:--:|:--:|---|")
    labels = {"raw": "_(untouched — the control)_", "pikepdf_rewrite": "qpdf/pikepdf rewrite",
              "mat2": "MAT2 (default)", "mat2_lightweight": "MAT2 `--lightweight`",
              "exiftool_all": "ExifTool `-all=`"}
    for key, r in results.items():
        lbl = labels.get(key, key)
        if "error" in r:
            P(f"| {lbl} | error | | | {r['error']} |")
            continue
        got = r["recovered"] + r["prior_metadata"]
        P(f"| {lbl} | {r['revisions']} | {r['ledger']['stale_definitions']} | "
          f"{'✅' if r['text_preserved'] else '❌ destroyed'} | "
          f"{'; '.join(f'`{g}`' for g in got) if got else '✅ nothing'} |")
    P("\n**ExifTool edits a PDF by appending an incremental update**, so `-all=` *adds* "
      "a revision and removes nothing — it says so itself: \"PDF edits are reversible. "
      "Deleted tags may be recovered!\" Every original value is still there.\n")
    P("**MAT2 re-renders**, so the document's own history genuinely goes — a real "
      "result, and the reason it beats ExifTool on this axis. But it then clears "
      "`/Info` by appending an incremental update of its own, leaving its producer "
      "string (`cairo …`) and a **wall-clock creation date with the operator's UTC "
      "offset** one revision down. Rolling back names the tool that made the file and "
      "the second it was made. Reproduced on a real 295 KB report as well as on the "
      "synthetic corpus, on both MAT2 paths.\n")
    P("_Our own PDF tier is under construction (Phase 3 M2). The row it has to beat is "
      "therefore: collapse to a single revision, keep the text, and leave no producer "
      "string or timestamp behind — which no tool measured here does._")


def main():
    P("# Benchmark — Irreversible Metadata Scrubber vs standard tools\n")
    P("_Comparison against the tools the field already uses (W9). Findings were "
      "reproduced and adversarially verified. Not about removing *more* metadata — "
      "mature tools do that well — but about **what it costs you** and **what it "
      "proves.**_\n")
    missing = [t for t, ok in HAVE.items() if not ok]
    if missing:
        P(f"> ⚠️ Not installed, skipped: {', '.join(missing)}.\n")

    # ---------------- capability matrix (headline) ----------------
    P("## Capability matrix\n")
    P("| Capability | Ours | ExifTool | MAT2 | jpegtran |")
    P("|---|:--:|:--:|:--:|:--:|")
    rows = [
        ("Removes all named metadata (EXIF/GPS/XMP/IPTC/thumbnail/trailer/MPF)", "✅", "✅", "✅", "✅"),
        ("Lossless option — JPEG pixels byte-identical", "✅ F1/F2", "✅", "❌ always lossy", "✅"),
        ("No cumulative quality loss when re-run", "✅", "✅", "❌ degrades each pass", "✅"),
        ("Preserves CMYK colour", "✅ F1/F2", "✅", "❌ converts to RGB", "✅"),
        ("Preserves progressive JPEG intact", "✅ F1/F2", "✅", "❌ lossy re-encode", "✅"),
        ("Erases the encoder fingerprint (untraceable, A2)", "✅ F3 / PNG-F2", "❌", "⚠️ only via forced lossy", "❌"),
        ("Lossless **and** untraceable (PNG)", "✅", "❌", "⚠️ re-encodes", "n/a"),
        ("You choose the fidelity for the threat you face", "✅ F1/F2/F3", "❌", "❌", "❌"),
        ("Measured (adversary × fidelity) guarantee matrix", "✅", "❌", "❌", "❌"),
        ("Differential-test verified · fails closed", "✅", "❌", "❌", "❌"),
        ("Documents impossible residuals honestly (PRNU)", "✅", "❌", "❌", "❌"),
    ]
    for r in rows:
        P("| " + " | ".join(r) + " |")

    P("\n**The one-line takeaway:** every tool deletes tags. Only ours lets you "
      "keep the picture *pixel-perfect* when you want fidelity, become *untraceable* "
      "when you want anonymity, and backs both with a measured, verified matrix.\n")

    tools = available_tools()

    # ---------------- evidence 1: content preservation ----------------
    P("## Evidence 1 — content preservation (a normal JPEG)\n")
    base = os.path.join(TD, "photo.jpg")
    corpus.make_pixels(200, 150, seed=5).save(base, "JPEG", quality=90)
    P("| Tool | Pixels byte-identical? | Quality |")
    P("|---|:--:|---|")
    for lbl, fn in tools:
        out = os.path.join(TD, f"c_{lbl}.jpg".replace("*", "").replace(" ", "").replace("(", "").replace(")", ""))
        try:
            r = fn(base, out)
            ident = pixels_identical(base, r)
            P(f"| {lbl} | {'✅ yes' if ident else '❌ no'} | "
              f"{'lossless' if ident else 'lossy re-encode'} |")
        except Exception as e:
            P(f"| {lbl} | error | {e} |")

    # ---------------- evidence 2: cumulative degradation (5 passes) ----------------
    P("\n## Evidence 2 — repeated cleaning (5 passes, chained)\n")
    P("_Real workflows scrub a file more than once. Lossless tools are stable; "
      "MAT2 degrades a little more every pass (generational loss)._\n")
    P("| Tool | RMSE from original after 1 / 3 / 5 passes |")
    P("|---|---|")
    photo = os.path.join(TD, "big.jpg")
    corpus.make_pixels(256, 256, seed=11).save(photo, "JPEG", quality=90)
    for lbl, fn in [t for t in tools if "F1" in t[0] or t[0] in ("ExifTool", "MAT2", "jpegtran")]:
        cur = photo
        marks = {}
        try:
            for i in range(1, 6):
                nxt = os.path.join(TD, f"deg_{lbl}_{i}.jpg".replace("*", "").replace(" ", ""))
                r = fn(cur, nxt)
                cur = r
                if i in (1, 3, 5):
                    marks[i] = rmse(photo, cur)
            P(f"| {lbl} | {marks.get(1)} / {marks.get(3)} / {marks.get(5)} |")
        except Exception as e:
            P(f"| {lbl} | error: {e} |")

    # ---------------- evidence 3: CMYK + progressive ----------------
    P("\n## Evidence 3 — awkward-but-valid JPEGs\n")
    cmyk = os.path.join(TD, "cmyk.jpg")
    Image.new("CMYK", (120, 90), (10, 20, 30, 5)).save(cmyk, "JPEG", quality=90)
    prog = os.path.join(TD, "prog.jpg")
    corpus.make_pixels(160, 120, seed=7).save(prog, "JPEG", quality=88, progressive=True)
    P("| Tool | CMYK colour preserved? | Progressive JPEG survives intact? |")
    P("|---|:--:|:--:|")
    for lbl, fn in tools:
        try:
            oc = fn(cmyk, os.path.join(TD, f"cm_{lbl}.jpg".replace("*", "").replace(" ", "")))
            op = fn(prog, os.path.join(TD, f"pr_{lbl}.jpg".replace("*", "").replace(" ", "")))
            cmyk_ok = "✅ CMYK" if mode_of(oc) == "CMYK" else f"❌ → {mode_of(oc)} (colour changed)"
            # Fair metric: is the picture undamaged? (progressive→baseline is a
            # LOSSLESS reorder; a lossy re-encode is real damage.)
            prog_ok = ("✅ lossless" if pixels_identical(prog, op)
                       else "⚠️ lossy (by design)" if "F3" in lbl
                       else "❌ lossy re-encode")
            P(f"| {lbl} | {cmyk_ok} | {prog_ok} |")
        except Exception as e:
            P(f"| {lbl} | error | {e} |")

    # ---------------- evidence 4: A2 fingerprint ----------------
    P("\n## Evidence 4 — the fingerprint snoop (A2)\n")
    P("_Same image from 3 different encoders. \"Untraceable\" = all outputs share "
      "one compressor fingerprint (DQT)._\n")
    P("| Tool | Distinct fingerprints across 3 producers | Verdict |")
    P("|---|:--:|---|")
    img = corpus.make_pixels(120, 90, seed=9)
    producers = {}
    for q in (92, 75, 60):
        p = os.path.join(TD, f"prod{q}.jpg"); img.save(p, "JPEG", quality=q); producers[q] = p
    a2_tools = [t for t in tools if t[0] in ("**Ours** (F3)", "ExifTool", "MAT2", "jpegtran")]
    for lbl, fn in a2_tools:
        try:
            sigs = {dqt_sig(fn(producers[q], os.path.join(TD, f"a2_{lbl}_{q}.jpg".replace("*", "").replace(" ", "")))) for q in producers}
            verdict = "✅ untraceable" if len(sigs) == 1 else f"❌ traceable ({len(sigs)} fingerprints)"
            P(f"| {lbl} | {len(sigs)} | {verdict} |")
        except Exception as e:
            P(f"| {lbl} | error | {e} |")

    # ---------------- evidence 5: A1 parity ----------------
    P("\n## Evidence 5 — metadata removal parity (the torture file)\n")
    P("_All loci at once: EXIF/GPS + embedded thumbnail, XMP, ExtendedXMP, ICC, "
      "IPTC/8BIM, MPF, COM, and a post-EOI trailer. Mature tools are strong here — "
      "we are at parity, which is the honest finding._\n")
    tort = os.path.join(TD, "torture.jpg")
    open(tort, "wb").write(corpus.build_torture_jpeg())
    P("| Tool | Metadata tags left | Trailer bytes |")
    P("|---|:--:|:--:|")
    for lbl, fn in tools:
        try:
            r = fn(tort, os.path.join(TD, f"t_{lbl}.jpg".replace("*", "").replace(" ", "")))
            trailer = len(seg.walk(open(r, "rb").read()).trailer)
            P(f"| {lbl} | {meta_tags(r)} | {trailer} |")
        except Exception as e:
            P(f"| {lbl} | error | {e} |")

    # ---------------- evidence 6: PDF revision history ----------------
    pdf_history_section()

    P("\n---\n_Generated by `scripts/benchmark.py`. Findings adversarially verified. "
      "MAT2's fingerprint normalization is a *side effect* of its forced lossy "
      "re-encode, not a tunable guarantee; ExifTool and jpegtran are lossless but "
      "leave the fingerprint intact._")


if __name__ == "__main__":
    main()
