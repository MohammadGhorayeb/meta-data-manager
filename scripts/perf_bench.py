"""Speed and memory, measured (November plan N3a).

Nothing about speed was measured before November, and memory only for video and
M4A (limit #38). This builds realistic files of each type at a ladder of sizes,
cleans each at every level the type offers, and measures every clean in a FRESH
process -- peak memory is a high-water mark, so a long-lived process would carry
one file's peak into the next:

- wall time of the clean (dispatch, scrub, residual check, write, report), with the
  ExifTool cross-check timed on its own, since it is a fixed cost the user can skip
  (`--no-verify`) and on small files it is most of the wait;
- our own peak memory above the interpreter's baseline (Linux: VmHWM, because
  `ru_maxrss` there inherits the parent's peak across fork+exec -- see
  test_memory_preflight.py), and the peak of any external program it ran.

Sizes are the file's size on disk. The lossy and LibreOffice levels stop at a
smaller size by default (`--heavy-max`); the table says what was not run.

Usage:  ./.venv/bin/python scripts/perf_bench.py [--sizes 1 10 100] [--big 1000]
            [--heavy-max 10] [--only jpeg mp4 ...] [--json out.json] [--markdown out.md]
"""
from __future__ import annotations

import argparse
import io
import json
import os
import platform
import random
import subprocess
import sys
import tempfile
import time
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
MB = 1_000_000

# Levels each type offers, and which are "heavy" (another program re-encodes or
# re-typesets the whole file) -- those stop at --heavy-max.
TIERS = {"jpeg": ("F1", "F2", "F3"), "png": ("F1", "F2"), "tiff": ("F1",),
         "mp3": ("F1", "F3"), "flac": ("F1", "F2"), "m4a": ("F1", "F2", "F3"),
         "mp4": ("F1",), "pdf": ("F1", "F2", "F3"), "docx": ("F1", "F2", "F3"),
         "xlsx": ("F1",), "pptx": ("F1",), "zip": ("F1",)}
HEAVY = {("jpeg", "F3"), ("mp3", "F3"), ("flac", "F2"), ("m4a", "F2"), ("m4a", "F3"),
         ("pdf", "F2"), ("pdf", "F3"), ("docx", "F2"), ("docx", "F3")}
# Types that reach gigabytes in real use: they also get --big.
BIG = {"mp4", "zip"}
EXT = {"jpeg": ".jpg", "png": ".png", "tiff": ".tif", "mp3": ".mp3", "flac": ".flac",
       "m4a": ".m4a", "mp4": ".mov", "pdf": ".pdf", "docx": ".docx", "xlsx": ".xlsx",
       "pptx": ".pptx", "zip": ".zip"}


# --------------------------------------------------------------------------- #
# Realistic files of about `mb` megabytes
# --------------------------------------------------------------------------- #
def _photo(pixels: int, seed: int = 1):
    """A photo-like picture of about `pixels`: shapes with edges and colour (what
    a perceptual hash keys on), over a gradient, with sensor-like noise. A bare
    gradient-plus-noise picture made JPEG's full clean refuse at the perceptual
    gate (distance 10 > 6) -- a near-flat picture is a hash's worst case."""
    import numpy as np
    from PIL import Image, ImageDraw
    w = max(64, int((pixels * 4 / 3) ** 0.5)) // 16 * 16
    h = max(48, pixels // w) // 16 * 16
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w]
    base = np.stack([(x * 255 // w), (y * 255 // h), ((x + y) * 255 // (w + h))], -1)
    img = Image.fromarray(base.astype("uint8"), "RGB")
    draw = ImageDraw.Draw(img)
    for _ in range(60):
        x0, y0 = int(rng.integers(0, w)), int(rng.integers(0, h))
        x1, y1 = x0 + int(rng.integers(w // 20, w // 3)), y0 + int(rng.integers(h // 20, h // 3))
        fill = tuple(int(v) for v in rng.integers(0, 256, 3))
        (draw.ellipse if rng.random() < 0.5 else draw.rectangle)((x0, y0, x1, y1), fill=fill)
    arr = np.asarray(img, dtype="int16") + rng.normal(0, 12, (h, w, 3))
    return Image.fromarray(np.clip(arr, 0, 255).astype("uint8"), "RGB")


def _exif():
    from PIL import Image
    e = Image.Exif()
    e[0x010F], e[0x0110], e[0x0131] = "BenchCam", "Model 7", "Bench 1.0"
    return e


_BYTES_PER_PIXEL: dict[str, float] = {}


def _per_pixel(fmt: str, save) -> float:
    """Bytes a pixel this picture costs in `fmt`, measured once on a trial encode,
    so every file lands near the size asked for."""
    if fmt not in _BYTES_PER_PIXEL:
        img = _photo(400_000)
        buf = io.BytesIO()
        save(img, buf)
        _BYTES_PER_PIXEL[fmt] = len(buf.getvalue()) / (img.width * img.height)
    return _BYTES_PER_PIXEL[fmt]


def _jpeg_save(img, f):
    img.save(f, "JPEG", quality=92, exif=_exif())


def _jpeg_bytes(mb: float, seed: int = 1) -> bytes:
    buf = io.BytesIO()
    _jpeg_save(_photo(int(mb * MB / _per_pixel("jpeg", _jpeg_save)), seed), buf)
    return buf.getvalue()


def make(kind: str, mb: float, path: str, work: str) -> None:
    if kind == "jpeg":
        open(path, "wb").write(_jpeg_bytes(mb))
    elif kind == "png":
        from PIL.PngImagePlugin import PngInfo
        info = PngInfo()
        info.add_text("Software", "Bench 1.0")
        def png_save(img, f):
            img.save(f, "PNG", pnginfo=info, exif=_exif())
        png_save(_photo(int(mb * MB / _per_pixel("png", png_save))), path)
    elif kind == "tiff":
        _photo(int(mb * MB / 3)).save(path, "TIFF", exif=_exif())
    elif kind in ("mp3", "flac", "m4a"):
        rate = {"mp3": 192_000, "flac": 900_000, "m4a": 256_000}[kind] / 8
        seconds = max(1, int(mb * MB / rate))
        codec = {"mp3": ["-c:a", "libmp3lame", "-b:a", "192k"],
                 "flac": ["-c:a", "flac"],
                 "m4a": ["-c:a", "aac", "-b:a", "256k"]}[kind]
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        f"anoisesrc=d={seconds}:c=pink:r=44100:a=0.3", "-ac", "2",
                        *codec, "-metadata", "artist=Bench Artist",
                        "-metadata", "comment=bench", path], check=True)
    elif kind == "mp4":
        seconds = max(1, int(mb * MB / 1_000_000))       # 8 Mbit/s = 1 MB/s
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        f"testsrc2=size=1280x720:rate=30:d={seconds}", "-f", "lavfi",
                        "-i", f"sine=d={seconds}", "-c:v", "libx264", "-preset",
                        "ultrafast", "-b:v", "8M", "-maxrate", "8M", "-bufsize", "16M",
                        "-c:a", "aac", "-metadata", "location=+52.5200+013.4050/",
                        "-metadata", "make=Bench", path], check=True)
    elif kind == "pdf":
        import pikepdf
        pdf = pikepdf.new()
        pdf.docinfo["/Author"] = "Bench Author"
        pages = max(1, int(mb / 0.5))
        for i in range(pages):
            img = _jpeg_bytes(0.5, seed=i)
            stream = pikepdf.Stream(pdf, img)
            from PIL import Image
            w, h = Image.open(io.BytesIO(img)).size
            stream.Type, stream.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
            stream.Width, stream.Height = w, h
            stream.ColorSpace, stream.BitsPerComponent = pikepdf.Name.DeviceRGB, 8
            stream.Filter = pikepdf.Name.DCTDecode
            page = pikepdf.Page(pdf.add_blank_page(page_size=(612, 792)))
            page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=stream))
            page.Contents = pdf.make_stream(
                f"q 540 0 0 405 36 300 cm /Im0 Do Q BT /F1 12 Tf 36 280 Td "
                f"(Page {i + 1}) Tj ET".encode())
            page.Resources.Font = pikepdf.Dictionary(F1=pikepdf.Dictionary(
                Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
                BaseFont=pikepdf.Name.Helvetica))
        pdf.save(path)
    elif kind == "docx":
        from tests.scrub import docx_corpus as dc
        src = dc.synthetic(os.path.join(work, "base.docx"))
        n = max(1, int(mb / 0.5))
        _add_media(src, path, [(f"word/media/image{i}.jpeg", _jpeg_bytes(0.5, i))
                               for i in range(n)])
    elif kind == "xlsx":
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        rng = random.Random(1)
        rows = max(10, int(mb * MB / 48))            # ~48 bytes a row on disk (measured)
        for r in range(rows):
            ws.append([f"Item {r}", rng.random() * 1000, rng.randint(0, 10**6),
                       f"Note {rng.getrandbits(64):x}"])
        wb.properties.creator = "Bench Author"
        wb.save(path)
    elif kind == "pptx":
        from pptx import Presentation
        from pptx.util import Inches
        prs = Presentation()
        for i in range(max(1, int(mb / 0.5))):
            slide = prs.slides.add_slide(prs.slide_layouts[5])
            slide.shapes.title.text = f"Slide {i + 1}"
            slide.shapes.add_picture(io.BytesIO(_jpeg_bytes(0.5, i)), Inches(1), Inches(1.5))
        prs.core_properties.author = "Bench Author"
        prs.save(path)
    elif kind == "zip":
        n = max(1, int(mb / 2))
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for i in range(n):
                z.writestr(f"photos/IMG_{i:04d}.jpg", _jpeg_bytes(2, i))
    else:
        raise ValueError(kind)


def _add_media(src: str, dst: str, media: list[tuple[str, bytes]]) -> None:
    """Pictures added to a DOCX the way Word stores them: a part each, a content
    type, a relationship and a drawing that shows it."""
    with zipfile.ZipFile(src) as z:
        parts = {n: z.read(n) for n in z.namelist()}
    ct = parts["[Content_Types].xml"].decode()
    ct = ct.replace("</Types>", '<Default Extension="jpeg" ContentType="image/jpeg"/></Types>')
    rels = parts.get("word/_rels/document.xml.rels",
                     b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                     b'<Relationships xmlns="http://schemas.openxmlformats.org/package/'
                     b'2006/relationships"></Relationships>').decode()
    doc = parts["word/document.xml"].decode()
    drawings = []
    for i, (name, _body) in enumerate(media):
        rid = f"rIdBench{i}"
        rels = rels.replace("</Relationships>",
                            f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats'
                            f'.org/officeDocument/2006/relationships/image" '
                            f'Target="{name[5:]}"/></Relationships>')
        drawings.append(
            '<w:p><w:r><w:drawing><wp:inline xmlns:wp="http://schemas.openxmlformats.org/'
            'drawingml/2006/wordprocessingDrawing"><wp:extent cx="5486400" cy="4114800"/>'
            f'<wp:docPr id="{i + 1}" name="Picture {i + 1}"/><a:graphic xmlns:a="http://'
            'schemas.openxmlformats.org/drawingml/2006/main"><a:graphicData uri="http://'
            'schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic xmlns:pic="http:'
            '//schemas.openxmlformats.org/drawingml/2006/picture"><pic:nvPicPr><pic:cNvPr '
            f'id="{i + 1}" name="Picture {i + 1}"/><pic:cNvPicPr/></pic:nvPicPr>'
            '<pic:blipFill><a:blip xmlns:r="http://schemas.openxmlformats.org/officeDocument'
            f'/2006/relationships" r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch>'
            '</pic:blipFill><pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="5486400" '
            'cy="4114800"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
            '</pic:spPr></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing>'
            '</w:r></w:p>')
    doc = doc.replace("</w:body>", "".join(drawings) + "</w:body>")
    parts.update({"[Content_Types].xml": ct.encode(),
                  "word/_rels/document.xml.rels": rels.encode(),
                  "word/document.xml": doc.encode(), **dict(media)})
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(parts, key=lambda n: (n != "[Content_Types].xml", n)):
            z.writestr(name, parts[name])


# --------------------------------------------------------------------------- #
# One clean, measured in a fresh process
# --------------------------------------------------------------------------- #
_CHILD = r"""
import ctypes, json, os, resource, sys, threading, time
sys.path.insert(0, os.environ["REPO"])
from src.scrub import cli, crosscheck
from src.scrub.dispatch import default_dispatcher

def resident():
    if sys.platform.startswith("linux"):
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) * 1024
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

# What cannot be reclaimed: mapped files (streamed video) count in resident size,
# and the system drops their pages at will. macOS keeps a high-water mark of the
# physical footprint; Linux has none for anonymous memory alone, so it is sampled.
if sys.platform == "darwin":
    class RU(ctypes.Structure):
        _fields_ = [("uuid", ctypes.c_uint8 * 16)] + [(f"f{i}", ctypes.c_uint64)
                                                      for i in range(37)]
    _lib = ctypes.CDLL("/usr/lib/libproc.dylib")
    def peak():
        ru = RU()
        _lib.proc_pid_rusage(os.getpid(), 4, ctypes.byref(ru))
        return ru.f28                                  # lifetime_max_phys_footprint
    _stop = None
else:
    _top = [0]
    _stop = threading.Event()
    def _anon():
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("RssAnon:"):
                    return int(line.split()[1]) * 1024
        return 0
    def _sample():
        while not _stop.is_set():
            _top[0] = max(_top[0], _anon())
            time.sleep(0.002)
    def peak():
        return max(_top[0], _anon())

def children():
    p = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return p if sys.platform == "darwin" else p * 1024

src, dst, tier = sys.argv[1:4]
default_dispatcher()                      # imports and registration, not the clean
base, base_res = peak(), resident()
if _stop is not None:
    threading.Thread(target=_sample, daemon=True).start()
t0 = time.perf_counter()
cli.scrub_file(src, dst, tier, check_memory=False)
seconds = time.perf_counter() - t0
if _stop is not None:
    _stop.set()
ours, res = peak() - base, resident() - base_res
t1 = time.perf_counter()
try:
    crosscheck.run(src, dst)
    xcheck = time.perf_counter() - t1
except Exception:
    xcheck = None
print(json.dumps({"seconds": seconds, "peak": ours, "resident": res,
                  "tools_peak": children(), "crosscheck": xcheck}))
"""


def measure(path: str, tier: str, out: str, timeout: int) -> dict:
    env = dict(os.environ, REPO=REPO)
    t0 = time.monotonic()
    proc = subprocess.run([sys.executable, "-c", _CHILD, path, out, tier],
                          capture_output=True, text=True, env=env, timeout=timeout)
    if proc.returncode != 0:
        return {"error": (proc.stderr.strip().splitlines() or ["?"])[-1][:160],
                "wall": time.monotonic() - t0}
    return json.loads(proc.stdout.strip().splitlines()[-1])


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sizes", type=float, nargs="+", default=[1, 10, 100])
    ap.add_argument("--big", type=float, nargs="*", default=[1000])
    ap.add_argument("--heavy-max", type=float, default=10)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--timeout", type=int, default=3600)
    ap.add_argument("--json")
    ap.add_argument("--markdown")
    args = ap.parse_args(argv)

    kinds = [k for k in TIERS if not args.only or k in args.only]
    rows = []
    with tempfile.TemporaryDirectory(prefix="perf_") as td:
        for kind in kinds:
            sizes = list(args.sizes) + (list(args.big) if kind in BIG else [])
            for mb in sizes:
                path = os.path.join(td, f"{kind}_{mb:g}{EXT[kind]}")
                try:
                    make(kind, mb, path, td)
                except Exception as exc:                  # noqa: BLE001
                    print(f"{kind} {mb} MB: could not build ({exc})", file=sys.stderr)
                    continue
                size = os.path.getsize(path)
                for tier in TIERS[kind]:
                    if (kind, tier) in HEAVY and mb > args.heavy_max:
                        rows.append({"kind": kind, "tier": tier, "size": size,
                                     "skipped": f"heavy level, over {args.heavy_max:g} MB"})
                        continue
                    out = os.path.join(td, f"out_{kind}_{tier}{EXT[kind]}")
                    r = measure(path, tier, out, args.timeout)
                    r.update({"kind": kind, "tier": tier, "size": size})
                    rows.append(r)
                    if os.path.exists(out):
                        os.unlink(out)
                    print(_line(r), file=sys.stderr, flush=True)
                os.unlink(path)
    meta = {"machine": f"{platform.machine()} {platform.system()} "
                       f"{platform.mac_ver()[0] or platform.release()}",
            "python": platform.python_version(), "cpus": os.cpu_count(),
            "date": time.strftime("%Y-%m-%d")}
    if args.json:
        with open(args.json, "w") as f:
            json.dump({"meta": meta, "rows": rows}, f, indent=1)
    if args.markdown:
        with open(args.markdown, "w") as f:
            f.write(table(rows, meta))
    print(table(rows, meta))
    return 0


def _mb(n) -> str:
    return "-" if n is None else (f"{n / MB:.0f}" if n >= 10 * MB else f"{n / MB:.1f}")


def _line(r: dict) -> str:
    if "error" in r:
        return f"  {r['kind']:<5} {r['tier']} {_mb(r['size']):>6} MB  ERROR {r['error']}"
    xc = "-" if r["crosscheck"] is None else f"{r['crosscheck']:.2f} s"
    return (f"  {r['kind']:<5} {r['tier']} {_mb(r['size']):>6} MB  {r['seconds']:7.2f} s  "
            f"ours {_mb(r['peak']):>6} MB ({r['peak'] / r['size']:.1f}x)  "
            f"tools {_mb(r['tools_peak'])} MB  exiftool {xc}")


def table(rows: list[dict], meta: dict) -> str:
    out = [f"Measured {meta['date']} on {meta['machine']}, {meta['cpus']} CPUs, "
           f"Python {meta['python']}.", "",
           "Our peak is memory that cannot be given back (macOS physical footprint; "
           "Linux anonymous memory, sampled). Resident also counts mapped file pages, "
           "which the system reclaims at will: they make streamed video look large.", "",
           "| type | level | file MB | clean s | MB/s | our peak MB | x file | "
           "resident MB | external peak MB | ExifTool check s |",
           "|---|---|--:|--:|--:|--:|--:|--:|--:|--:|"]
    for r in rows:
        if "skipped" in r:
            out.append(f"| {r['kind']} | {r['tier']} | {_mb(r['size'])} | not run "
                       f"({r['skipped']}) | | | | | | |")
        elif "error" in r:
            out.append(f"| {r['kind']} | {r['tier']} | {_mb(r['size'])} | refused: "
                       f"{r['error']} | | | | | | |")
        else:
            rate = r["size"] / MB / r["seconds"] if r["seconds"] else 0
            xc = "-" if r["crosscheck"] is None else f"{r['crosscheck']:.2f}"
            out.append(f"| {r['kind']} | {r['tier']} | {_mb(r['size'])} | "
                       f"{r['seconds']:.2f} | {rate:.0f} | {_mb(r['peak'])} | "
                       f"{r['peak'] / r['size']:.1f} | {_mb(r.get('resident'))} | "
                       f"{_mb(r['tools_peak'])} | {xc} |")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
