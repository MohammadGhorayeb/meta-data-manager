"""PowerPoint-shaped PPTX corpus (`docs/office_plan.md` §3): every locus the O0
probe planted, wired the way PowerPoint wires it -- relationships and content
types included, so a dropped part must leave a consistent package.

Built per locus so a test can ask for one and know what it planted. Planted values
derive from `variant` at a fixed length; the CONTENT (slide text, geometry, the
picture's pixels) is the same in every variant, so two variants differ only in
metadata. Imports nothing from `src`.
"""
from __future__ import annotations

import io
import zipfile

from tests.scrub import xlsx_corpus, zip_corpus

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_R = "http://schemas.openxmlformats.org/package/2006/relationships"
P14 = "http://schemas.microsoft.com/office/powerpoint/2010/main"
A16 = "http://schemas.microsoft.com/office/drawing/2014/main"
PML = "application/vnd.openxmlformats-officedocument.presentationml"
HDR = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
NS = f'xmlns:a="{A}" xmlns:r="{R}" xmlns:p="{P}"'

# F1 removes these; each planted value must be gone from the output.
REMOVED = ("properties", "thumbnail", "creation_ids", "table_ids", "revision_info",
           "changes_info", "recent_colours", "view_props", "tags", "printer_settings",
           "picture", "comment_authors", "xml_comment")
# F1 keeps these (content, or a visible link), and reports them.
KEPT = ("notes", "hidden_slide", "sections", "hyperlink", "cropped")
# F1 refuses these.
REFUSED = ("legacy_comments", "modern_comments", "embedded_font", "chart_workbook")
LOCI = REMOVED + KEPT

secret = xlsx_corpus.secret
guid = xlsx_corpus.guid


def _a16(name: str, v: int) -> str:
    return (f'<a:extLst><a:ext uri="{{FF2B5EF4-FFF2-40B4-BE49-F238E27FC236}}">'
            f'<a16:creationId xmlns:a16="{A16}" id="{guid(name, v)}"/></a:ext></a:extLst>')


def _grp() -> str:
    return ('<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
            '<p:grpSpPr/>')


def _text_shape(sid: int, name: str, text: str, y: int, extra: str = "") -> str:
    return (f'<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name="{name}">{extra}</p:cNvPr><p:cNvSpPr/>'
            '<p:nvPr/></p:nvSpPr><p:spPr><a:xfrm>'
            f'<a:off x="457200" y="{y}"/><a:ext cx="8229600" cy="1143000"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr><p:txBody><a:bodyPr/>'
            f'<a:lstStyle/><a:p><a:r><a:rPr lang="en-US"/><a:t>{text}</a:t></a:r></a:p>'
            '</p:txBody></p:sp>')


def build(variant: int = 0, *, loci: tuple[str, ...] = LOCI) -> bytes:
    v = variant
    parts: dict[str, str | bytes] = {}
    overrides = [("/ppt/presentation.xml", PML + ".presentation.main+xml"),
                 ("/ppt/slides/slide1.xml", PML + ".slide+xml"),
                 ("/ppt/slideLayouts/slideLayout1.xml", PML + ".slideLayout+xml"),
                 ("/ppt/slideMasters/slideMaster1.xml", PML + ".slideMaster+xml"),
                 ("/ppt/theme/theme1.xml", "application/vnd.openxmlformats-officedocument.theme+xml"),
                 ("/ppt/presProps.xml", PML + ".presProps+xml")]
    defaults = [("rels", "application/vnd.openxmlformats-package.relationships+xml"),
                ("xml", "application/xml")]
    pkg_rels = [("rId1", f"{R}/officeDocument", "ppt/presentation.xml")]
    pres_rels = [("rId1", f"{R}/slideMaster", "slideMasters/slideMaster1.xml"),
                 ("rId2", f"{R}/slide", "slides/slide1.xml"),
                 ("rId4", f"{R}/theme", "theme/theme1.xml"),
                 ("rId9", f"{R}/presProps", "presProps.xml")]
    slide_rels = [("rId1", f"{R}/slideLayout", "../slideLayouts/slideLayout1.xml")]

    # ---- slide 1: the content, the same in every variant
    title_extra = ""
    if "hyperlink" in loci:
        title_extra += '<a:hlinkClick r:id="rId7"/>'
        slide_rels.append(("rId7", f"{R}/hyperlink",
                           "file:///\\\\fileserver\\decks\\q3.pptx", True))
    if "creation_ids" in loci:
        title_extra += _a16("SHAPE", v)
    shapes = _text_shape(2, "Title 1", "Quarterly review", 274638, title_extra)
    if "picture" in loci:
        crop = '<a:srcRect l="25000" r="25000"/>' if "cropped" in loci else ""
        shapes += (
            '<p:pic><p:nvPicPr><p:cNvPr id="3" name="Picture 2" descr="C:\\Users\\'
            + secret("USER", v) + '\\Pictures\\IMG_2034.JPG"'
            + ('>' + _a16("PICTURE", v) + '</p:cNvPr>' if "creation_ids" in loci else "/>")
            + '<p:cNvPicPr/><p:nvPr/></p:nvPicPr><p:blipFill>'
            '<a:blip r:embed="rId2" r:link="rId8"/>' + crop
            + '<a:stretch><a:fillRect/></a:stretch></p:blipFill><p:spPr><a:xfrm>'
            '<a:off x="457200" y="1600200"/><a:ext cx="2000000" cy="1500000"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>')
        slide_rels += [("rId2", f"{R}/image", "../media/image1.jpeg"),
                       ("rId8", f"{R}/image", "file:///C:\\Users\\" + secret("USER", v)
                        + "\\Pictures\\IMG_2034.JPG", True)]
        parts["ppt/media/image1.jpeg"] = zip_corpus.photo(v)
        defaults.append(("jpeg", "image/jpeg"))
    if "table_ids" in loci:
        shapes += (
            '<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="5" name="Table 4"/>'
            '<p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr><p:xfrm>'
            '<a:off x="4572000" y="1600200"/><a:ext cx="4000000" cy="740000"/></p:xfrm>'
            '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table">'
            '<a:tbl><a:tblPr/><a:tblGrid><a:gridCol w="4000000"><a:extLst>'
            '<a:ext uri="{9D8B030D-6E8A-4147-A177-3AD203B41FA5}">'
            f'<a16:colId xmlns:a16="{A16}" val="2000{v:05d}"/></a:ext></a:extLst></a:gridCol>'
            '</a:tblGrid><a:tr h="370000"><a:tc><a:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r>'
            '<a:t>Region</a:t></a:r></a:p></a:txBody><a:tcPr/></a:tc><a:extLst>'
            '<a:ext uri="{0D108BD9-81ED-4DB2-BD59-A6C34878D82A}">'
            f'<a16:rowId xmlns:a16="{A16}" val="1000{v:05d}"/></a:ext></a:extLst></a:tr>'
            '</a:tbl></a:graphicData></a:graphic></p:graphicFrame>')
    if "chart_workbook" in loci:
        shapes += (
            '<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="6" name="Chart 5"/>'
            '<p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr><p:xfrm>'
            '<a:off x="4572000" y="3600200"/><a:ext cx="4000000" cy="2000000"/></p:xfrm>'
            '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/chart">'
            '<c:chart xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" r:id="rId6"/>'
            '</a:graphicData></a:graphic></p:graphicFrame>')
        slide_rels.append(("rId6", f"{R}/chart", "../charts/chart1.xml"))
        overrides.append(("/ppt/charts/chart1.xml",
                          "application/vnd.openxmlformats-officedocument.drawingml.chart+xml"))
        parts["ppt/charts/chart1.xml"] = _chart()
        parts["ppt/charts/_rels/chart1.xml.rels"] = _rels([
            ("rId1", f"{R}/package", "../embeddings/Microsoft_Excel_Worksheet1.xlsx")])
        parts["ppt/embeddings/Microsoft_Excel_Worksheet1.xlsx"] = xlsx_corpus.build(v)
        defaults.append(("xlsx", "application/vnd.openxmlformats-officedocument."
                                 "spreadsheetml.sheet"))
    slide_ext = ""
    if "creation_ids" in loci:
        slide_ext = ('<p:extLst><p:ext uri="{BB962C8B-B14F-4D97-AF65-F5344CB8AC3E}">'
                     f'<p14:creationId xmlns:p14="{P14}" val="{3141590000 + v}"/></p:ext>'
                     '</p:extLst>')
    parts["ppt/slides/slide1.xml"] = (
        HDR + f'<p:sld {NS}><p:cSld><p:spTree>{_grp()}{shapes}</p:spTree></p:cSld>'
        f'<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>{slide_ext}</p:sld>')

    sld_ids = '<p:sldId id="256" r:id="rId2"/>'
    if "hidden_slide" in loci:
        pres_rels.append(("rId3", f"{R}/slide", "slides/slide2.xml"))
        overrides.append(("/ppt/slides/slide2.xml", PML + ".slide+xml"))
        sld_ids += '<p:sldId id="257" r:id="rId3"/>'
        parts["ppt/slides/slide2.xml"] = (
            HDR + f'<p:sld {NS} show="0"><p:cSld><p:spTree>{_grp()}'
            + _text_shape(2, "Text 1", "Draft figures, not for circulation", 274638)
            + '</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>')
        parts["ppt/slides/_rels/slide2.xml.rels"] = _rels([
            ("rId1", f"{R}/slideLayout", "../slideLayouts/slideLayout1.xml")])

    if "notes" in loci:
        slide_rels.append(("rId3", f"{R}/notesSlide", "../notesSlides/notesSlide1.xml"))
        pres_rels.append(("rId11", f"{R}/notesMaster", "notesMasters/notesMaster1.xml"))
        overrides += [("/ppt/notesSlides/notesSlide1.xml", PML + ".notesSlide+xml"),
                      ("/ppt/notesMasters/notesMaster1.xml", PML + ".notesMaster+xml"),
                      ("/ppt/theme/theme2.xml", "application/vnd.openxmlformats-officedocument.theme+xml")]
        parts["ppt/notesMasters/notesMaster1.xml"] = (
            HDR + f'<p:notesMaster {NS}><p:cSld><p:spTree>{_grp()}</p:spTree></p:cSld>'
            + _CLRMAP + '</p:notesMaster>')
        parts["ppt/notesMasters/_rels/notesMaster1.xml.rels"] = _rels([
            ("rId1", f"{R}/theme", "../theme/theme2.xml")])
        parts["ppt/theme/theme2.xml"] = xlsx_corpus_theme()
        parts["ppt/notesSlides/notesSlide1.xml"] = (
            HDR + f'<p:notes {NS}><p:cSld><p:spTree>{_grp()}<p:sp><p:nvSpPr>'
            '<p:cNvPr id="2" name="Notes 1"/><p:cNvSpPr/><p:nvPr><p:ph type="body" idx="1"/>'
            '</p:nvPr></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r>'
            '<a:t>Remember to mention the layoffs</a:t></a:r></a:p></p:txBody></p:sp>'
            '</p:spTree></p:cSld></p:notes>')
        parts["ppt/notesSlides/_rels/notesSlide1.xml.rels"] = _rels([
            ("rId1", f"{R}/notesMaster", "../notesMasters/notesMaster1.xml"),
            ("rId2", f"{R}/slide", "../slides/slide1.xml")])

    pres = [f'<p:presentation {NS} saveSubsetFonts="1">',
            '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>']
    if "notes" in loci:
        pres.append('<p:notesMasterIdLst><p:notesMasterId r:id="rId11"/></p:notesMasterIdLst>')
    pres.append(f'<p:sldIdLst>{sld_ids}</p:sldIdLst>')
    pres.append('<p:sldSz cx="9144000" cy="6858000"/><p:notesSz cx="6858000" cy="9144000"/>')
    if "embedded_font" in loci:
        pres.append('<p:embeddedFontLst><p:embeddedFont><p:font typeface="Brand Sans"/>'
                    '<p:regular r:id="rId14"/></p:embeddedFont></p:embeddedFontLst>')
        pres_rels.append(("rId14", f"{R}/font", "fonts/font1.fntdata"))
        defaults.append(("fntdata", "application/x-fontdata"))
        parts["ppt/fonts/font1.fntdata"] = b"\x00\x01\x00\x00" + secret("FONT", v).encode()
    if "tags" in loci:
        pres.append('<p:custDataLst><p:tags r:id="rId13"/></p:custDataLst>')
        pres_rels.append(("rId13", f"{R}/tags", "tags/tag1.xml"))
        overrides.append(("/ppt/tags/tag1.xml", PML + ".tags+xml"))
        parts["ppt/tags/tag1.xml"] = (
            HDR + f'<p:tagLst {NS}><p:tag name="ADDIN_USER" val="{secret("ADDIN", v)}"/>'
            '</p:tagLst>')
    if "sections" in loci:
        pres.append('<p:extLst><p:ext uri="{521415D9-36F7-43E2-AB2F-B90AF26B5E84}">'
                    f'<p14:sectionLst xmlns:p14="{P14}"><p14:section name="Results" '
                    'id="{6A1F0E2B-3C4D-4E5F-8A9B-0C1D2E3F4A5B}"><p14:sldIdLst>'
                    '<p14:sldId id="256"/></p14:sldIdLst></p14:section></p14:sectionLst>'
                    '</p:ext></p:extLst>')
    pres.append('</p:presentation>')
    if "xml_comment" in loci:
        pres.insert(1, f'<!-- saved by {secret("COMMENTER", v)} -->')
    parts["ppt/presentation.xml"] = HDR + "".join(pres)

    parts["ppt/presProps.xml"] = (
        HDR + f'<p:presentationPr {NS}>'
        + (f'<p:clrMru><a:srgbClr val="ABC{v:03d}"/></p:clrMru>'
           if "recent_colours" in loci else "") + '</p:presentationPr>')
    if "view_props" in loci:
        pres_rels.append(("rId10", f"{R}/viewProps", "viewProps.xml"))
        overrides.append(("/ppt/viewProps.xml", PML + ".viewProps+xml"))
        parts["ppt/viewProps.xml"] = (
            HDR + f'<p:viewPr {NS} lastView="sldThumbnailView"><p:normalViewPr>'
            f'<p:restoredLeft sz="{15000 + v}"/><p:restoredTop sz="94660"/></p:normalViewPr>'
            '</p:viewPr>')
    if "revision_info" in loci:
        pres_rels.append(("rId7", "http://schemas.microsoft.com/office/2015/10/"
                          "relationships/revisionInfo", "revisionInfo.xml"))
        overrides.append(("/ppt/revisionInfo.xml", "application/vnd.ms-powerpoint.revisioninfo+xml"))
        parts["ppt/revisionInfo.xml"] = (
            HDR + '<p1510:revInfo xmlns:p1510="http://schemas.microsoft.com/office/'
            f'powerpoint/2015/10/main"><p1510:revLst><p1510:client id="{guid("CLIENT", v)}" '
            'v="2"/></p1510:revLst></p1510:revInfo>')
    if "changes_info" in loci:
        pres_rels.append(("rId8", "http://schemas.microsoft.com/office/2016/11/"
                          "relationships/changesInfo", "changesInfos/changesInfo1.xml"))
        overrides.append(("/ppt/changesInfos/changesInfo1.xml",
                          "application/vnd.ms-powerpoint.changesinfo+xml"))
        parts["ppt/changesInfos/changesInfo1.xml"] = (
            HDR + '<pc:chgInfo xmlns:pc="http://schemas.microsoft.com/office/powerpoint/'
            f'2013/main/command"><pc:docChgLst><pc:chgData name="{secret("COAUTHOR", v)}" '
            f'userId="S::{secret("EMAIL", v)}@contoso.example::{guid("ACCOUNT", v)}" '
            'providerId="AD"/></pc:docChgLst></pc:chgInfo>')
    if "printer_settings" in loci:
        pres_rels.append(("rId12", f"{R}/printerSettings",
                          "printerSettings/printerSettings1.bin"))
        defaults.append(("bin", PML + ".printerSettings"))
        parts["ppt/printerSettings/printerSettings1.bin"] = xlsx_corpus._devmode(
            secret("PRINTER", v))
    if "comment_authors" in loci or "legacy_comments" in loci:
        pres_rels.append(("rId5", f"{R}/commentAuthors", "commentAuthors.xml"))
        overrides.append(("/ppt/commentAuthors.xml", PML + ".commentAuthors+xml"))
        parts["ppt/commentAuthors.xml"] = (
            HDR + f'<p:cmAuthorLst {NS}><p:cmAuthor id="1" name="{secret("REVIEWER", v)}" '
            'initials="RV" lastIdx="1" clrIdx="0"/></p:cmAuthorLst>')
    if "comment_authors" in loci or "modern_comments" in loci:
        pres_rels.append(("rId6", "http://schemas.microsoft.com/office/2018/10/"
                          "relationships/authors", "authors.xml"))
        overrides.append(("/ppt/authors.xml", "application/vnd.ms-powerpoint.authors+xml"))
        parts["ppt/authors.xml"] = (
            HDR + '<p188:authorLst xmlns:p188="http://schemas.microsoft.com/office/'
            f'powerpoint/2018/8/main"><p188:author id="{guid("AUTHOR", v)}" '
            f'name="{secret("AUTHOR", v)}" initials="AU" userId="{secret("EMAIL", v)}'
            '@contoso.example" providerId="AD"/></p188:authorLst>')
    if "legacy_comments" in loci:
        slide_rels.append(("rId4", f"{R}/comments", "../comments/comment1.xml"))
        overrides.append(("/ppt/comments/comment1.xml", PML + ".comments+xml"))
        parts["ppt/comments/comment1.xml"] = (
            HDR + f'<p:cmLst {NS}><p:cm authorId="1" dt="2026-01-01T09:00:00.000" idx="1">'
            '<p:pos x="10" y="10"/><p:text>Check the numbers</p:text></p:cm></p:cmLst>')
    if "modern_comments" in loci:
        slide_rels.append(("rId5", "http://schemas.microsoft.com/office/2018/10/"
                           "relationships/comments", "../comments/modernComment_100_1.xml"))
        overrides.append(("/ppt/comments/modernComment_100_1.xml",
                          "application/vnd.ms-powerpoint.comments+xml"))
        parts["ppt/comments/modernComment_100_1.xml"] = (
            HDR + f'<p188:cmLst xmlns:a="{A}" xmlns:p188="http://schemas.microsoft.com/'
            f'office/powerpoint/2018/8/main"><p188:cm id="{guid("COMMENT", v)}" '
            f'authorId="{guid("AUTHOR", v)}" created="2026-01-01T09:00:00.000">'
            '<p188:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>Check the numbers</a:t>'
            '</a:r></a:p></p188:txBody></p188:cm></p188:cmLst>')

    if "properties" in loci:
        props = xlsx_corpus._properties(v)
        props["docProps/app.xml"] = props["docProps/app.xml"].replace(
            "Microsoft Excel", "Microsoft Office PowerPoint")
        parts.update(props)
        overrides += [("/docProps/core.xml", "application/vnd.openxmlformats-package."
                       "core-properties+xml"),
                      ("/docProps/app.xml", "application/vnd.openxmlformats-"
                       "officedocument.extended-properties+xml"),
                      ("/docProps/custom.xml", "application/vnd.openxmlformats-"
                       "officedocument.custom-properties+xml")]
        pkg_rels += [("rId2", f"{PKG_R}/metadata/core-properties", "docProps/core.xml"),
                     ("rId3", f"{R}/extended-properties", "docProps/app.xml"),
                     ("rId4", f"{R}/custom-properties", "docProps/custom.xml")]
    if "thumbnail" in loci:
        pkg_rels.append(("rId5", f"{PKG_R}/metadata/thumbnail", "docProps/thumbnail.jpeg"))
        parts["docProps/thumbnail.jpeg"] = zip_corpus.photo(v)
        if ("jpeg", "image/jpeg") not in defaults:
            defaults.append(("jpeg", "image/jpeg"))

    parts["ppt/slideLayouts/slideLayout1.xml"] = (
        HDR + f'<p:sldLayout {NS} type="blank"><p:cSld name="Blank"><p:spTree>{_grp()}'
        '</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>')
    parts["ppt/slideLayouts/_rels/slideLayout1.xml.rels"] = _rels([
        ("rId1", f"{R}/slideMaster", "../slideMasters/slideMaster1.xml")])
    parts["ppt/slideMasters/slideMaster1.xml"] = (
        HDR + f'<p:sldMaster {NS}><p:cSld><p:spTree>{_grp()}</p:spTree></p:cSld>'
        + _CLRMAP + '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/>'
        '</p:sldLayoutIdLst></p:sldMaster>')
    parts["ppt/slideMasters/_rels/slideMaster1.xml.rels"] = _rels([
        ("rId1", f"{R}/slideLayout", "../slideLayouts/slideLayout1.xml"),
        ("rId2", f"{R}/theme", "../theme/theme1.xml")])
    parts["ppt/theme/theme1.xml"] = xlsx_corpus_theme()

    parts["_rels/.rels"] = _rels(pkg_rels)
    parts["ppt/_rels/presentation.xml.rels"] = _rels(pres_rels)
    parts["ppt/slides/_rels/slide1.xml.rels"] = _rels(slide_rels)
    parts["[Content_Types].xml"] = (
        HDR + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        + "".join(f'<Default Extension="{e}" ContentType="{t}"/>' for e, t in defaults)
        + "".join(f'<Override PartName="{n}" ContentType="{t}"/>' for n, t in overrides)
        + "</Types>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(parts, key=lambda n: (n != "[Content_Types].xml", n)):
            body = parts[name]
            z.writestr(name, body if isinstance(body, bytes) else body.encode())
    return buf.getvalue()


_CLRMAP = ('<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" '
           'accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" '
           'accent6="accent6" hlink="hlink" folHlink="folHlink"/>')


def xlsx_corpus_theme() -> str:
    colours = [("dk1", "000000"), ("lt1", "FFFFFF"), ("dk2", "44546A"), ("lt2", "E7E6E6"),
               ("accent1", "4472C4"), ("accent2", "ED7D31"), ("accent3", "A5A5A5"),
               ("accent4", "FFC000"), ("accent5", "5B9BD5"), ("accent6", "70AD47"),
               ("hlink", "0563C1"), ("folHlink", "954F72")]
    fill = '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    return HDR + (
        f'<a:theme xmlns:a="{A}" name="Office Theme"><a:themeElements><a:clrScheme name="Office">'
        + "".join(f'<a:{k}><a:srgbClr val="{c}"/></a:{k}>' for k, c in colours)
        + '</a:clrScheme><a:fontScheme name="Office"><a:majorFont><a:latin typeface="Calibri Light"/>'
        '<a:ea typeface=""/><a:cs typeface=""/></a:majorFont><a:minorFont>'
        '<a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont>'
        '</a:fontScheme><a:fmtScheme name="Office"><a:fillStyleLst>' + fill * 3
        + '</a:fillStyleLst><a:lnStyleLst>' + f'<a:ln w="6350">{fill}</a:ln>' * 3
        + '</a:lnStyleLst><a:effectStyleLst>' + '<a:effectStyle><a:effectLst/></a:effectStyle>' * 3
        + '</a:effectStyleLst><a:bgFillStyleLst>' + fill * 3
        + '</a:bgFillStyleLst></a:fmtScheme></a:themeElements></a:theme>')


def _chart() -> str:
    return HDR + (
        '<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
        f'xmlns:a="{A}" xmlns:r="{R}"><c:chart><c:plotArea><c:layout/><c:barChart>'
        '<c:barDir val="col"/><c:grouping val="clustered"/><c:ser><c:idx val="0"/>'
        '<c:order val="0"/><c:val><c:numRef><c:f>Summary!$B$1</c:f><c:numCache>'
        '<c:formatCode>General</c:formatCode><c:ptCount val="1"/><c:pt idx="0"><c:v>42</c:v>'
        '</c:pt></c:numCache></c:numRef></c:val></c:ser><c:axId val="1"/><c:axId val="2"/>'
        '</c:barChart><c:catAx><c:axId val="1"/><c:scaling><c:orientation val="minMax"/>'
        '</c:scaling><c:axPos val="b"/><c:crossAx val="2"/></c:catAx><c:valAx><c:axId val="2"/>'
        '<c:scaling><c:orientation val="minMax"/></c:scaling><c:axPos val="l"/>'
        '<c:crossAx val="1"/></c:valAx></c:plotArea></c:chart>'
        '<c:externalData r:id="rId1"><c:autoUpdate val="0"/></c:externalData></c:chartSpace>')


def _rels(items) -> str:
    return xlsx_corpus._rels(items)


def planted(variant: int = 0, locus: str = "properties") -> list[bytes]:
    """What F1 must remove for `locus` (empty for a kept locus)."""
    names = {
        "properties": ["AUTHOR", "EDITOR", "COMPANY", "MANAGER", "TENANT"],
        "thumbnail": ["PHOTO-ARTIST"], "creation_ids": ["SHAPE"],
        "revision_info": ["CLIENT"], "changes_info": ["COAUTHOR", "EMAIL", "ACCOUNT"],
        "tags": ["ADDIN"], "picture": ["USER", "PHOTO-ARTIST", "PHOTO-SOFT"],
        "comment_authors": ["REVIEWER", "AUTHOR", "EMAIL"], "xml_comment": ["COMMENTER"],
    }.get(locus, [])
    out = [secret(n, variant).encode() for n in names]
    if locus == "printer_settings":
        out.append(secret("PRINTER", variant).encode("utf-16-le"))
    if locus == "creation_ids":
        out.append(str(3141590000 + variant).encode())
    if locus == "table_ids":
        out += [f"2000{variant:05d}".encode(), f"1000{variant:05d}".encode()]
    if locus == "recent_colours":
        out.append(f"ABC{variant:03d}".encode())
    if locus == "view_props":
        out.append(f'sz="{15000 + variant}"'.encode())
    return out


def members(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {n: z.read(n) for n in z.namelist()}
