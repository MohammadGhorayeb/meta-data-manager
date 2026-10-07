"""SVG corpus for CI: documents shaped like what real editors write, every locus
planted (`docs/p6_tail_plan.md` §1, §3).

- `inkscape`: the editor's namespaces with the document's file name, the version, an
  export path naming a home folder, a `sodipodi:namedview`, layer labels, RDF
  `<metadata>` with a creator and date, and a generator comment;
- `illustrator`: prefixes bound through DOCTYPE entities, a generator comment,
  `i:extraneous` attributes and an `i:pgf` block of private data;
- `sketch`: a generator comment, `sketch:type` attributes, and the `<desc>Created
  with Sketch.</desc>` the survey found in eleven app files;
- `libreoffice`: `ooo:` attributes (kept: its presentation script reads them) and
  an embedded photo carrying its own EXIF, as LibreOffice exports one.

Every shape also embeds a PNG with a text chunk, a nested SVG with its own metadata,
a kept `xml-stylesheet` and a dropped processing instruction. Planted values derive
from `variant` at a fixed length. Imports nothing from `src`.
"""
from __future__ import annotations

import base64
import gzip
import io
import struct
import zlib

from PIL import Image

INKSCAPE = "http://www.inkscape.org/namespaces/inkscape"
SODIPODI = "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd"


def secret(name: str, variant: int = 0) -> str:
    return f"SENTINEL-{name}-V{variant:02d}"


def photo(variant: int = 0, seed: int = 0) -> bytes:
    img = Image.new("RGB", (24, 16), (40 + seed * 37 % 200, 120, 200))
    img.paste((220, 40, 40), (0, 0, 8, 16))
    exif = Image.Exif()
    exif[0x013B] = secret("PHOTO-ARTIST", variant)
    exif[0x0131] = secret("PHOTO-SOFT", variant)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90, exif=exif.tobytes())
    return buf.getvalue()


def png(variant: int = 0) -> bytes:
    img = Image.new("RGBA", (12, 12), (10, 200, 80, 255))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    raw = buf.getvalue()
    text = b"Author\0" + secret("PNG-AUTHOR", variant).encode()
    chunk = struct.pack(">I", len(text)) + b"tEXt" + text + struct.pack(
        ">I", zlib.crc32(b"tEXt" + text) & 0xFFFFFFFF)
    return raw[:33] + chunk + raw[33:]


def nested(variant: int = 0) -> bytes:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="6" height="6">'
            f'<!-- {secret("NESTED-COMMENT", variant)} -->'
            f'<metadata>{secret("NESTED-META", variant)}</metadata>'
            f'<rect width="6" height="6" fill="#123456"/></svg>').encode()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _drawing(variant: int, extra_attr: str = "", seed: int = 0) -> str:
    """The drawing; a seed changes its shapes, colours, text and photo, so diverse
    inputs share no content (what the fingerprint guard needs to see past)."""
    return (f'<rect x="{2 + seed}" y="2" width="{40 - seed}" height="20" '
            f'fill="#e0{40 + seed * 11:02x}40"{extra_attr}/>'
            f'<circle cx="60" cy="{14 + seed}" r="9" fill="#2060c0"/>'
            f'<text x="4" y="40" font-size="8" fill="#000">Visible label {seed}</text>'
            f'<image x="4" y="44" width="24" height="16" '
            f'xlink:href="data:image/jpeg;base64,{_b64(photo(variant, seed))}"/>'
            f'<image x="32" y="44" width="12" height="12" '
            f'href="data:image/png;base64,{_b64(png(variant))}"/>'
            f'<image x="50" y="44" width="6" height="6" '
            f'href="data:image/svg+xml;base64,{_b64(nested(variant))}"/>')


_HEAD = ('<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
         '<?xml-stylesheet type="text/css" href="style.css"?>\n'
         '<?sentinel-editor-state {state}?>\n')
_ROOT = ('width="80" height="64" viewBox="0 0 80 64" version="1.1" '
         'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"')


def build(variant: int = 0, *, shape: str = "inkscape", seed: int = 0) -> bytes:
    v = variant
    head = _HEAD.format(state=secret("PI", v))
    if shape == "inkscape":
        doc = (head + f'<!-- Created with Inkscape ({secret("COMMENT", v)}) -->\n'
               f'<svg {_ROOT} xmlns:inkscape="{INKSCAPE}" xmlns:sodipodi="{SODIPODI}" '
               'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
               'xmlns:dc="http://purl.org/dc/elements/1.1/" '
               'xmlns:cc="http://creativecommons.org/ns#" '
               f'sodipodi:docname="{secret("DOCNAME", v)}.svg" '
               f'inkscape:version="1.3 ({secret("VERSION", v)})" '
               f'inkscape:export-filename="/home/sentinel{v:02d}/Desktop/logo.png" '
               'inkscape:export-xdpi="96">\n'
               f'<sodipodi:namedview id="namedview1" inkscape:zoom="2" '
               f'inkscape:window-width="1440"><inkscape:page x="0" y="0" '
               f'id="page1"/></sodipodi:namedview>\n'
               '<metadata id="metadata1"><rdf:RDF><cc:Work rdf:about="">'
               f'<dc:creator><cc:Agent><dc:title>{secret("CREATOR", v)}</dc:title>'
               f'</cc:Agent></dc:creator><dc:date>2001-02-0{v % 10}</dc:date>'
               '</cc:Work></rdf:RDF></metadata>\n'
               '<g inkscape:label="Layer 1" inkscape:groupmode="layer" id="layer1">'
               + _drawing(v, ' inkscape:connector-curvature="0"', seed) + '</g>\n</svg>\n')
    elif shape == "illustrator":
        doc = ('<?xml version="1.0" encoding="utf-8"?>\n'
               f'<!-- Generator: Adobe Illustrator 27.0.0, SVG Export Plug-In . '
               f'{secret("COMMENT", v)} -->\n'
               '<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
               '"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd" [\n'
               '\t<!ENTITY ns_extend "http://ns.adobe.com/Extensibility/1.0/">\n'
               '\t<!ENTITY ns_ai "http://ns.adobe.com/AdobeIllustrator/10.0/">\n'
               '\t<!ENTITY ns_graphs "http://ns.adobe.com/Graphs/1.0/">\n'
               '\t<!ENTITY ns_svg "http://www.w3.org/2000/svg">\n'
               '\t<!ENTITY ns_xlink "http://www.w3.org/1999/xlink">\n]>\n'
               '<svg width="80" height="64" viewBox="0 0 80 64" version="1.1" '
               'xmlns:x="&ns_extend;" xmlns:i="&ns_ai;" xmlns:graph="&ns_graphs;" '
               'xmlns="&ns_svg;" xmlns:xlink="&ns_xlink;">\n'
               f'<g i:extraneous="self">' + _drawing(v, seed=seed) + '</g>\n'
               f'<i:pgf id="adobe_illustrator_pgf"><![CDATA[{secret("PGF", v)}]]>'
               '</i:pgf>\n</svg>\n')
    elif shape == "sketch":
        doc = (head + f'<svg {_ROOT} '
               'xmlns:sketch="http://www.bohemiancoding.com/sketch/ns">\n'
               f'<!-- Generator: Sketch 47.1 (45422) - {secret("COMMENT", v)} -->\n'
               '<title>Artboard</title>\n<desc>Created with Sketch.</desc>\n'
               '<g sketch:type="MSArtboardGroup" stroke="none">' + _drawing(v, seed=seed)
               + '</g>\n</svg>\n')
    elif shape == "libreoffice":
        doc = (head + f'<svg {_ROOT} xmlns:ooo="http://xml.openoffice.org/svg/export">\n'
               '<g class="SlideGroup" ooo:name="page1" ooo:numberingtype="0">'
               + _drawing(v, seed=seed) + '</g>\n</svg>\n')
    else:
        raise ValueError(shape)
    return doc.encode("utf-8")


def svgz(variant: int = 0, shape: str = "inkscape") -> bytes:
    """A gzipped SVG whose gzip header names the original file and its time."""
    buf = io.BytesIO()
    with gzip.GzipFile(filename=f"{secret('GZNAME', variant)}.svg", mode="wb",
                       fileobj=buf, mtime=1_791_279_045 + variant) as g:
        g.write(build(variant, shape=shape))
    return buf.getvalue()


SHAPES = ("inkscape", "illustrator", "sketch", "libreoffice")
_COMMON = ("PI", "PHOTO-ARTIST", "PHOTO-SOFT", "PNG-AUTHOR", "NESTED-COMMENT",
           "NESTED-META")
_BY_SHAPE = {"inkscape": ("COMMENT", "DOCNAME", "VERSION", "CREATOR"),
             "illustrator": ("COMMENT", "PGF"),
             "sketch": ("COMMENT",), "libreoffice": ()}


def planted(variant: int = 0, shape: str = "inkscape") -> list[bytes]:
    """Every planted value as it appears in the bytes -- the ones inside embedded
    pictures are checked after decoding (they sit in base64)."""
    names = _BY_SHAPE[shape] + (() if shape == "illustrator" else ("PI",))
    out = [secret(n, variant).encode() for n in names]
    if shape == "inkscape":
        out.append(f"/home/sentinel{variant:02d}/Desktop".encode())
    return out


def embedded_planted(variant: int = 0) -> list[bytes]:
    return [secret(n, variant).encode() for n in _COMMON if n != "PI"]
