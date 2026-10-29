"""Excel-shaped XLSX corpus (`docs/office_plan.md` §3): every locus the O0 probe
planted, wired the way Excel wires it -- relationships and content types included,
so a dropped part must leave a consistent package.

Built per locus so a test can ask for one and know what it planted. Planted values
derive from `variant` at a fixed length; the CONTENT (cells, strings shown, the
picture's pixels) is the same in every variant, so two variants differ only in
metadata -- the differential test's premise. Imports nothing from `src`.
"""
from __future__ import annotations

import io
import zipfile

from tests.scrub import zip_corpus

X = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_R = "http://schemas.openxmlformats.org/package/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
XDR = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
SML = "application/vnd.openxmlformats-officedocument.spreadsheetml"
HDR = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'

# F1 removes these; each planted value must be gone from the output.
REMOVED = ("properties", "abspath", "revision_ids", "protection", "window",
           "selection", "custom_views", "orphan_string", "printer_settings",
           "picture", "persons", "pivot_definition", "xml_comment")
# F1 keeps these (content, or a visible link), and reports them.
KEPT = ("hidden_sheet", "hidden_rows", "header", "hyperlink", "hidden_name")
# F1 refuses these.
REFUSED = ("comments", "threaded", "extlink", "pivot_records", "connections",
           "power_query", "revision_log")
LOCI = REMOVED + KEPT


def secret(name: str, variant: int = 0) -> str:
    return f"SENTINEL-{name}-V{variant:02d}"


def guid(name: str, variant: int = 0) -> str:
    """A GUID-shaped planted value: `{SENTINEL-NAME-V01}` is what the tests look
    for; Excel would write hex, which nothing here parses."""
    return "{" + secret(name, variant) + "}"


def build(variant: int = 0, *, loci: tuple[str, ...] = LOCI) -> bytes:
    v = variant
    parts: dict[str, str | bytes] = {}
    overrides = [("/xl/workbook.xml", SML + ".sheet.main+xml"),
                 ("/xl/worksheets/sheet1.xml", SML + ".worksheet+xml"),
                 ("/xl/sharedStrings.xml", SML + ".sharedStrings+xml"),
                 ("/xl/styles.xml", SML + ".styles+xml")]
    defaults = [("rels", "application/vnd.openxmlformats-package.relationships+xml"),
                ("xml", "application/xml")]
    pkg_rels = [("rId1", f"{R}/officeDocument", "xl/workbook.xml")]
    wb_rels = [("rId1", f"{R}/worksheet", "worksheets/sheet1.xml"),
               ("rId3", f"{R}/styles", "styles.xml"),
               ("rId4", f"{R}/sharedStrings", "sharedStrings.xml")]
    sheet_rels: list[tuple] = []

    # ---- the content: the same in every variant
    strings = ["Quarterly total", "Region"]
    if "hidden_sheet" in loci:
        strings.append("Text on a hidden sheet")
    if "orphan_string" in loci:
        strings.append(secret("DELETED-TEXT", v))     # no cell uses it
    sheet_data = ('<sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1"><v>42</v>'
                  '</c><c r="C1"><f>B1*2</f><v>84</v></c></row>'
                  '<row r="2"' + (' hidden="1"' if "hidden_rows" in loci else "")
                  + '><c r="A2" t="s"><v>1</v></c></row></sheetData>')

    # ---- workbook.xml
    ns = (f'xmlns="{X}" xmlns:r="{R}" '
          'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
          'mc:Ignorable="x15 xr xr6 xr10 xr2" '
          'xmlns:x15="http://schemas.microsoft.com/office/spreadsheetml/2010/11/main" '
          'xmlns:xr="http://schemas.microsoft.com/office/spreadsheetml/2014/revision" '
          'xmlns:xr6="http://schemas.microsoft.com/office/spreadsheetml/2016/revision6" '
          'xmlns:xr10="http://schemas.microsoft.com/office/spreadsheetml/2016/revision10" '
          'xmlns:xr2="http://schemas.microsoft.com/office/spreadsheetml/2015/revision2"')
    wb = [f'<workbook {ns}>',
          '<fileVersion appName="xl" lastEdited="7" lowestEdited="7" rupBuild="27425"/>',
          '<workbookPr defaultThemeVersion="202300"/>']
    if "abspath" in loci:
        wb.append('<mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/'
                  'markup-compatibility/2006"><mc:Choice Requires="x15">'
                  '<x15ac:absPath url="C:\\Users\\' + secret("USER", v) + '\\Documents\\" '
                  'xmlns:x15ac="http://schemas.microsoft.com/office/spreadsheetml/2010/11/ac"/>'
                  '</mc:Choice></mc:AlternateContent>')
    if "revision_ids" in loci:
        wb.append(f'<xr:revisionPtr revIDLastSave="0" documentId="8_{guid("DOCID", v)}" '
                  'xr6:coauthVersionLast="47" xr6:coauthVersionMax="47" '
                  f'xr10:uidLastSave="{guid("LASTSAVE", v)}"/>')
    if "protection" in loci:
        wb.append('<workbookProtection workbookAlgorithmName="SHA-512" '
                  f'workbookHashValue="{secret("HASH", v)}" '
                  f'workbookSaltValue="{secret("SALT", v)}" workbookSpinCount="100000" '
                  'lockStructure="1"/>')
        wb.append(f'<fileSharing readOnlyRecommended="1" userName="{secret("RESERVER", v)}"/>')
    view = '<workbookView'
    if "window" in loci:
        view += (f' xWindow="-1{v}" yWindow="-1{v}" windowWidth="3862{v}" '
                 f'windowHeight="2110{v}"')
    if "revision_ids" in loci:
        view += f' xr2:uid="{guid("VIEW", v)}"'
    wb.append('<bookViews>' + view + '/></bookViews>')
    if "custom_views" in loci:
        wb.append('<customWorkbookViews><customWorkbookView name="'
                  + secret("PERSONAL-VIEW", v) + '" guid="' + guid("CVIEW", v)
                  + '" windowWidth="800" windowHeight="600" activeSheetId="1"/>'
                  '</customWorkbookViews>')
    sheets = '<sheet name="Summary" sheetId="1" r:id="rId1"/>'
    if "hidden_sheet" in loci:
        sheets += '<sheet name="Workings" sheetId="2" state="hidden" r:id="rId2"/>'
        wb_rels.append(("rId2", f"{R}/worksheet", "worksheets/sheet2.xml"))
        overrides.append(("/xl/worksheets/sheet2.xml", SML + ".worksheet+xml"))
        parts["xl/worksheets/sheet2.xml"] = (
            HDR + f'<worksheet xmlns="{X}"><sheetData><row r="1"><c r="A1" t="s"><v>2</v>'
            '</c></row></sheetData></worksheet>')
    wb.append(f'<sheets>{sheets}</sheets>')
    if "extlink" in loci:
        wb.append('<externalReferences><externalReference r:id="rId6"/></externalReferences>')
        wb_rels.append(("rId6", f"{R}/externalLink", "externalLinks/externalLink1.xml"))
        overrides.append(("/xl/externalLinks/externalLink1.xml", SML + ".externalLink+xml"))
        parts["xl/externalLinks/externalLink1.xml"] = (
            HDR + f'<externalLink xmlns="{X}" xmlns:r="{R}"><externalBook r:id="rId1">'
            '<sheetNames><sheetName val="Salaries"/></sheetNames><sheetDataSet>'
            '<sheetData sheetId="0"><row r="1"><cell r="A1" t="str"><v>'
            + secret("OTHER-BOOK-CELL", v) + '</v></cell></row></sheetData></sheetDataSet>'
            '</externalBook></externalLink>')
        parts["xl/externalLinks/_rels/externalLink1.xml.rels"] = _rels([(
            "rId1", f"{R}/externalLinkPath",
            "file:///C:\\Users\\" + secret("USER", v) + "\\Budget.xlsx", True)])
    if "hidden_name" in loci:
        wb.append('<definedNames><definedName name="_xlnm._FilterDatabase" '
                  'localSheetId="0" hidden="1">Summary!$A$1:$C$2</definedName></definedNames>')
    wb.append('<calcPr calcId="191029"/>')
    if "pivot_definition" in loci or "pivot_records" in loci:
        wb.append('<pivotCaches><pivotCache cacheId="1" r:id="rId7"/></pivotCaches>')
        wb_rels.append(("rId7", f"{R}/pivotCacheDefinition",
                        "pivotCache/pivotCacheDefinition1.xml"))
        overrides.append(("/xl/pivotCache/pivotCacheDefinition1.xml",
                          SML + ".pivotCacheDefinition+xml"))
        records = "pivot_records" in loci
        parts["xl/pivotCache/pivotCacheDefinition1.xml"] = (
            HDR + f'<pivotCacheDefinition xmlns="{X}" xmlns:r="{R}"'
            + (' r:id="rId1"' if records else ' saveData="0"')
            + f' refreshedBy="{secret("REFRESHER", v)}" refreshedDate="4400{v}.5" '
            'createdVersion="8" refreshedVersion="8" recordCount="1">'
            '<cacheSource type="worksheet"><worksheetSource ref="A1:A2" sheet="Summary"/>'
            '</cacheSource><cacheFields count="1"><cacheField name="Region" numFmtId="0">'
            '<sharedItems/></cacheField></cacheFields></pivotCacheDefinition>')
        if records:
            parts["xl/pivotCache/_rels/pivotCacheDefinition1.xml.rels"] = _rels([
                ("rId1", f"{R}/pivotCacheRecords", "pivotCacheRecords1.xml")])
            overrides.append(("/xl/pivotCache/pivotCacheRecords1.xml",
                              SML + ".pivotCacheRecords+xml"))
            parts["xl/pivotCache/pivotCacheRecords1.xml"] = (
                HDR + f'<pivotCacheRecords xmlns="{X}" count="1"><r><s v="'
                + secret("SOURCE-ROW", v) + '"/></r></pivotCacheRecords>')
    wb.append('</workbook>')
    if "xml_comment" in loci:
        wb.insert(1, f'<!-- saved by {secret("COMMENTER", v)} -->')
    parts["xl/workbook.xml"] = HDR + "".join(wb)

    # ---- the visible sheet
    sheet = [f'<worksheet xmlns="{X}" xmlns:r="{R}" '
             'xmlns:xr="http://schemas.microsoft.com/office/spreadsheetml/2014/revision"'
             + (f' xr:uid="{guid("SHEET", v)}"' if "revision_ids" in loci else "") + '>',
             '<sheetViews><sheetView tabSelected="1" workbookViewId="0">'
             + (f'<selection activeCell="C{v + 3}" sqref="C{v + 3}"/>'
                if "selection" in loci else "") + '</sheetView></sheetViews>']
    if "hidden_rows" in loci:
        sheet.append('<cols><col min="4" max="4" width="9" hidden="1"/></cols>')
    sheet.append(sheet_data)
    if "protection" in loci:
        sheet.append(f'<sheetProtection algorithmName="SHA-512" hashValue="{secret("SHEETHASH", v)}" '
                     f'saltValue="{secret("SHEETSALT", v)}" spinCount="100000" sheet="1"/>')
    if "custom_views" in loci:
        sheet.append(f'<customSheetViews><customSheetView guid="{guid("CSVIEW", v)}">'
                     '<selection activeCell="A1" sqref="A1"/></customSheetView></customSheetViews>')
    if "hyperlink" in loci:
        sheet.append('<hyperlinks><hyperlink ref="B1" r:id="rId6"/></hyperlinks>')
        sheet_rels.append(("rId6", f"{R}/hyperlink",
                           "file:///\\\\fileserver\\finance\\q3.xlsx", True))
    if "printer_settings" in loci:
        sheet.append('<pageSetup orientation="portrait" r:id="rId4"/>')
        sheet_rels.append(("rId4", f"{R}/printerSettings",
                           "../printerSettings/printerSettings1.bin"))
        parts["xl/printerSettings/printerSettings1.bin"] = _devmode(secret("PRINTER", v))
        defaults.append(("bin", SML + ".printerSettings"))
    if "header" in loci:
        sheet.append('<headerFooter><oddHeader>&amp;LPrepared by the finance team</oddHeader>'
                     '</headerFooter>')
    if "picture" in loci:
        sheet.append('<drawing r:id="rId5"/>')
        sheet_rels.append(("rId5", f"{R}/drawing", "../drawings/drawing1.xml"))
        overrides.append(("/xl/drawings/drawing1.xml",
                          "application/vnd.openxmlformats-officedocument.drawing+xml"))
        parts["xl/drawings/drawing1.xml"] = _drawing(v)
        parts["xl/drawings/_rels/drawing1.xml.rels"] = _rels([
            ("rId1", f"{R}/image", "../media/image1.jpeg"),
            ("rId2", f"{R}/image", "file:///C:\\Users\\" + secret("USER", v)
             + "\\Pictures\\IMG_2034.JPG", True)])
        parts["xl/media/image1.jpeg"] = zip_corpus.photo(v)
        defaults.append(("jpeg", "image/jpeg"))
    if "comments" in loci:
        sheet.append('<legacyDrawing r:id="rId2"/>')
        sheet_rels += [("rId1", f"{R}/comments", "../comments1.xml"),
                       ("rId2", f"{R}/vmlDrawing", "../drawings/vmlDrawing1.vml")]
        overrides.append(("/xl/comments1.xml", SML + ".comments+xml"))
        defaults.append(("vml", "application/vnd.openxmlformats-officedocument.vmlDrawing"))
        parts["xl/comments1.xml"] = (
            HDR + f'<comments xmlns="{X}"><authors><author>{secret("COMMENTER", v)}</author>'
            '</authors><commentList><comment ref="A1" authorId="0"><text><r><t>Check this'
            '</t></r></text></comment></commentList></comments>')
        parts["xl/drawings/vmlDrawing1.vml"] = (
            '<xml xmlns:v="urn:schemas-microsoft-com:vml" '
            'xmlns:x="urn:schemas-microsoft-com:office:excel"><v:shape type="#_x0000_t202">'
            '<x:ClientData ObjectType="Note"><x:Row>0</x:Row><x:Column>0</x:Column>'
            '</x:ClientData></v:shape></xml>')
    if "threaded" in loci:
        sheet_rels.append(("rId3", "http://schemas.microsoft.com/office/2017/10/"
                           "relationships/threadedComment",
                           "../threadedComments/threadedComment1.xml"))
        overrides.append(("/xl/threadedComments/threadedComment1.xml",
                          "application/vnd.ms-excel.threadedcomments+xml"))
        parts["xl/threadedComments/threadedComment1.xml"] = (
            HDR + '<ThreadedComments xmlns="http://schemas.microsoft.com/office/'
            'spreadsheetml/2018/threadedcomments"><threadedComment ref="A1" '
            f'dT="2026-01-0{v + 1}T09:00:00.00" personId="{guid("PERSON", v)}" '
            f'id="{guid("THREAD", v)}"><text>Check this</text></threadedComment>'
            '</ThreadedComments>')
    if "persons" in loci or "threaded" in loci:
        wb_rels.append(("rId10", "http://schemas.microsoft.com/office/2017/10/"
                        "relationships/person", "persons/person.xml"))
        overrides.append(("/xl/persons/person.xml", "application/vnd.ms-excel.person+xml"))
        parts["xl/persons/person.xml"] = (
            HDR + '<personList xmlns="http://schemas.microsoft.com/office/spreadsheetml/'
            f'2018/threadedcomments"><person displayName="{secret("PERSON", v)}" '
            f'id="{guid("PERSON", v)}" userId="{secret("EMAIL", v)}@contoso.example" '
            'providerId="AD"/></personList>')
    if "connections" in loci:
        wb_rels.append(("rId8", f"{R}/connections", "connections.xml"))
        overrides.append(("/xl/connections.xml", SML + ".connections+xml"))
        parts["xl/connections.xml"] = (
            HDR + f'<connections xmlns="{X}"><connection id="1" name="db" type="1" '
            'refreshedVersion="8"><dbPr connection="Provider=SQLOLEDB;Data Source='
            + secret("SERVER", v) + '" command="x"/></connection></connections>')
    if "power_query" in loci:
        pkg_rels.append(("rId8", f"{R}/customXml", "customXml/item1.xml"))
        parts["customXml/item1.xml"] = (
            '<?xml version="1.0" encoding="utf-16"?><DataMashup xmlns="http://schemas.'
            'microsoft.com/DataMashup">' + secret("QUERY", v) + '</DataMashup>')
    if "revision_log" in loci:
        wb_rels.append(("rId9", f"{R}/revisionHeaders", "revisions/revisionHeaders.xml"))
        overrides.append(("/xl/revisions/revisionHeaders.xml", SML + ".revisionHeaders+xml"))
        parts["xl/revisions/revisionHeaders.xml"] = (
            HDR + f'<headers xmlns="{X}" guid="{guid("REVLOG", v)}"><header '
            f'guid="{guid("REV", v)}" dateTime="2026-01-01T00:00:00" maxSheetId="2" '
            f'userName="{secret("EDITOR", v)}"/></headers>')
    sheet.append('</worksheet>')
    parts["xl/worksheets/sheet1.xml"] = HDR + "".join(sheet)

    parts["xl/sharedStrings.xml"] = (
        HDR + f'<sst xmlns="{X}" count="2" uniqueCount="{len(strings)}">'
        + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>")
    parts["xl/styles.xml"] = (
        HDR + f'<styleSheet xmlns="{X}"><fonts count="1"><font><sz val="11"/>'
        '<name val="Calibri"/></font></fonts><fills count="2"><fill><patternFill '
        'patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
        '</fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf/>'
        '</cellStyleXfs><cellXfs count="1"><xf xfId="0"/></cellXfs></styleSheet>')

    if "properties" in loci:
        parts.update(_properties(v))
        overrides += [("/docProps/core.xml", "application/vnd.openxmlformats-package."
                       "core-properties+xml"),
                      ("/docProps/app.xml", "application/vnd.openxmlformats-"
                       "officedocument.extended-properties+xml"),
                      ("/docProps/custom.xml", "application/vnd.openxmlformats-"
                       "officedocument.custom-properties+xml")]
        pkg_rels += [("rId2", f"{PKG_R}/metadata/core-properties", "docProps/core.xml"),
                     ("rId3", f"{R}/extended-properties", "docProps/app.xml"),
                     ("rId4", f"{R}/custom-properties", "docProps/custom.xml")]

    parts["_rels/.rels"] = _rels(pkg_rels)
    parts["xl/_rels/workbook.xml.rels"] = _rels(wb_rels)
    if sheet_rels:
        parts["xl/worksheets/_rels/sheet1.xml.rels"] = _rels(sheet_rels)
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


def _properties(v: int) -> dict[str, str]:
    return {
        "docProps/core.xml": HDR + (
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/'
            'metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f'<dc:creator>{secret("AUTHOR", v)}</dc:creator>'
            f'<cp:lastModifiedBy>{secret("EDITOR", v)}</cp:lastModifiedBy>'
            f'<dcterms:created xsi:type="dcterms:W3CDTF">2026-01-0{v + 1}T09:00:00Z'
            '</dcterms:created></cp:coreProperties>'),
        "docProps/app.xml": HDR + (
            '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/'
            'extended-properties"><Application>Microsoft Excel</Application>'
            f'<Company>{secret("COMPANY", v)}</Company>'
            f'<Manager>{secret("MANAGER", v)}</Manager></Properties>'),
        "docProps/custom.xml": HDR + (
            '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/'
            'custom-properties" xmlns:vt="http://schemas.openxmlformats.org/'
            'officeDocument/2006/docPropsVTypes"><property fmtid="{D5CDD505-2E9C-101B-'
            '9397-08002B2CF9AE}" pid="2" name="MSIP_Label_0_SiteId"><vt:lpwstr>'
            + secret("TENANT", v) + '</vt:lpwstr></property></Properties>'),
    }


def _drawing(v: int) -> str:
    return HDR + (
        f'<xdr:wsDr xmlns:xdr="{XDR}" xmlns:a="{A}" xmlns:r="{R}"><xdr:oneCellAnchor>'
        '<xdr:from><xdr:col>4</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>1</xdr:row>'
        '<xdr:rowOff>0</xdr:rowOff></xdr:from><xdr:ext cx="381000" cy="285750"/>'
        '<xdr:pic><xdr:nvPicPr><xdr:cNvPr id="2" name="Picture 1" descr="C:\\Users\\'
        + secret("USER", v) + '\\Pictures\\IMG_2034.JPG"/><xdr:cNvPicPr/></xdr:nvPicPr>'
        '<xdr:blipFill><a:blip r:embed="rId1" r:link="rId2"/><a:stretch><a:fillRect/>'
        '</a:stretch></xdr:blipFill><xdr:spPr><a:prstGeom prst="rect"><a:avLst/>'
        '</a:prstGeom></xdr:spPr></xdr:pic><xdr:clientData/></xdr:oneCellAnchor></xdr:wsDr>')


def _devmode(name: str) -> bytes:
    raw = name.encode("utf-16-le")
    return raw + b"\0" * (64 - len(raw)) + b"\x01\x04\x00\x00" + b"\0" * 140


def _rels(items) -> str:
    out = []
    for item in items:
        rid, rtype, target = item[:3]
        external = len(item) > 3 and item[3]
        out.append(f'<Relationship Id="{rid}" Type="{rtype}" Target="{target}"'
                   + (' TargetMode="External"' if external else "") + "/>")
    return HDR + f'<Relationships xmlns="{PKG_R}">' + "".join(out) + "</Relationships>"


def planted(variant: int = 0, locus: str = "properties") -> list[bytes]:
    """What F1 must remove for `locus` (empty for a kept locus)."""
    names = {
        "properties": ["AUTHOR", "EDITOR", "COMPANY", "MANAGER", "TENANT"],
        "abspath": ["USER"], "revision_ids": ["DOCID", "LASTSAVE", "VIEW", "SHEET"],
        "protection": ["HASH", "SALT", "RESERVER", "SHEETHASH", "SHEETSALT"],
        "window": [], "selection": [], "custom_views": ["PERSONAL-VIEW", "CVIEW", "CSVIEW"],
        "orphan_string": ["DELETED-TEXT"], "printer_settings": [],
        "picture": ["USER", "PHOTO-ARTIST", "PHOTO-SOFT"], "persons": ["PERSON", "EMAIL"],
        "pivot_definition": ["REFRESHER"], "xml_comment": ["COMMENTER"],
    }.get(locus, [])
    out = [secret(n, variant).encode() for n in names]
    if locus == "printer_settings":
        out.append(secret("PRINTER", variant).encode("utf-16-le"))
    return out


def members(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {n: z.read(n) for n in z.namelist()}
