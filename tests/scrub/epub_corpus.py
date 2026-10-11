"""EPUB corpus for CI: books shaped like what real producers write, every locus
planted (`docs/p6_tail_plan.md` §1, §11).

- `libreoffice`: EPUB 3, minified, a bare UUID, the generator line naming the build;
- `pandoc`: EPUB 3, pretty-printed, `urn:uuid`, creator with a role refinement,
  date, subject, description, publisher, rights, accessibility properties, Apple's
  display options, `dtb:uid` in the NCX, a generator meta in every chapter;
- `calibre`: EPUB 2 with two identifiers (the library's and the book's), `opf:`
  attributes, a contributor naming calibre, `calibre:*` metas (timestamp, a user
  column), the EPUB 2 cover pointer, `docAuthor` and `dtb:generator` in the NCX,
  `META-INF/calibre_bookmarks.txt` (where the reader stopped), and an unlisted
  `iTunesMetadata.plist`;
- `sigil`: EPUB 3 with a font obfuscated by the IDPF algorithm and a Sigil meta;
- `indesign`: EPUB 3 with a font obfuscated by Adobe's algorithm.

Every shape holds the same book: two chapters, a title page that prints a fixed
byline (the book's text, the same in every variant), a stylesheet, a cover photo
with EXIF and GPS, an SVG with a comment, and -- in the font shapes -- a font.
Planted values derive from `variant` at a fixed length; `seed` changes the
content. Written byte by byte through `zip_corpus.write`; imports nothing from
`src`.
"""
from __future__ import annotations

import hashlib
import io
import re
import zipfile

from tests.scrub import zip_corpus as zc

SHAPES = ("libreoffice", "pandoc", "calibre", "sigil", "indesign")
IDPF = "http://www.idpf.org/2008/embedding"
ADOBE = "http://ns.adobe.com/pdf/enc#RC"
BYLINE = "by A. Writer"                     # printed on the title page: content


def secret(name: str, variant: int = 0) -> str:
    return f"SENTINEL-{name}-V{variant:02d}"


def uuid_for(variant: int, salt: str = "book") -> str:
    h = hashlib.sha256(f"{salt}{variant}".encode()).hexdigest()
    return f"{h[:8]}-{h[8:12]}-4{h[13:16]}-a{h[17:20]}-{h[20:32]}"


def font(seed: int = 0) -> bytes:
    """A stand-in font: a TrueType signature and 1,500 bytes, so obfuscation (the
    first 1,040 or 1,024 bytes) has something past it to leave alone."""
    body = bytes((i * 7 + seed * 13) % 251 for i in range(1500))
    return b"\x00\x01\x00\x00" + body


def obfuscate(data: bytes, identifier: str, algorithm: str) -> bytes:
    if algorithm == IDPF:
        key = hashlib.sha1(re.sub(r"[ \t\r\n]", "", identifier).encode()).digest()
        n = 1040
    else:
        key = bytes.fromhex(re.sub(r"(?i)^urn:uuid:|-", "", identifier))
        n = 1024
    n = min(n, len(data))
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data[:n])) + data[n:]


def chapter(n: int, seed: int, head_extra: str = "", pretty: bool = False) -> str:
    nl = "\n" if pretty else ""
    return ('<?xml version="1.0" encoding="UTF-8"?>' + nl
            + '<html xmlns="http://www.w3.org/1999/xhtml" '
            'xmlns:epub="http://www.idpf.org/2007/ops">' + nl
            + f"<head>{nl}<title>Chapter {n}</title>{nl}{head_extra}"
            '<link rel="stylesheet" type="text/css" href="style.css"/>'
            + nl + "</head>" + nl
            + f'<body><h1>Chapter {n}</h1><p>Words of chapter {n}, edition {seed}'
            + zc.filler(seed) + "</p>"
            + ('<p><img src="cover.jpg" alt="cover"/><img src="figure.svg" '
               'alt="figure"/></p>' if n == 1 else "")
            + "</body>" + nl + "</html>" + nl)


def title_page(seed: int, head_extra: str = "") -> str:
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<html '
            'xmlns="http://www.w3.org/1999/xhtml"><head><title>Title</title>'
            f'{head_extra}</head><body><h1>The Book {seed}</h1><p>{BYLINE}</p>'
            '</body></html>\n')


def nav(seed: int = 0) -> str:
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<html '
            'xmlns="http://www.w3.org/1999/xhtml" '
            'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Contents'
            + zc.filler(seed, 3) + '</title>'
            '</head><body><nav epub:type="toc"><ol><li><a href="ch1.xhtml">Chapter 1'
            '</a></li><li><a href="ch2.xhtml">Chapter 2</a></li></ol></nav></body>'
            '</html>\n')


def ncx(uid: str, extra_head: str = "", author: str = "", seed: int = 0) -> str:
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<ncx version="2005-1" '
            'xmlns="http://www.daisy.org/z3986/2005/ncx/"><head>'
            f'<meta name="dtb:uid" content="{uid}"/>{extra_head}'
            '<meta name="dtb:depth" content="1"/></head><docTitle><text>The Book'
            + zc.filler(seed, 4) + '</text></docTitle>'
            + (f"<docAuthor><text>{author}</text></docAuthor>" if author else "")
            + '<navMap><navPoint id="n1" playOrder="1"><navLabel><text>Chapter 1'
            '</text></navLabel><content src="ch1.xhtml"/></navPoint><navPoint '
            'id="n2" playOrder="2"><navLabel><text>Chapter 2</text></navLabel>'
            '<content src="ch2.xhtml"/></navPoint></navMap></ncx>\n')


def svg(variant: int, seed: int) -> bytes:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
            f'<!-- {secret("SVG-COMMENT", variant)} --><rect width="{4 + seed}" '
            'height="4" fill="#204080"/>'
            + (f'<g id="g{zc.filler(seed, 9)}"/>' if seed else "") + '</svg>').encode()


CSS = "body { margin: 1em; }\nh1 { font-size: 1.4em; }\n"


def _manifest(items: list[tuple[str, str, str, str]]) -> str:
    return "".join(f'<item id="{i}" href="{h}" media-type="{t}"'
                   + (f' properties="{p}"' if p else "") + "/>"
                   for i, h, t, p in items)


def _base_items(epub3: bool, with_font: bool) -> list[tuple[str, str, str, str]]:
    items = [("ncx", "toc.ncx", "application/x-dtbncx+xml", ""),
             ("title", "title.xhtml", "application/xhtml+xml", ""),
             ("ch1", "ch1.xhtml", "application/xhtml+xml", ""),
             ("ch2", "ch2.xhtml", "application/xhtml+xml", ""),
             ("css", "style.css", "text/css", ""),
             ("cover", "cover.jpg", "image/jpeg", "cover-image" if epub3 else ""),
             ("figure", "figure.svg", "image/svg+xml", "")]
    if epub3:
        items.append(("nav", "nav.xhtml", "application/xhtml+xml", "nav"))
    if with_font:
        items.append(("font", "fonts/book.ttf", "font/ttf", ""))
    return items


_SPINE = ('<spine toc="ncx"><itemref idref="title"/><itemref idref="ch1"/>'
          '<itemref idref="ch2"/></spine>')
_CONTAINER = ('<?xml version="1.0" encoding="UTF-8"?>\n<container version="1.0" '
              'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
              '<rootfile full-path="{root}/content.opf" '
              'media-type="application/oebps-package+xml"/></rootfiles></container>\n')


def files(variant: int = 0, shape: str = "pandoc", seed: int = 0) -> dict[str, bytes]:
    """{path: bytes} of the book, before packaging. A seed also renames the book's
    folder (a different length per seed) and lengthens every part, so diverse
    books share no member name, size or offset."""
    v = variant
    epub3 = shape != "calibre"
    with_font = shape in ("sigil", "indesign")
    modified = f"2026-10-0{1 + v % 9}T1{v % 10}:00:00Z"
    uid = {"libreoffice": uuid_for(v), "calibre": uuid_for(v)}.get(
        shape, f"urn:uuid:{uuid_for(v)}")
    gen_meta = ""
    meta = ""
    if shape == "libreoffice":
        meta = (f'<dc:identifier id="unique-identifier">{uid}</dc:identifier>'
                '<dc:title>The Book</dc:title>'
                f'<dc:creator>{secret("CREATOR", v)}</dc:creator>'
                '<dc:language>en</dc:language>'
                f'<meta property="dcterms:modified">{modified}</meta>'
                f'<meta content="LibreOffice/26.2.4.2$MacOSX_AARCH64 '
                f'LibreOffice_project/{secret("COMMIT", v)} (libepubgen/0.1.1)" '
                'name="generator"/>')
        uid_ref = "unique-identifier"
    elif shape == "pandoc":
        meta = ('\n    <dc:identifier id="epub-id-1">' + uid + "</dc:identifier>"
                '\n    <dc:title id="epub-title-1">The Book</dc:title>'
                '\n    <meta refines="#epub-title-1" property="title-type">main</meta>'
                f'\n    <dc:date id="epub-date">2001-02-0{1 + v % 9}</dc:date>'
                '\n    <dc:language>en</dc:language>'
                f'\n    <dc:creator id="epub-creator-1">{secret("CREATOR", v)}'
                '</dc:creator>'
                '\n    <meta refines="#epub-creator-1" property="role" '
                'scheme="marc:relators">aut</meta>'
                f'\n    <dc:subject>{secret("SUBJECT", v)}</dc:subject>'
                f'\n    <dc:description>{secret("DESCRIPTION", v)}</dc:description>'
                f'\n    <dc:publisher>{secret("PUBLISHER", v)}</dc:publisher>'
                f'\n    <dc:rights>{secret("RIGHTS", v)}</dc:rights>'
                f'\n    <meta property="dcterms:modified">{modified}</meta>'
                '\n    <meta property="schema:accessMode">textual</meta>'
                '\n    <meta property="ibooks:specified-fonts">true</meta>\n  ')
        uid_ref = "epub-id-1"
        gen_meta = '<meta name="generator" content="pandoc"/>\n'
    elif shape == "calibre":
        meta = (f'<dc:identifier opf:scheme="calibre" id="calibre_id">'
                f'{uuid_for(v, "library")}</dc:identifier>'
                f'<dc:identifier opf:scheme="uuid" id="uuid_id">{uid}</dc:identifier>'
                '<dc:title>The Book</dc:title>'
                f'<dc:creator opf:file-as="{secret("FILE-AS", v)}" opf:role="aut">'
                f'{secret("CREATOR", v)}</dc:creator>'
                f'<dc:contributor opf:role="bkp">calibre ({secret("CALVER", v)}) '
                '[https://calibre-ebook.com]</dc:contributor>'
                f'<dc:date>2001-02-0{1 + v % 9}T00:00:00+00:00</dc:date>'
                '<dc:language>en</dc:language>'
                f'<meta name="calibre:timestamp" content="{modified}"/>'
                f'<meta name="calibre:title_sort" content="{secret("SORT", v)}"/>'
                f'<meta name="calibre:user_metadata:#read" content="{{&quot;v&quot;:'
                f' &quot;{secret("COLUMN", v)}&quot;}}"/>'
                '<meta name="cover" content="cover"/>')
        uid_ref = "uuid_id"
        gen_meta = f'<meta name="author" content="{secret("XHTML-AUTHOR", v)}"/>'
    else:                                                  # sigil, indesign
        meta = (f'<dc:identifier id="BookId">{uid}</dc:identifier>'
                '<dc:title>The Book</dc:title><dc:language>en</dc:language>'
                f'<dc:creator id="cre">{secret("CREATOR", v)}</dc:creator>'
                f'<meta property="dcterms:modified">{modified}</meta>'
                f'<meta name="{"Sigil version" if shape == "sigil" else "generator"}"'
                f' content="{secret("TOOL", v)}"/>')
        uid_ref = "BookId"
    opf_attrs = ('version="3.0"' if epub3 else 'version="2.0"')
    if shape == "pandoc":
        opf_attrs += (' prefix="ibooks: http://vocabulary.itunes.apple.com/rdf/ibooks/'
                      'vocabulary-extensions-1.0/"')
    opf = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           f'<package xmlns="http://www.idpf.org/2007/opf" {opf_attrs} '
           f'unique-identifier="{uid_ref}">'
           '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/" '
           'xmlns:opf="http://www.idpf.org/2007/opf">'
           f'<!-- {secret("OPF-COMMENT", v)} -->{meta}</metadata>'
           f'<manifest>{_manifest(_base_items(epub3, with_font))}</manifest>'
           f'{_SPINE}</package>\n')
    ncx_extra = (f'<meta name="dtb:generator" content="{secret("NCXGEN", v)}"/>'
                 if shape == "calibre" else "")
    root = "OEBPS" if not seed else chr(ord("a") + seed) * (seed + 2)
    out = {
        "META-INF/container.xml": _CONTAINER.format(root=root).encode(),
        f"{root}/content.opf": opf.encode(),
        f"{root}/toc.ncx": ncx(uid, ncx_extra,
                               secret("DOCAUTHOR", v) if shape == "calibre" else "",
                               seed).encode(),
        f"{root}/title.xhtml": title_page(seed, gen_meta).encode(),
        f"{root}/ch1.xhtml": chapter(1, seed, gen_meta,
                                     pretty=shape == "pandoc").encode(),
        f"{root}/ch2.xhtml": chapter(2, seed, gen_meta,
                                     pretty=shape == "pandoc").encode(),
        f"{root}/style.css": (CSS + (f"/* {zc.filler(seed, 6)} */\n" if seed
                                      else "")).encode(),
        f"{root}/cover.jpg": zc.photo(v, seed),
        f"{root}/figure.svg": svg(v, seed),
    }
    if epub3:
        out[f"{root}/nav.xhtml"] = nav(seed).encode()
    if shape == "pandoc":
        out["META-INF/com.apple.ibooks.display-options.xml"] = (
            b'<?xml version="1.0" encoding="UTF-8"?>\n<display_options><platform '
            b'name="*"><option name="specified-fonts">true</option></platform>'
            b'</display_options>\n')
    if shape == "calibre":
        out["META-INF/calibre_bookmarks.txt"] = (
            f"encoding=json+base64:\n{secret('BOOKMARK', v)}\n").encode()
        out["iTunesMetadata.plist"] = (
            f'<?xml version="1.0"?><plist><dict><key>appleId</key><string>'
            f'{secret("APPLEID", v)}</string></dict></plist>').encode()
    if with_font:
        algorithm = IDPF if shape == "sigil" else ADOBE
        out[f"{root}/fonts/book.ttf"] = obfuscate(font(seed), uid, algorithm)
        out["META-INF/encryption.xml"] = (
            '<?xml version="1.0" encoding="UTF-8"?>\n<encryption xmlns="urn:oasis:'
            'names:tc:opendocument:xmlns:container" xmlns:enc="http://www.w3.org/'
            '2001/04/xmlenc#"><enc:EncryptedData><enc:EncryptionMethod Algorithm="'
            f'{algorithm}"/><enc:CipherData><enc:CipherReference URI="{root}/fonts/'
            'book.ttf"/></enc:CipherData></enc:EncryptedData></encryption>\n').encode()
    return out


def build(variant: int = 0, *, shape: str = "pandoc", seed: int = 0) -> bytes:
    """The book packaged the way its producer packages it."""
    book = files(variant, shape, seed)
    when = (2026, 10, 1 + variant % 9, 10 + variant, 0, 0)
    cs, made_by, flags = {"libreoffice": (0, 20, 0x800), "pandoc": (0, 0, 0x800),
                          "calibre": (3, 20, 0), "sigil": (3, 20, 0),
                          "indesign": (0, 20, 0)}[shape]
    entries = [zc.Entry(b"mimetype", b"application/epub+zip", method=0, flags=flags,
                        when=when, create_system=cs, made_by=made_by,
                        external=0 if cs == 0 else 0o100644 << 16)]
    names = sorted(book) if shape != "libreoffice" else list(reversed(sorted(book)))
    for name in names:
        entries.append(zc.Entry(name.encode(), book[name], flags=flags, when=when,
                                create_system=cs, made_by=made_by,
                                external=0 if cs == 0 else 0o100644 << 16))
    return zc.write(entries)


def planted(variant: int = 0, shape: str = "pandoc") -> list[bytes]:
    """Every planted value as bytes, found in the raw archive or a member once
    decompressed (the photo's EXIF and the SVG's comment included)."""
    names = ["CREATOR", "OPF-COMMENT", "SVG-COMMENT", "PHOTO-ARTIST", "PHOTO-SOFT"]
    names += {"libreoffice": ["COMMIT"],
              "pandoc": ["SUBJECT", "DESCRIPTION", "PUBLISHER", "RIGHTS"],
              "calibre": ["FILE-AS", "CALVER", "SORT", "COLUMN", "XHTML-AUTHOR",
                          "NCXGEN", "DOCAUTHOR", "BOOKMARK", "APPLEID"],
              "sigil": ["TOOL"], "indesign": ["TOOL"]}[shape]
    out = [secret(n, variant).encode() for n in names]
    out.append(uuid_for(variant).encode())                  # the per-export ID
    if shape == "calibre":
        out.append(uuid_for(variant, "library").encode())
    return out


def members(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {i.filename: z.read(i) for i in z.infolist()}
