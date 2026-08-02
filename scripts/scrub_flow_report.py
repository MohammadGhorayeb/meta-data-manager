"""Before/after scrub-flow report — Markdown for the CI job summary.

Generates synthetic sample files with planted metadata (never real user photos),
then for each format x fidelity shows the flow: what the file looks like BEFORE
scrubbing vs AFTER, using ExifTool (ground truth) plus structural facts (embedded
thumbnails, trailer bytes, the encoder DQT fingerprint). Also demonstrates the A2
fingerprint defense: several producers of one image collapse to one DQT after F3.

Writes Markdown to stdout (CI redirects it into $GITHUB_STEP_SUMMARY) and saves
the scrubbed outputs under --out for artifact upload.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import warnings

warnings.filterwarnings("ignore")

# Allow `python scripts/scrub_flow_report.py` from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import PngImagePlugin

from src.scrub import cli
from src.scrub.formats.jpeg import segments as jseg
from src.scrub.formats.png import chunks as pck
from tests.scrub import corpus

_INTRINSIC = {
    "SourceFile", "ExifToolVersion", "FileName", "Directory", "FileSize",
    "FileModifyDate", "FileAccessDate", "FileInodeChangeDate", "FilePermissions",
    "FileType", "FileTypeExtension", "MIMEType", "ImageWidth", "ImageHeight",
    "EncodingProcess", "BitsPerSample", "ColorComponents", "YCbCrSubSampling",
    "ImageSize", "Megapixels", "JFIFVersion", "ResolutionUnit", "XResolution",
    "YResolution", "BitDepth", "ColorType", "Compression", "Filter", "Interlace",
    "ImageDataMD5",
}


def exiftool_tags(path: str) -> list[str]:
    try:
        p = subprocess.run(["exiftool", "-j", "-a", "-u", "-ee", path],
                           capture_output=True)
        rec = json.loads(p.stdout)[0]
    except Exception:
        return []
    return [k for k in rec if k not in _INTRINSIC]


def embedded_jpegs(data: bytes) -> int:
    return max(0, len(re.findall(b"\xff\xd8\xff", data)) - 1)


def dqt_sig(data: bytes) -> str:
    try:
        p = b"".join(s.payload for s in jseg.walk(data).by_kind("dqt"))
        return hashlib.sha1(p).hexdigest()[:10] if p else "-"
    except Exception:
        return "-"


def make_jpeg(path: str) -> None:
    with open(path, "wb") as f:
        f.write(corpus.build_torture_jpeg())


def make_png(path: str) -> None:
    meta = PngImagePlugin.PngInfo()
    meta.add_text("Author", "Jane Doe")
    meta.add_text("GPS", "33.88N, 35.50E")
    meta.add_text("Software", "SecretApp 1.0")
    corpus.make_pixels(80, 60).save(path, "PNG", pnginfo=meta, compress_level=9)


def flow_row(fmt: str, fid: str, src: str, out: str) -> str:
    cli.scrub_file(src, out, fid)
    b, a = open(src, "rb").read(), open(out, "rb").read()
    tb, ta = len(exiftool_tags(src)), len(exiftool_tags(out))
    size = f"{len(b):,} → {len(a):,} B"
    tags = f"{tb} → **{ta}**"
    if fmt == "jpeg":
        thumb = embedded_jpegs(a)
        trailer = len(jseg.walk(a).trailer)
        dqt = f"`{dqt_sig(b)}`→`{dqt_sig(a)}`"
        ok = ta == 0 and thumb == 0 and trailer == 0
        status = "✅ clean" if ok else "⚠️ leak"
        return (f"| {fid} | {tags} | {thumb} | {trailer} | {size} | {dqt} | {status} |")
    trailer = len(pck.walk(a).trailer)
    ok = ta == 0 and trailer == 0
    status = "✅ clean" if ok else "⚠️ leak"
    return f"| {fid} | {tags} | {trailer} | {size} | {status} |"


_HEADERS = {
    "jpeg": ("| Fidelity | Metadata tags | Embedded thumbnails | Trailer bytes "
             "| File size | Encoder DQT (raw→scrubbed) | Result |\n"
             "|---|---|---|---|---|---|---|"),
    "png": ("| Fidelity | Metadata tags | Trailer bytes | File size | Result |\n"
            "|---|---|---|---|---|"),
}


def format_section(fmt: str, mk, fids, outdir: str) -> str:
    src = os.path.join(outdir, f"sample.{fmt}")
    mk(src)
    before = exiftool_tags(src)
    highlights = sorted(t for t in before if any(
        k in t for k in ("GPS", "Make", "Model", "Author", "Software", "Comment")))
    lines = [f"### {fmt.upper()} sample",
             "",
             f"**Before scrub** — {len(before)} metadata tags incl. "
             f"{', '.join(highlights[:6]) or 'various'}",
             "",
             _HEADERS[fmt]]
    for fid in fids:
        out = os.path.join(outdir, f"scrubbed_{fmt}_{fid}.{fmt}")
        lines.append(flow_row(fmt, fid, src, out))
    lines.append("")
    return "\n".join(lines)


def a2_section(outdir: str) -> str:
    """Show the fingerprint defense: 3 producers of one image -> distinct DQT;
    after F3 they collapse to one."""
    base = corpus.make_pixels(96, 72, seed=3)
    producers = {}
    # three 'producers' = three encoders/qualities => distinct DQT
    for name, q in (("encoderA_q92", 92), ("encoderB_q75", 75), ("encoderC_q60", 60)):
        buf = io.BytesIO()
        base.save(buf, "JPEG", quality=q)
        producers[name] = buf.getvalue()
    rows = ["### A2 fingerprint defense (JPEG)",
            "",
            "Same image, three producers. F1/F2 keep each producer's DQT "
            "(traceable); F3 re-compresses so all share one DQT (untraceable).",
            "",
            "| Producer | DQT raw | DQT after F1 | DQT after F3 |",
            "|---|---|---|---|"]
    f3_sigs = set()
    for name, data in producers.items():
        src = os.path.join(outdir, f"{name}.jpg")
        open(src, "wb").write(data)
        f1o = os.path.join(outdir, f"{name}_f1.jpg")
        f3o = os.path.join(outdir, f"{name}_f3.jpg")
        cli.scrub_file(src, f1o, "F1")
        cli.scrub_file(src, f3o, "F3")
        s_raw = dqt_sig(data)
        s_f1 = dqt_sig(open(f1o, "rb").read())
        s_f3 = dqt_sig(open(f3o, "rb").read())
        f3_sigs.add(s_f3)
        rows.append(f"| {name} | `{s_raw}` | `{s_f1}` | `{s_f3}` |")
    verdict = ("✅ all producers share ONE DQT after F3 — fingerprint erased"
               if len(f3_sigs) == 1 else "⚠️ F3 DQTs differ")
    rows += ["", f"**{verdict}**", ""]
    return "\n".join(rows)


def build_body(outdir: str) -> str:
    """The before/after scrub-flow report as Markdown (reused by qa_report)."""
    os.makedirs(outdir, exist_ok=True)
    return "\n".join([
        "Synthetic samples with planted metadata (no real user data). Each is "
        "shown **before → after** scrubbing across fidelity tiers.",
        "",
        format_section("jpeg", make_jpeg, ("F1", "F2", "F3"), outdir),
        format_section("png", make_png, ("F1", "F2"), outdir),
        a2_section(outdir),
        "_F1 bit-preserving · F2 lossless re-encode · F3 lossy re-encode. "
        "A ✅ result means zero metadata tags, no embedded thumbnail, and no "
        "trailer bytes survive._",
    ])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="scrub_artifacts")
    args = ap.parse_args()
    print("# 🧼 Scrub flow report\n\n" + build_body(args.out))


if __name__ == "__main__":
    main()
