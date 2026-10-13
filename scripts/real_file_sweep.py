"""Real-file sweep: the files Apple and app vendors ship, cleaned in memory.

`docs/p6_tail_plan.md` §14. The corpora plant values where we already know to look;
this runs the tool over real producers' output instead -- the images, PDFs, audio,
video and archives inside `/System/Library` and `/Applications` bundles on a Mac
(vendor assets, never a user's files). Per file: dispatch, F1, the handler's own
residual check, and a content check -- the DISPLAYED pixels for pictures (EXIF
rotation applied, the lesson of the sideways-photo bug), page count and text for
PDFs, the handlers' own sample-digest checks for audio and video -- then ExifTool's
identity tags before and after, with their VALUES, since a field blanked in place
(TIFF) or zeroed (MP4 dates) is still listed.

Aggregates only: no value from any file is printed or written.

Usage (macOS):  ./.venv/bin/python scripts/real_file_sweep.py [files-per-type]
"""
import collections
import json
import os
import random
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PIL import Image, ImageOps, ImageSequence  # noqa: E402

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:                                         # noqa: BLE001
    pass

from src.scrub.dispatch import default_dispatcher  # noqa: E402
from src.scrub.errors import ScrubError, UnsupportedFormatError  # noqa: E402

ROOTS = ["/System/Library", "/Applications"]
TYPES = {"jpeg": ("jpg", "jpeg"), "png": ("png",), "tiff": ("tif", "tiff"),
         "gif": ("gif",), "webp": ("webp",), "heic": ("heic",), "pdf": ("pdf",),
         "mp3": ("mp3",), "m4a": ("m4a",), "video": ("mp4", "mov"), "zip": ("zip",),
         "svg": ()}
PER_TYPE = int(sys.argv[1]) if len(sys.argv) > 1 else 150
IDENTITY = ("GPS", "Artist", "Author", "Creator", "Copyright", "Make", "Model",
            "Serial", "Software", "Owner", "UserComment", "Producer", "CreatorTool",
            "DocumentID", "InstanceID", "History", "DateTimeOriginal", "CreateDate")


def find(exts):
    out = []
    for root in ROOTS:
        p = subprocess.run(["find", root, "-type", "f", "("]
                           + sum([["-iname", f"*.{e}", "-o"] for e in exts], [])[:-1]
                           + [")"], capture_output=True, text=True)
        out += p.stdout.splitlines()
    return sorted(set(out))


def displayed(data: bytes):
    import io
    with Image.open(io.BytesIO(data)) as im:
        frames = []
        for f in ImageSequence.Iterator(im):
            frames.append(ImageOps.exif_transpose(f.copy()).convert("RGBA").tobytes())
            if len(frames) >= 8:
                break
        return frames


def pdf_text(path):
    r = subprocess.run(["pdftotext", "-q", path, "-"], capture_output=True)
    return r.stdout if r.returncode == 0 else None


def pdf_pages(path):
    r = subprocess.run(["pdfinfo", path], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        if line.startswith("Pages:"):
            return line.split()[1]
    return None


def exiftool_identity(paths):
    """{path: set of identity tag names} in one ExifTool run."""
    out = {}
    for i in range(0, len(paths), 200):
        chunk = paths[i:i + 200]
        r = subprocess.run(["exiftool", "-j", "-G1", "-a"] + chunk,
                           capture_output=True, text=True)
        try:
            for rec in json.loads(r.stdout or "[]"):
                tags = {k for k, v in rec.items() if any(w in k for w in IDENTITY)
                        and not k.startswith(("System:", "File:", "ExifTool:",
                                              "Composite:"))
                        and str(v).strip(" 0:") != ""}
                out[rec["SourceFile"]] = tags
        except json.JSONDecodeError:
            pass
    return out


def main():
    d = default_dispatcher()
    rnd = random.Random(7)
    work = tempfile.mkdtemp(prefix="sweep_")
    summary = {}
    for kind, exts in TYPES.items():
        if not exts:
            continue
        files = find(exts)
        sample = rnd.sample(files, min(PER_TYPE, len(files)))
        stats = collections.Counter()
        reasons = collections.Counter()
        pairs = []
        for k, path in enumerate(sample):
            try:
                data = open(path, "rb").read()
            except OSError:
                stats["unreadable"] += 1
                continue
            try:
                h = d.resolve(data)
            except UnsupportedFormatError:
                stats["not supported"] += 1
                continue
            try:
                out = h.scrub(data, "F1")
            except ScrubError as e:
                stats["refused"] += 1
                reasons[f"{type(e).__name__}: {str(e)[:70]}"] += 1
                continue
            except Exception as e:                        # noqa: BLE001
                stats["ESCAPE"] += 1
                reasons[f"ESCAPE {type(e).__name__}: {str(e)[:70]}"] += 1
                continue
            res = h.verify(out, "F1") if hasattr(h, "verify") else []
            if res:
                stats["residual (CLI refuses)"] += 1
                reasons[f"residual: {res[0][:70]}"] += 1
                continue
            stats["cleaned"] += 1
            if out == data:
                stats["  unchanged (nothing to remove)"] += 1
            ext = os.path.splitext(path)[1]
            a, b = (os.path.join(work, f"{kind}_{k}_in{ext}"),
                    os.path.join(work, f"{kind}_{k}_out{ext}"))
            open(a, "wb").write(data)
            open(b, "wb").write(out)
            pairs.append((a, b))
            if kind in ("jpeg", "png", "tiff", "gif", "webp", "heic"):
                try:
                    same = displayed(data) == displayed(out)
                except Exception:                         # noqa: BLE001
                    same = None
                stats[{True: "  display identical", False: "  DISPLAY DIFFERS",
                       None: "  display not decodable"}[same]] += 1
            elif kind == "pdf":
                same = (pdf_pages(a) == pdf_pages(b) and pdf_text(a) == pdf_text(b))
                stats["  pages+text identical" if same else "  PDF TEXT DIFFERS"] += 1
        before = exiftool_identity([a for a, _ in pairs])
        after = exiftool_identity([b for _, b in pairs])
        kept_tags = collections.Counter()
        for a, b in pairs:
            had, has = before.get(a, set()), after.get(b, set())
            if had:
                stats["  had identity tags (ExifTool)"] += 1
            if has:
                stats["  identity tags left (ExifTool)"] += 1
                kept_tags.update(t.split(":")[-1] for t in has)
        summary[kind] = {"files found": len(files), "sampled": len(sample),
                         **dict(sorted(stats.items())),
                         "top refusal reasons": reasons.most_common(4),
                         "identity tags ExifTool still reads": kept_tags.most_common(8)}
        print(kind, json.dumps(summary[kind], indent=1), flush=True)
    if len(sys.argv) > 2:
        json.dump(summary, open(sys.argv[2], "w"), indent=1)


if __name__ == "__main__":
    main()
