"""Word-shaped DOCX corpus (`docs/word_plan.md`): the parts and elements Microsoft
Word writes that the Phase 3 producers (LibreOffice, textutil, MAT2, a synthetic
writer) never did, each planted, wired the way Word wires them -- relationships and
content types included, so a dropped part must leave a consistent package.

Built per locus so a test can ask for one and know what it planted. Planted values
derive from `variant` at a fixed length. Imports nothing from `src`.
"""
from __future__ import annotations

import io
import struct
import zipfile

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_R = "http://schemas.openxmlformats.org/package/2006/relationships"
WE = "http://schemas.microsoft.com/office/webextensions/webextension/2010/11"
WETP = "http://schemas.microsoft.com/office/webextensions/taskpanes/2010/11"
REL_TASKPANES = "http://schemas.microsoft.com/office/2011/relationships/webextensiontaskpanes"
REL_WEBEXT = "http://schemas.microsoft.com/office/2011/relationships/webextension"
REL_PRINTER = f"{R}/printerSettings"
REL_SETTINGS = f"{R}/settings"
REL_ACTIVEX = f"{R}/control"


def secret(name: str, variant: int = 0) -> str:
    return f"SENTINEL-{name}-V{variant:02d}"


def _doc(body: str) -> str:
    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<w:document xmlns:w="{W}" xmlns:r="{R}"><w:body>{body}'
            '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/></w:sectPr></w:body>'
            '</w:document>')


def paragraph(text: str, extra: str = "") -> str:
    return f'<w:p><w:r><w:t xml:space="preserve">{text}</w:t></w:r>{extra}</w:p>'


def formatting_change(variant: int = 0) -> str:
    """A run made bold with change tracking on: Word records the old properties,
    who changed them and when."""
    return ('<w:r><w:rPr><w:b/><w:rPrChange w:id="9" '
            f'w:author="{secret("REVIEWER", variant)}" '
            f'w:date="2026-01-0{1 + variant % 9}T09:00:00Z"><w:rPr/></w:rPrChange>'
            '</w:rPr><w:t>bold now</w:t></w:r>')


def permission(variant: int = 0) -> str:
    """An editing-permission range ("allow only this person to edit here")."""
    return (f'<w:permStart w:id="5" w:ed="{secret("EDITOR", variant)}@example.com"/>'
            '<w:r><w:t>editable part</w:t></w:r><w:permEnd w:id="5"/>')


def settings(variant: int = 0) -> str:
    v = variant
    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<w:settings xmlns:w="{W}">'
            '<w:writeProtection w:recommended="1" w:cryptProviderType="rsaAES" '
            'w:cryptAlgorithmClass="hash" w:cryptAlgorithmType="typeAny" '
            'w:cryptAlgorithmSid="14" w:cryptSpinCount="100000" '
            f'w:hash="{secret("HASH", v)}" w:salt="{secret("SALT", v)}"/>'
            '<w:defaultTabStop w:val="720"/>'
            '<w:docVars>'
            f'<w:docVar w:name="ClientMatter" w:val="{secret("MATTER", v)}"/>'
            f'<w:docVar w:name="Typist" w:val="{secret("TYPIST", v)}"/>'
            '</w:docVars>'
            '<w:compat><w:compatSetting w:name="compatibilityMode" '
            'w:uri="http://schemas.microsoft.com/office/word" w:val="15"/></w:compat>'
            '</w:settings>')


def taskpanes() -> str:
    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<wetp:taskpanes xmlns:wetp="{WETP}" xmlns:r="{R}">'
            '<wetp:taskpane dockstate="right" visibility="0" width="350" row="0">'
            '<wetp:webextensionref r:id="rId1"/></wetp:taskpane></wetp:taskpanes>')


def webextension(variant: int = 0) -> str:
    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<we:webextension xmlns:we="{WE}" id="{{00000000-0000-0000-0000-'
            f'0000000000{variant:02d}}}">'
            '<we:reference id="wa104381727" version="1.0.0.0" store="en-US" '
            'storeType="OMEX"/><we:properties>'
            f'<we:property name="lastUser" value="&quot;{secret("ADDIN", variant)}'
            '&quot;"/></we:properties><we:bindings/><we:snapshot/></we:webextension>')


def printer_settings(variant: int = 0) -> bytes:
    """A DEVMODE: the printer's name in UTF-16, 32 characters, then the driver's
    fields."""
    name = secret("PRINTER", variant).encode("utf-16-le").ljust(64, b"\x00")
    return name + struct.pack("<HHHHI", 0x0401, 0, 156, 0, 0) + bytes(140)


REL_IMAGE = f"{R}/image"


def pictures(variant: int = 0) -> dict[str, bytes]:
    """Pictures Word holds in `word/media/`, each from its own format's corpus with
    that corpus's planted values (W2: cleaned by their own handlers, by content)."""
    from tests.scrub import gif_corpus, svg_corpus, tiff_corpus, webp_corpus
    return {"image1.gif": gif_corpus.build(variant),
            "image2.tif": tiff_corpus.build(variant),
            "image3.webp": webp_corpus.build(variant),
            "image4.svg": svg_corpus.build(variant)}


def emf(variant: int = 0) -> bytes:
    """An EMF header record whose description names the application and the file,
    as Office writes it: no handler exists, so the document is refused."""
    desc = f"Microsoft Office\x00{secret('EMF', variant)}.emf\x00\x00".encode("utf-16-le")
    header = (struct.pack("<II", 1, 108 + len(desc)) + bytes(32)
              + struct.pack("<IIIHH", 0x464D4520, 0x10000, 108 + len(desc), 1, 0)
              + struct.pack("<II", len(desc) // 2, 108) + bytes(24))
    return header[:108] + desc


def template_settings(variant: int = 0) -> tuple[str, str]:
    """settings.xml naming its template and a mail-merge source by relationship, and
    the relationships holding the paths -- measured in 24 and 3 of 784 real Word
    documents, where F1 removed the elements and kept the paths."""
    user = secret("USER", variant)
    settings_xml = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                    f'<w:settings xmlns:w="{W}" xmlns:r="{R}">'
                    '<w:attachedTemplate r:id="rId1"/>'
                    '<w:mailMerge><w:mainDocumentType w:val="formLetters"/>'
                    '<w:dataSource r:id="rId2"/></w:mailMerge>'
                    '<w:defaultTabStop w:val="720"/></w:settings>')
    rels = _rels([("rId1", f"{R}/attachedTemplate",
                   f"file:///C:\\Users\\{user}\\AppData\\Roaming\\Microsoft\\"
                   "Templates\\Letter.dotm"),
                  ("rId2", f"{R}/mailMergeSource",
                   f"file:///C:\\Users\\{user}\\Documents\\clients.xlsx")])
    rels = rels.replace('"/>', '" TargetMode="External"/>')
    return settings_xml, rels


def drawing(variant: int = 0) -> str:
    """A picture as Word inserts it: the file's own name in `pic:cNvPr name`, its
    full path in `wp:docPr descr` (both measured in real documents), embedded AND
    linked (`r:embed` + `r:link`); and a VML picture with `o:title`/`o:href`."""
    path = f"C:\\Users\\{secret('USER', variant)}\\Pictures\\IMG_2034.JPG"
    a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    pic = "http://schemas.openxmlformats.org/drawingml/2006/picture"
    wp = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
    return ('<w:r><w:drawing>'
            f'<wp:inline xmlns:wp="{wp}"><wp:extent cx="952500" cy="952500"/>'
            f'<wp:docPr id="1" name="Picture 1" descr="{path}"/>'
            f'<a:graphic xmlns:a="{a}"><a:graphicData uri="{pic}">'
            f'<pic:pic xmlns:pic="{pic}"><pic:nvPicPr>'
            '<pic:cNvPr id="0" name="IMG_2034.JPG"/><pic:cNvPicPr/></pic:nvPicPr>'
            '<pic:blipFill><a:blip r:embed="rId20" r:link="rId21"/></pic:blipFill>'
            '</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>'
            '<w:r><w:pict><v:shape xmlns:v="urn:schemas-microsoft-com:vml" '
            'xmlns:o="urn:schemas-microsoft-com:office:office" id="s1" '
            'style="width:10pt;height:10pt" alt="A short caption">'
            f'<v:imagedata r:id="rId20" o:title="IMG_2034" o:href="file:///{path}"/>'
            '</v:shape></w:pict></w:r>')


def goback() -> str:
    """Word's cursor bookmark: collapsed, start next to end, at the last cursor."""
    return ('<w:bookmarkStart w:id="0" w:name="_GoBack"/><w:bookmarkEnd w:id="0"/>'
            '<w:r><w:t>after the cursor</w:t></w:r>')


LOCI = ("formatting_change", "permission", "settings", "webextensions",
        "printer_settings", "activex", "pictures", "emf", "template", "drawing",
        "goback")


def build(variant: int = 0, *, loci: tuple[str, ...] = LOCI) -> bytes:
    body = paragraph("A document written in Word.")
    if "formatting_change" in loci:
        body += f"<w:p>{formatting_change(variant)}</w:p>"
    if "permission" in loci:
        body += f"<w:p>{permission(variant)}</w:p>"
    if "drawing" in loci:
        body += f"<w:p>{drawing(variant)}</w:p>"
    if "goback" in loci:
        body += f"<w:p>{goback()}</w:p>"
    body += paragraph("Its last paragraph.")

    parts: dict[str, bytes] = {"word/document.xml": _doc(body).encode()}
    overrides = [("/word/document.xml", "application/vnd.openxmlformats-officedocument"
                  ".wordprocessingml.document.main+xml")]
    doc_rels: list[tuple[str, str, str]] = []
    pkg_rels = [("rId1", f"{R}/officeDocument", "word/document.xml")]
    defaults = [("rels", "application/vnd.openxmlformats-package.relationships+xml"),
                ("xml", "application/xml")]

    if "template" in loci:
        settings_xml, rels = template_settings(variant)
        parts["word/settings.xml"] = settings_xml.encode()
        parts["word/_rels/settings.xml.rels"] = rels.encode()
        overrides.append(("/word/settings.xml", "application/vnd.openxmlformats-"
                          "officedocument.wordprocessingml.settings+xml"))
        doc_rels.append(("rId2", REL_SETTINGS, "settings.xml"))
    if "drawing" in loci:
        from tests.scrub import zip_corpus
        parts["word/media/image20.jpeg"] = zip_corpus.photo(variant)
        doc_rels.append(("rId20", REL_IMAGE, "media/image20.jpeg"))
        doc_rels.append(("rId21", REL_IMAGE,
                         f"file:///C:\\Users\\{secret('USER', variant)}\\Pictures\\"
                         "IMG_2034.JPG\" TargetMode=\"External"))
        defaults.append(("jpeg", "image/jpeg"))
    if "settings" in loci:
        parts["word/settings.xml"] = settings(variant).encode()
        overrides.append(("/word/settings.xml", "application/vnd.openxmlformats-"
                          "officedocument.wordprocessingml.settings+xml"))
        doc_rels.append(("rId2", REL_SETTINGS, "settings.xml"))
    if "webextensions" in loci:
        parts["word/webextensions/taskpanes.xml"] = taskpanes().encode()
        parts["word/webextensions/webextension1.xml"] = webextension(variant).encode()
        parts["word/webextensions/_rels/taskpanes.xml.rels"] = _rels(
            [("rId1", REL_WEBEXT, "webextension1.xml")]).encode()
        overrides += [("/word/webextensions/taskpanes.xml",
                       "application/vnd.ms-office.webextensiontaskpanes+xml"),
                      ("/word/webextensions/webextension1.xml",
                       "application/vnd.ms-office.webextension+xml")]
        pkg_rels.append(("rId9", REL_TASKPANES, "word/webextensions/taskpanes.xml"))
    if "printer_settings" in loci:
        parts["word/printerSettings/printerSettings1.bin"] = printer_settings(variant)
        defaults.append(("bin", "application/vnd.openxmlformats-officedocument."
                                "wordprocessingml.printerSettings"))
        doc_rels.append(("rId3", REL_PRINTER, "printerSettings/printerSettings1.bin"))
    if "activex" in loci:
        parts["word/activeX/activeX1.xml"] = (
            b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            b'<ax:ocx xmlns:ax="http://schemas.microsoft.com/office/2006/activeX" '
            b'ax:classid="{8BD21D40-EC42-11CE-9E0D-00AA006002F3}" '
            b'ax:persistence="persistStorage"/>')
        overrides.append(("/word/activeX/activeX1.xml",
                          "application/vnd.ms-office.activeX+xml"))
        doc_rels.append(("rId4", REL_ACTIVEX, "activeX/activeX1.xml"))

    if "pictures" in loci or "emf" in loci:
        media = dict(pictures(variant)) if "pictures" in loci else {}
        if "emf" in loci:
            media["image9.emf"] = emf(variant)
        for k, (name, body) in enumerate(sorted(media.items()), start=10):
            parts[f"word/media/{name}"] = body
            doc_rels.append((f"rId{k}", REL_IMAGE, f"media/{name}"))
        for ext, ctype in (("gif", "image/gif"), ("tif", "image/tiff"),
                           ("webp", "image/webp"), ("svg", "image/svg+xml"),
                           ("emf", "image/x-emf")):
            if any(n.endswith("." + ext) for n in media):
                defaults.append((ext, ctype))

    parts["_rels/.rels"] = _rels(pkg_rels).encode()
    if doc_rels:
        parts["word/_rels/document.xml.rels"] = _rels(doc_rels).encode()
    parts["[Content_Types].xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        + "".join(f'<Default Extension="{e}" ContentType="{t}"/>' for e, t in defaults)
        + "".join(f'<Override PartName="{n}" ContentType="{t}"/>' for n, t in overrides)
        + "</Types>").encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(parts, key=lambda n: (n != "[Content_Types].xml", n)):
            z.writestr(name, parts[name])
    return buf.getvalue()


def _rels(items: list[tuple[str, str, str]]) -> str:
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<Relationships xmlns="{PKG_R}">'
            + "".join(f'<Relationship Id="{i}" Type="{t}" Target="{tgt}"/>'
                      for i, t, tgt in items)
            + "</Relationships>")


def planted(variant: int = 0, locus: str = "settings") -> list[bytes]:
    names = {"formatting_change": ["REVIEWER"], "permission": ["EDITOR"],
             "settings": ["HASH", "SALT", "MATTER", "TYPIST"],
             "webextensions": ["ADDIN"], "printer_settings": [], "activex": [],
             "pictures": [], "emf": [], "template": ["USER"], "drawing": ["USER"],
             "goback": []}[locus]
    out = [secret(n, variant).encode() for n in names]
    if locus == "printer_settings":
        out.append(secret("PRINTER", variant).encode("utf-16-le"))
    return out


def members(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {n: z.read(n) for n in z.namelist()}
