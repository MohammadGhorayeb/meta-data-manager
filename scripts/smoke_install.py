"""Does an INSTALLED scrubmeta work? (November plan N4)

The test suite runs from a checkout with every development dependency, so it cannot
notice a module left out of the package or a dependency missing from the install.
This uses only what a user has -- the `scrubmeta` command and the runtime libraries:
it builds one file of each kind with a planted secret, cleans it through the command,
and checks the secret is gone and the exit code is 0. Then one folder with `--jobs`,
because worker processes import the package by its installed name.

Usage:  python scripts/smoke_install.py [path/to/scrubmeta]
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

SECRET = "SMOKE-SECRET-7f3a"
PROGRAM = sys.argv[1] if len(sys.argv) > 1 else "scrubmeta"
CT = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
PKG = "http://schemas.openxmlformats.org/package/2006"
DOC = "http://schemas.openxmlformats.org/officeDocument/2006"


def _picture():
    from PIL import Image
    img = Image.new("RGB", (64, 48), (90, 140, 200))
    img.paste((200, 30, 60), (0, 0, 20, 48))
    return img


def _exif():
    from PIL import Image
    e = Image.Exif()
    e[0x013B] = SECRET                                # Artist
    return e


def jpeg(path):
    _picture().save(path, "JPEG", exif=_exif())


def png(path):
    from PIL.PngImagePlugin import PngInfo
    info = PngInfo()
    info.add_text("Author", SECRET)
    _picture().save(path, "PNG", pnginfo=info)


def tiff(path):
    _picture().save(path, "TIFF", exif=_exif())


def webp(path):
    _picture().save(path, "WEBP", exif=_exif(), lossless=True)


def gif(path):
    _picture().convert("P").save(path, "GIF", comment=SECRET.encode())


def svg(path):
    with open(path, "w") as f:
        f.write('<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4">'
                f'<!-- {SECRET} --><rect width="4" height="4"/></svg>')


def pdf(path):
    import pikepdf
    doc = pikepdf.new()
    doc.add_blank_page(page_size=(100, 100))
    doc.docinfo["/Author"] = SECRET
    doc.save(path)


def _core() -> str:
    return (CT + '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/'
            '2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f'<dc:creator>{SECRET}</dc:creator></cp:coreProperties>')


def _package(path, main_type, main_part, main_xml, extra=()):
    types = (CT + f'<Types xmlns="{PKG}/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package'
             '.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
             f'<Override PartName="/{main_part}" ContentType="{main_type}"/>'
             '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxml'
             'formats-package.core-properties+xml"/></Types>')
    rels = (CT + f'<Relationships xmlns="{PKG}/relationships">'
            f'<Relationship Id="rId1" Type="{DOC}/relationships/officeDocument" '
            f'Target="{main_part}"/><Relationship Id="rId2" Type="{PKG}/relationships/'
            'metadata/core-properties" Target="docProps/core.xml"/></Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("_rels/.rels", rels)
        z.writestr("docProps/core.xml", _core())
        z.writestr(main_part, main_xml)
        for name, body in extra:
            z.writestr(name, body)


def docx(path):
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    _package(path, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
             ".main+xml", "word/document.xml",
             CT + f'<w:document xmlns:w="{w}"><w:body><w:p><w:r><w:t>Hello</w:t></w:r></w:p>'
             '</w:body></w:document>')


def xlsx(path):
    x = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    sml = "application/vnd.openxmlformats-officedocument.spreadsheetml"
    _package(path, f"{sml}.sheet.main+xml", "xl/workbook.xml",
             CT + f'<workbook xmlns="{x}" xmlns:r="{DOC}/relationships"><sheets>'
             '<sheet name="S" sheetId="1" r:id="rId1"/></sheets></workbook>',
             [("xl/_rels/workbook.xml.rels",
               CT + f'<Relationships xmlns="{PKG}/relationships"><Relationship Id="rId1" '
               f'Type="{DOC}/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
               '</Relationships>'),
              ("xl/worksheets/sheet1.xml",
               CT + f'<worksheet xmlns="{x}"><sheetData><row r="1"><c r="A1"><v>1</v></c>'
               '</row></sheetData></worksheet>')])


def archive(path):
    buf = io.BytesIO()
    jpeg(buf)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("photo.jpg", buf.getvalue())


def _ffmpeg(path, *codec):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=d=1", *codec, "-metadata", f"artist={SECRET}", path], check=True)


def mp3(path):
    _ffmpeg(path, "-c:a", "libmp3lame")


def flac(path):
    _ffmpeg(path, "-c:a", "flac")


def m4a(path):
    _ffmpeg(path, "-c:a", "aac")


BUILDERS = {"jpg": jpeg, "png": png, "tif": tiff, "webp": webp, "gif": gif, "svg": svg,
            "pdf": pdf, "docx": docx, "xlsx": xlsx, "zip": archive}
if shutil.which("ffmpeg"):
    BUILDERS |= {"mp3": mp3, "flac": flac, "m4a": m4a}


def _bytes_of(path: str) -> bytes:
    """The file, plus every member's contents if it is a ZIP: Office and archive
    members are compressed, so a plain search would see nothing either way."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:2] == b"PK":
        with zipfile.ZipFile(path) as z:
            data += b"".join(z.read(n) for n in z.namelist())
    return data


def main() -> int:
    failures = []
    with tempfile.TemporaryDirectory() as td:
        source = os.path.join(td, "in")
        os.makedirs(source)
        for ext, build in BUILDERS.items():
            src, dst = os.path.join(source, f"f.{ext}"), os.path.join(td, f"out.{ext}")
            build(src)
            if SECRET.encode() not in _bytes_of(src):
                failures.append(f"{ext}: the secret was never planted")
                continue
            r = subprocess.run([PROGRAM, src, dst, "--no-report"], capture_output=True, text=True)
            if r.returncode != 0:
                failures.append(f"{ext}: exit {r.returncode}: {r.stderr.strip()[:200]}")
            elif SECRET.encode() in _bytes_of(dst):
                failures.append(f"{ext}: the secret survived")
            else:
                print(f"  {ext:<5} clean")
        out = os.path.join(td, "cleaned")
        r = subprocess.run([PROGRAM, source, out, "--jobs", "2", "--no-report"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            failures.append(f"folder: exit {r.returncode}: {r.stderr.strip()[-300:]}")
        else:
            written = [n for n in os.listdir(out) if n.startswith("f.")]
            if len(written) != len(BUILDERS):
                failures.append(f"folder: {len(written)} of {len(BUILDERS)} files written")
            else:
                print(f"  folder --jobs 2: {len(written)} files")
    for line in failures:
        print("FAILED", line)
    print("ok" if not failures else f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
