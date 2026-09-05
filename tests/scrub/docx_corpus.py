"""DOCX producer corpus for the Phase-3 OOXML work items.

Same shape as `pdf_corpus.py`: build one source document through every producer this
machine can actually run, and report the absent ones rather than quietly shrinking the
peer set (limit #12).

Two things differ from the PDF corpus, both because of what DOCX is:

1. **The synthetic producer is written with stdlib `zipfile` on purpose.** For PDF the
   corpus deliberately shares no code with the scrubber, so a shared misunderstanding
   of the format cannot cancel out. Here the point is the opposite: `zipfile` is the
   *candidate writer* for W11, so having it in the peer set is how W8 finds out whether
   its output looks like anybody else's or like nothing else at all.
2. **Microsoft Word cannot be driven from a script.** It is the producer that writes
   RSIDs, so it matters more than any other, and it is reachable only as files a human
   saved. They are discovered from a git-ignored directory, never a hardcoded personal
   path, and their absence is reported — the Apple-AAC precedent (limit #12).

Everything else is deterministic: fixed source text, no wall clock in anything we
write ourselves.
"""
from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import zipfile

HAVE_SOFFICE = shutil.which("soffice") is not None
HAVE_TEXTUTIL = shutil.which("textutil") is not None      # macOS Cocoa text engine
HAVE_MAT2 = shutil.which("mat2") is not None

# Word files are authored by hand and never committed (they carry real personal
# metadata -- which is the whole reason they are interesting). Point this at a
# directory of .docx saved by Word; `tests/corpus/docx/` is the default and is
# git-ignored by the `tests/corpus/**/*.docx` rule.
WORD_DIR = os.environ.get(
    "DOCX_WORD_SAMPLES",
    os.path.join(os.path.dirname(__file__), "..", "corpus", "docx"))


def word_samples() -> list[str]:
    """Word-authored .docx found on this machine, or []. Never bundled."""
    if not os.path.isdir(WORD_DIR):
        return []
    return sorted(p for p in glob.glob(os.path.join(WORD_DIR, "*.docx"))
                  if not os.path.basename(p).startswith("~$"))


HAVE_WORD = bool(word_samples())

SOURCE_TEXT = (
    "Quarterly Review\n\n"
    "This paragraph exists so that every producer has the same words to lay out.\n"
    "A second sentence gives the packager something to compress.\n\n"
    "Prepared for internal circulation only.\n"
)


def _run_quiet(cmd, **kw) -> bool:
    try:
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=180, **kw)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


# --------------------------------------------------------------------------- #
# The synthetic producer: a minimal, valid, deterministic DOCX
# --------------------------------------------------------------------------- #
_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package'
    '.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd'
    '.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '</Types>'
)

_ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
    'relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
    'officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    '</Relationships>'
)

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _document_xml(text: str) -> str:
    paras = "".join(
        f'<w:p><w:r><w:t xml:space="preserve">{line}</w:t></w:r></w:p>'
        for line in text.splitlines() if line.strip())
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<w:document xmlns:w="{_W}"><w:body>{paras}'
            '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/></w:sectPr>'
            '</w:body></w:document>')


def synthetic(path: str, text: str = SOURCE_TEXT, *, compresslevel: int | None = None,
              date_time=(1980, 1, 1, 0, 0, 0)) -> str:
    """A DOCX written through stdlib `zipfile` -- the W11 candidate writer, in the
    peer set so W8 can see what it looks like from outside."""
    parts = [("[Content_Types].xml", _CONTENT_TYPES),
             ("_rels/.rels", _ROOT_RELS),
             ("word/document.xml", _document_xml(text))]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=compresslevel) as z:
        for name, body in parts:
            zi = zipfile.ZipInfo(name, date_time)
            # A bare ZipInfo carries compress_type=ZIP_STORED and SILENTLY OVERRIDES
            # the ZipFile's own compression argument -- the first run of this corpus
            # produced three uncompressed entries while asking for deflate, and the
            # only reason it was caught is that the census counts stored entries.
            # W11 has to set this explicitly on every part.
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, body)
    return path


# --------------------------------------------------------------------------- #
# Real producers
# --------------------------------------------------------------------------- #
def _source_file(tmpdir: str, text: str, tag: str) -> str:
    src = os.path.join(tmpdir, f"src_{tag}.txt")
    with open(src, "w", encoding="utf-8") as f:
        f.write(text)
    return src


def libreoffice(tmpdir: str, text: str = SOURCE_TEXT, tag: str = "d0") -> str | None:
    if not HAVE_SOFFICE:
        return None
    src = _source_file(tmpdir, text, tag)
    out = os.path.join(tmpdir, f"lo_{tag}")
    os.makedirs(out, exist_ok=True)
    # `writer_MS_Word_2007` pins the filter: bare `docx` has picked ODF in some
    # builds, which would silently put an ODT in a DOCX peer set.
    if not _run_quiet(["soffice", "--headless", "--convert-to",
                       "docx:MS Word 2007 XML", "--outdir", out, src]):
        return None
    p = os.path.join(out, os.path.basename(src).replace(".txt", ".docx"))
    return p if os.path.exists(p) else None


def cocoa_textutil(tmpdir: str, text: str = SOURCE_TEXT,
                   tag: str = "d0") -> str | None:
    if not HAVE_TEXTUTIL:
        return None
    src = _source_file(tmpdir, text, tag)
    p = os.path.join(tmpdir, f"textutil_{tag}.docx")
    if not _run_quiet(["textutil", "-convert", "docx", "-output", p, src]):
        return None
    return p if os.path.exists(p) else None


def mat2_of(path: str, out_dir: str) -> str | None:
    """MAT2's output as a producer in its own right -- it rewrites the package with
    its own XML writer and its own ZIP conventions, so it belongs in the peer set as
    well as in the benchmark."""
    if not HAVE_MAT2:
        return None
    dst = os.path.join(out_dir, "mat2_" + os.path.basename(path))
    shutil.copy(path, dst)
    if not _run_quiet(["mat2", "--inplace", dst]):
        return None
    return dst if os.path.exists(dst) else None


def available_producers() -> dict[str, bool]:
    """Which producers this machine can run. An absent one makes a cell say
    *not measured*, never *clean*."""
    return {"synth_zipfile": True, "libreoffice": HAVE_SOFFICE,
            "cocoa_textutil": HAVE_TEXTUTIL, "mat2_out": HAVE_MAT2,
            "msword": HAVE_WORD}


def producers(tmpdir: str, text: str = SOURCE_TEXT) -> dict[str, str]:
    """producer -> one .docx path. Missing producers are simply absent from the dict.

    For W8 (container conventions) the documents need not match across producers --
    ZIP layout is content-independent. The A2 peer set built for W13 holds content
    constant; that is a different builder and a different question.
    """
    out: dict[str, str] = {}
    out["synth_zipfile"] = synthetic(os.path.join(tmpdir, "synth.docx"), text)

    p = libreoffice(tmpdir, text)
    if p:
        out["libreoffice"] = p
    p = cocoa_textutil(tmpdir, text)
    if p:
        out["cocoa_textutil"] = p

    words = word_samples()
    if words:
        out["msword"] = words[0]

    # MAT2 is applied to the most "real" producer available, so its own conventions
    # are measured on a package it had to rewrite rather than one we handed it.
    seed = out.get("msword") or out.get("libreoffice") or out["synth_zipfile"]
    p = mat2_of(seed, tmpdir)
    if p:
        out["mat2_out"] = p
    return out

# --------------------------------------------------------------------------- #
# The torture package: every locus we have a rule for, in one file
# --------------------------------------------------------------------------- #
# `pdf_corpus.torture_pdf()` is the precedent. A rule table is only worth as much as
# the evidence that its rules FIRE, and the real-producer corpus exercises maybe half
# of them -- no producer here writes a tracked change, a comment, a bookmark, an
# embedded image or a sensitivity label. So we write one that does. It is synthetic,
# and it is honest about that: it proves the census can SEE each locus, never that a
# real producer writes it that way.

_SENTINEL = "TORTURE-SENTINEL"

_TORTURE_CORE = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/'
    'metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'xmlns:dcterms="http://purl.org/dc/terms/" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
    f'<dc:title>{_SENTINEL} title</dc:title>'
    f'<dc:creator>{_SENTINEL} author</dc:creator>'
    f'<cp:lastModifiedBy>{_SENTINEL} editor</cp:lastModifiedBy>'
    f'<cp:keywords>{_SENTINEL}</cp:keywords>'
    '<cp:revision>7</cp:revision><cp:category>internal</cp:category>'
    '<dcterms:created xsi:type="dcterms:W3CDTF">2019-03-04T09:11:00Z</dcterms:created>'
    '<dcterms:modified xsi:type="dcterms:W3CDTF">2019-03-04T17:52:00Z</dcterms:modified>'
    '<cp:lastPrinted>2019-03-05T08:00:00Z</cp:lastPrinted>'
    '</cp:coreProperties>')

_TORTURE_APP = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/'
    'extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/'
    '2006/docPropsVTypes">'
    '<Template>\\\\fileserver\\templates\\House.dotm</Template>'
    '<TotalTime>412</TotalTime><Pages>3</Pages><Words>810</Words>'
    f'<Application>{_SENTINEL} Word 99.9</Application><AppVersion>16.0000</AppVersion>'
    f'<Company>{_SENTINEL} Holdings Ltd</Company><Manager>{_SENTINEL} manager</Manager>'
    '<HeadingPairs><vt:vector size="2" baseType="variant">'
    '<vt:variant><vt:lpstr>Title</vt:lpstr></vt:variant>'
    '<vt:variant><vt:i4>1</vt:i4></vt:variant></vt:vector></HeadingPairs>'
    f'<TitlesOfParts><vt:vector size="1" baseType="lpstr"><vt:lpstr>{_SENTINEL} '
    'deleted section heading</vt:lpstr></vt:vector></TitlesOfParts>'
    '</Properties>')

_TORTURE_CUSTOM = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/'
    'custom-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/'
    '2006/docPropsVTypes">'
    '<property fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}" pid="2" '
    'name="MSIP_Label_9f7f0f0f-0000-0000-0000-000000000000_Enabled">'
    '<vt:lpwstr>true</vt:lpwstr></property>'
    '<property fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}" pid="3" '
    'name="MSIP_Label_9f7f0f0f-0000-0000-0000-000000000000_SiteId">'
    f'<vt:lpwstr>{_SENTINEL}-tenant-guid</vt:lpwstr></property>'
    '</Properties>')

_TORTURE_SETTINGS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    f'<w:settings xmlns:w="{_W}" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" '
    'xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml">'
    '<w:attachedTemplate r:id="rId9"/>'
    '<w:proofState w:spelling="clean" w:grammar="dirty"/>'
    '<w:documentProtection w:edit="readOnly" w:enforcement="1" '
    'w:cryptProviderType="rsaAES" w:hash="Zm9vYmFyaGFzaA==" w:salt="c2FsdHk="/>'
    '<w:mailMerge><w:dataSource r:id="rId10"/></w:mailMerge>'
    '<w15:docId w15:val="{DEADBEEF-0000-4000-8000-000000000001}"/>'
    # Word writes the SAME GUID in both namespaces at once. Ruling only the newer one
    # left the older one in a real document, so the torture package carries both.
    '<w14:docId w14:val="280E9D04"/>'
    '<w:rsids><w:rsidRoot w:val="00A11CE0"/>'
    '<w:rsid w:val="00A11CE0"/><w:rsid w:val="00B22DF1"/>'
    '<w:rsid w:val="00C33E02"/><w:rsid w:val="00D44F13"/></w:rsids>'
    '</w:settings>')

_TORTURE_PEOPLE = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<w15:people xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml">'
    f'<w15:person w15:author="{_SENTINEL} reviewer">'
    '<w15:presenceInfo w15:providerId="AD" '
    f'w15:userId="{_SENTINEL}@example.invalid"/></w15:person></w15:people>')

_TORTURE_COMMENTS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    f'<w:comments xmlns:w="{_W}">'
    f'<w:comment w:id="1" w:author="{_SENTINEL} reviewer" w:initials="TS" '
    'w:date="2019-03-04T14:00:00Z"><w:p><w:r><w:t>check this figure</w:t></w:r>'
    '</w:p></w:comment></w:comments>')


def _torture_document() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<w:document xmlns:w="{_W}" '
        'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
        'mc:Ignorable="w14"><w:body>'
        # A comment and a processing instruction: constructs with no name, which
        # neither the part census nor the element census can reach.
        f'<!-- {_SENTINEL} hidden in an xml comment -->'
        '<?custom-pi note="not content"?>'
        '<w:p w:rsidR="00A11CE0" w:rsidRDefault="00B22DF1" w:rsidRPr="00C33E02" '
        'w14:paraId="1A2B3C4D" w14:textId="5E6F7A8B">'
        '<w:bookmarkStart w:id="0" w:name="_GoBack"/><w:bookmarkEnd w:id="0"/>'
        '<w:proofErr w:type="spellStart"/>'
        '<w:r><w:t xml:space="preserve">Quarterly Review </w:t></w:r>'
        '<w:proofErr w:type="spellEnd"/>'
        '<w:lastRenderedPageBreak/>'
        f'<w:ins w:id="7" w:author="{_SENTINEL} reviewer" w:date="2019-03-04T15:00:00Z">'
        '<w:r><w:t>inserted by a named reviewer</w:t></w:r></w:ins>'
        f'<w:del w:id="8" w:author="{_SENTINEL} reviewer" w:date="2019-03-04T15:01:00Z">'
        '<w:r><w:delText>deleted but still in the file</w:delText></w:r></w:del>'
        '<w:commentRangeStart w:id="1"/><w:commentRangeEnd w:id="1"/>'
        '<w:r><w:commentReference w:id="1"/></w:r>'
        '</w:p>'
        '<w:p w14:paraId="9C0D1E2F" w14:textId="3A4B5C6D">'
        '<w:r><w:drawing><wp:inline xmlns:wp="http://schemas.openxmlformats.org/'
        'drawingml/2006/wordprocessingDrawing"><wp:docPr id="1" name="Picture 1"/>'
        '</wp:inline></w:drawing></w:r></w:p>'
        '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/></w:sectPr>'
        '</w:body></w:document>')


def _torture_content_types() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package'
        '.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="jpeg" ContentType="image/jpeg"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd'
        '.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/settings.xml" ContentType="application/vnd'
        '.openxmlformats-officedocument.wordprocessingml.settings+xml"/>'
        '<Override PartName="/word/comments.xml" ContentType="application/vnd'
        '.openxmlformats-officedocument.wordprocessingml.comments+xml"/>'
        '<Override PartName="/word/people.xml" ContentType="application/vnd'
        '.openxmlformats-officedocument.wordprocessingml.people+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd'
        '.openxmlformats-package.core-properties+xml"/>'
        '<Override PartName="/docProps/app.xml" ContentType="application/vnd'
        '.openxmlformats-officedocument.extended-properties+xml"/>'
        '<Override PartName="/docProps/custom.xml" ContentType="application/vnd'
        '.openxmlformats-officedocument.custom-properties+xml"/>'
        '</Types>')


# Word writes a standalone `<w:rsid>` inside EVERY style, from the same pool as the
# document's own rsids. Neither the attribute rule nor the `w:rsids` container rule
# reaches it, and only a per-part assertion on a real document found it.
_TORTURE_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    f'<w:styles xmlns:w="{_W}">'
    '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>'
    '<w:rsid w:val="00A11CE0"/></w:style>'
    '<w:style w:type="paragraph" w:styleId="Normal"><w:name w:val="Normal"/>'
    '<w:rsid w:val="00B22DF1"/></w:style></w:styles>')

_TORTURE_FONTTABLE = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    f'<w:fonts xmlns:w="{_W}">'
    '<w:font w:name="Aptos"><w:panose1 w:val="020B0004020202020204"/></w:font>'
    '<w:font w:name="A Font Only This Machine Has"/></w:fonts>')

# macOS Cocoa's nonstandard extra part -- no other producer writes it, so its mere
# presence names the producer before anything inside it is read.
_TORTURE_META = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<meta xmlns="http://www.w3.org/1999/xhtml">'
    f'<generator>{_SENTINEL} CocoaOOXMLWriter/9999</generator></meta>')

_TORTURE_ITEMPROPS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<ds:datastoreItem xmlns:ds="http://schemas.openxmlformats.org/officeDocument/'
    '2006/customXml" ds:itemID="{0BADF00D-0000-4000-8000-000000000002}">'
    '<ds:schemaRefs/></ds:datastoreItem>')


def _leaky_jpeg() -> bytes:
    """A JPEG carrying EXIF + a thumbnail, so the recursion rule has something to
    recurse into. Falls back to a bare JPEG if the image corpus is unavailable."""
    try:
        from . import corpus as imgc
        return imgc.build_torture_jpeg()
    except Exception:                                    # noqa: BLE001
        return b"\xff\xd8\xff\xd9"


def torture(path: str) -> str:
    """One package containing every locus the E-DOCX-LOCI rule table knows about,
    plus the two structural hazards (an AppleDouble entry and a part absent from
    `[Content_Types].xml`). Deterministic; no wall clock anywhere."""
    img = _leaky_jpeg()
    parts: list[tuple[str, bytes]] = [
        ("[Content_Types].xml", _torture_content_types().encode()),
        ("_rels/.rels", _ROOT_RELS.encode()),
        ("docProps/core.xml", _TORTURE_CORE.encode()),
        ("docProps/app.xml", _TORTURE_APP.encode()),
        ("docProps/custom.xml", _TORTURE_CUSTOM.encode()),
        ("docProps/thumbnail.jpeg", img),
        ("word/document.xml", _torture_document().encode()),
        ("word/settings.xml", _TORTURE_SETTINGS.encode()),
        ("word/people.xml", _TORTURE_PEOPLE.encode()),
        ("word/comments.xml", _TORTURE_COMMENTS.encode()),
        ("word/media/image1.jpeg", img),
        ("word/styles.xml", _TORTURE_STYLES.encode()),
        # A DOCTYPE lives in its own part rather than in document.xml: F1 REFUSES a
        # package carrying one, so putting it in the main document would make every
        # other torture assertion untestable. The census still sees it here.
        ("word/header1.xml",
         b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
         b'<!DOCTYPE w:hdr [<!ENTITY leak "hidden-by-entity">]>\n'
         b'<w:hdr xmlns:w="' + _W.encode() + b'"/>'),
        ("word/fontTable.xml", _TORTURE_FONTTABLE.encode()),
        ("docProps/meta.xml", _TORTURE_META.encode()),
        ("customXml/item1.xml", b"<root><record>internal-classification</record></root>"),
        ("customXml/itemProps1.xml", _TORTURE_ITEMPROPS.encode()),
        ("word/embeddings/oleObject1.bin", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"),
        ("word/vbaProject.bin", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1VBA"),
        # Structural hazards, not metadata: an AppleDouble pair a macOS round-trip
        # leaves behind, and a part no content-type override covers.
        ("__MACOSX/word/._document.xml", b"\x00\x05\x16\x07AppleDouble"),
        ("word/undeclared.bin", b"a part with no content-type override"),
    ]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in parts:
            zi = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, body)
    return path


TORTURE_SENTINEL = _SENTINEL

def producers_matched(tmpdir: str, repeats: int = 3) -> dict[str, list[str]]:
    """A2 peer set: the SAME document through different producers, N times each.

    Content is held constant and the producer varies -- the shape every A2 peer set
    in this project takes, and different from `producers()` above, which exists for
    W8's container census where content is irrelevant.

    **Microsoft Word cannot be in this set**, and that is a real limitation rather
    than an oversight: it has no CLI on any platform, so it cannot be made to render
    *our* document. It is reported as an absent producer (the limit-#12 precedent
    for Apple's AAC encoder) rather than being represented by an unrelated file,
    which would make the classifier separate documents while appearing to separate
    producers.
    """
    out: dict[str, list[str]] = {}

    out["synth_zipfile"] = [
        synthetic(os.path.join(tmpdir, f"synth_r{r}.docx")) for r in range(repeats)]

    if HAVE_SOFFICE:
        paths = [libreoffice(tmpdir, tag=f"lo{r}") for r in range(repeats)]
        if all(paths):
            out["libreoffice"] = [p for p in paths if p]

    if HAVE_TEXTUTIL:
        paths = [cocoa_textutil(tmpdir, tag=f"tu{r}") for r in range(repeats)]
        if all(paths):
            out["cocoa_textutil"] = [p for p in paths if p]

    # MAT2 as a producer in its own right: it rewrites the package with its own XML
    # writer and its own ZIP conventions. Seeded from a producer whose content
    # matches the rest of the set, so it stays a producer axis rather than a
    # content one.
    seed_name = "libreoffice" if "libreoffice" in out else "synth_zipfile"
    if HAVE_MAT2:
        sub = os.path.join(tmpdir, "mat2")
        os.makedirs(sub, exist_ok=True)
        paths = []
        for r, src in enumerate(out[seed_name][:repeats]):
            dst = os.path.join(sub, f"m{r}_" + os.path.basename(src))
            shutil.copy(src, dst)
            if _run_quiet(["mat2", "--inplace", dst]):
                paths.append(dst)
        if len(paths) == repeats:
            out["mat2_out"] = paths

    n = min(len(v) for v in out.values())
    return {k: v[:n] for k, v in out.items()}


def matched_producers_available() -> dict[str, bool]:
    """Which producers can render OUR document. Word never can -- no CLI exists."""
    return {"synth_zipfile": True, "libreoffice": HAVE_SOFFICE,
            "cocoa_textutil": HAVE_TEXTUTIL, "mat2_out": HAVE_MAT2,
            "msword": False}

def a1_variants(tmpdir: str, n_variants: int = 3, n_repeats: int = 5):
    """Same document, metadata differing only by a per-variant sentinel.

    A correct F1 collapses them to identical bytes -> A1 pass. The sentinel goes into
    **every** locus that carries a name, not just `core.xml`: a DOCX routinely holds
    the same identity in `core.xml`, `app.xml`, `custom.xml` and `people.xml` at
    once, and clearing one is the classic half-scrub this test exists to catch.
    """
    groups = []
    for i in range(n_variants):
        sentinel = chr(65 + i) * 6
        core = _TORTURE_CORE.replace(_SENTINEL, sentinel)
        app = _TORTURE_APP.replace(_SENTINEL, sentinel)
        custom = _TORTURE_CUSTOM.replace(_SENTINEL, sentinel)
        people = _TORTURE_PEOPLE.replace(_SENTINEL, sentinel)
        settings = _TORTURE_SETTINGS.replace("00A11CE0", f"00A11{i:03X}")
        doc = _torture_document().replace(_SENTINEL, sentinel)
        # Tracked changes and comments are refused at F1, so the A1 corpus carries
        # the loci F1 actually handles -- testing a tier against input it declines
        # would measure the refusal, not the scrub.
        for tag in ("w:ins", "w:del", "w:commentRangeStart", "w:commentRangeEnd",
                    "w:commentReference"):
            doc = re.sub(rf"<{tag}[^>]*>.*?</{tag}>|<{tag}[^>]*/>", "", doc,
                         flags=re.S)

        parts = {
            "[Content_Types].xml": _torture_content_types().encode(),
            "_rels/.rels": _ROOT_RELS.encode(),
            "docProps/core.xml": core.encode(),
            "docProps/app.xml": app.encode(),
            "docProps/custom.xml": custom.encode(),
            "word/document.xml": doc.encode(),
            "word/settings.xml": settings.encode(),
            "word/people.xml": people.encode(),
            "word/styles.xml": _TORTURE_STYLES.encode(),
        }
        from src.scrub.formats.ooxml import zipwrite
        variant = zipwrite.write(parts)

        paths = []
        for r in range(n_repeats):
            path = os.path.join(tmpdir, f"a1_v{i}_r{r}.docx")
            with open(path, "wb") as f:
                f.write(variant)
            paths.append(path)
        groups.append(paths)
    return groups


def diverse_inputs(tmpdir: str, n: int = 4) -> list[str]:
    """Structurally varied packages for the fingerprint guard.

    **The part SET varies, not only the text**, and the first version of this
    function got that wrong in an instructive way: it wrote the same three-part
    package every time with a different paragraph inside, so `[Content_Types].xml`
    and `_rels/.rels` were byte-identical across every input — and the guard duly
    reported their entire central-directory records, CRCs and all, as our signature.
    The guard was right and the corpus was wrong. A guard run over near-identical
    inputs finds every shared byte and reports the format itself as a tool
    fingerprint.
    """
    bodies = [
        "Short.\n",
        "A longer paragraph with rather more words in it, so the parts differ in "
        "size and the deflate output differs with them.\n",
        "Heading\n\nBody text.\n\nAnother paragraph.\n",
        "Tabs\tand punctuation: semicolons; dashes -- and numbers 12345.\n",
    ]
    # Each shape declares a different set of parts, so no content-type override list
    # and no relationship set is shared by every input.
    shapes = [
        (),
        ("styles",),
        ("settings", "fontTable"),
        ("styles", "settings", "theme"),
    ]
    out = []
    for i in range(n):
        path = os.path.join(tmpdir, f"diverse_{i}.docx")
        _shaped(path, bodies[i % len(bodies)], shapes[i % len(shapes)])
        out.append(path)
    return out


_EXTRA_PARTS = {
    "styles": ("word/styles.xml",
               '<w:styles xmlns:w="%s"><w:style w:type="paragraph" '
               'w:styleId="Normal"><w:name w:val="Normal"/></w:style></w:styles>'),
    "settings": ("word/settings.xml",
                 '<w:settings xmlns:w="%s"><w:defaultTabStop w:val="720"/>'
                 '</w:settings>'),
    "fontTable": ("word/fontTable.xml",
                  '<w:fonts xmlns:w="%s"><w:font w:name="Aptos"/></w:fonts>'),
    "theme": ("word/theme/theme1.xml",
              '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/'
              'main" name="%.0s"/>'),
}

_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"


def _shaped(path: str, text: str, extras: tuple[str, ...]) -> str:
    """A package whose declared part set follows `extras`, kept internally
    consistent: every extra part gets its content-type override and its
    relationship, so the walker's own consistency check stays meaningful."""
    from src.scrub.formats.ooxml import zipwrite

    overrides = ['<Override PartName="/word/document.xml" ContentType="application/'
                 'vnd.openxmlformats-officedocument.wordprocessingml.document'
                 '.main+xml"/>']
    rels, parts = [], {}
    for i, key in enumerate(extras):
        name, tmpl = _EXTRA_PARTS[key]
        parts[name] = (tmpl % _W).encode()
        overrides.append(
            f'<Override PartName="/{name}" ContentType="application/vnd'
            f'.openxmlformats-officedocument.wordprocessingml.{key}+xml"/>')
        target = name.split("/", 1)[1]
        rels.append(f'<Relationship Id="rId{i + 1}" Type="{_REL_TYPE}{key}" '
                    f'Target="{target}"/>')

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-'
        'package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        + "".join(overrides) + '</Types>')

    parts["[Content_Types].xml"] = content_types.encode()
    parts["_rels/.rels"] = _ROOT_RELS.encode()
    parts["word/document.xml"] = _document_xml(text).encode()
    if rels:
        parts["word/_rels/document.xml.rels"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            'relationships">' + "".join(rels) + "</Relationships>").encode()

    with open(path, "wb") as f:
        f.write(zipwrite.write(parts))
    return path
