"""E-DOCX-LOCI (W9/M7) — every place a DOCX keeps metadata, with a disposition.

Phase 1's discipline was "prove every duplicate locus is cleared, not just the named
tags". DOCX has more loci than any format so far, and the ones that matter are the
ones no tool's README mentions. So F1 is written against a **list**, and this module
is how the list is produced and kept honest.

The load-bearing idea is the last column. Every locus carries a disposition —
**drop / keep / recurse / refuse** — and a locus found in the corpus that has no
disposition comes back `UNCLASSIFIED`, which a test treats as a failure. A leak
cannot hide in a place nobody wrote a rule for, because the census will name the
place.

Reading is deliberately done twice, by two mechanisms that fail differently:
- an XML parse, which sees structure and namespaces properly;
- a raw-bytes regex sweep, which sees things the parse would miss — attributes inside
  `mc:AlternateContent` fallbacks, content in parts that are not well-formed, and
  anything in a part the parse refused entirely.

Run it for the table:

    python -m tests.scrub.e_docx_loci
"""
from __future__ import annotations

import re
import zlib
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from .e_docx_zip import read_archive

DROP, KEEP, RECURSE, REFUSE = "drop", "keep", "recurse", "refuse"


@dataclass(frozen=True)
class Locus:
    id: str
    what: str
    disposition: str
    why: str


# --------------------------------------------------------------------------- #
# Whole parts
# --------------------------------------------------------------------------- #
PART_RULES: list[tuple[re.Pattern, Locus]] = [
    (re.compile(r"^docProps/core\.xml$"), Locus(
        "core.xml", "creator, lastModifiedBy, revision, created/modified, lastPrinted",
        DROP, "pure metadata; the part itself goes, not just its fields")),
    (re.compile(r"^docProps/app\.xml$"), Locus(
        "app.xml", "Application, AppVersion, Company, Manager, Template, TotalTime, "
        "Pages/Words/Characters, HeadingPairs, TitlesOfParts",
        DROP, "names the application AND leaks heading text of possibly-deleted "
              "sections through TitlesOfParts")),
    (re.compile(r"^docProps/custom\.xml$"), Locus(
        "custom.xml", "arbitrary properties; SharePoint content types; MSIP_Label_* "
        "sensitivity labels carrying a tenant GUID",
        DROP, "the tenant GUID identifies the organisation, not merely the person")),
    (re.compile(r"^docProps/thumbnail\.(jpeg|jpg|emf|wmf)$"), Locus(
        "thumbnail", "rendered first page, with its own EXIF",
        DROP, "a picture of the document's own content plus a second metadata block")),
    (re.compile(r"^docProps/meta\.xml$"), Locus(
        "meta.xml (textutil)", "macOS Cocoa's nonstandard extra properties part",
        DROP, "no other producer writes it, so its mere presence names the producer")),
    (re.compile(r"^word/people\.xml$"), Locus(
        "people.xml", "comment/revision author names and provider IDs",
        DROP, "author identity, which is the leak this project exists to remove")),
    (re.compile(r"^word/comments.*\.xml$"), Locus(
        "comments*", "comments, commentsExtended, commentsIds — text, author, date",
        REFUSE, "not metadata: content the document displays. Refused at F1 and "
                "flattened at F2, never silently deleted")),
    (re.compile(r"^word/(settings|webSettings)\.xml$"), Locus(
        "settings.xml", "rsids, w15:docId, attachedTemplate, proofState, "
        "documentProtection, mailMerge data source",
        KEEP, "the part is needed; individual elements are dropped (see below)")),
    (re.compile(r"^word/fontTable\.xml$"), Locus(
        "fontTable.xml", "the fonts this machine had installed",
        KEEP, "removing it changes rendering; it is a weak machine-profile hint and "
              "is recorded as a residual rather than destroyed")),
    (re.compile(r"^word/media/"), Locus(
        "word/media/*", "embedded images with their own EXIF/XMP/ICC",
        RECURSE, "goes through the Phase 1 JPEG/PNG handlers, which already exist")),
    (re.compile(r"^word/embeddings/"), Locus(
        "word/embeddings/*", "OLE objects — a whole other document inside this one",
        REFUSE, "a document of its own needing its own pass; the PDF-attachment "
                "precedent")),
    (re.compile(r"^word/vbaProject\.bin$"), Locus(
        "vbaProject.bin", "macros, with their own authorship and paths",
        REFUSE, "an executable payload we do not parse; refusing beats half-scrubbing")),
    (re.compile(r"^customXml/"), Locus(
        "customXml/*", "SharePoint / records-management payloads and their itemProps",
        DROP, "opaque organisational metadata attached to the document")),
    (re.compile(r"^__MACOSX/|(^|/)\._"), Locus(
        "AppleDouble", "macOS resource forks with Finder metadata",
        DROP, "the Step 2 crash target; also carries the original filename and "
              "Finder info")),
    (re.compile(r"^_rels/|/_rels/"), Locus(
        "*.rels", "relationship targets — external URLs and absolute local paths",
        KEEP, "structurally required; individual external targets are examined")),
    (re.compile(r"^\[Content_Types\]\.xml$"), Locus(
        "[Content_Types].xml", "overrides revealing which parts existed",
        KEEP, "required; must be kept consistent with the parts we drop")),
    (re.compile(r"^word/(document|styles|numbering|theme/|header|footer|footnotes|"
                r"endnotes|glossary)"), Locus(
        "content parts", "the document itself",
        KEEP, "content; scrubbed in place at the attribute level")),
]


# --------------------------------------------------------------------------- #
# Things inside parts
# --------------------------------------------------------------------------- #
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
W14 = "{http://schemas.microsoft.com/office/word/2010/wordml}"
W15 = "{http://schemas.microsoft.com/office/word/2012/wordml}"

ATTR_RULES: list[tuple[re.Pattern, Locus]] = [
    (re.compile(r"^w:rsid[A-Za-z]*$"), Locus(
        "w:rsid*", "revision-save IDs linking edits to editing sessions",
        DROP, "the leak the literature names for this format")),
    (re.compile(r"^w14:paraId$|^w14:textId$"), Locus(
        "w14:paraId/textId", "persistent per-paragraph identifiers",
        DROP, "they travel with a paragraph pasted into another document, so they "
              "link two files to one source — and MAT2 leaves them (§2.6)")),
    (re.compile(r"^w:author$|^w:date$|^w15:person|^w15:providerId$"), Locus(
        "author/date attrs", "revision and comment authorship",
        DROP, "author identity")),
]

# Attributes whose meaning depends on the element carrying them. `w:name` was in the
# rule table above until the first census run reported it **386 times** in one
# document: WordprocessingML reuses it on `w:font`, `w:style` and `w:compatSetting`,
# so a context-free rule would have had F1 deleting font and style names — content,
# not metadata. Anything ambiguous belongs here, matched with its parent.
CONTEXT_ATTR_RULES: list[tuple[str, str, Locus]] = [
    (f"{W}bookmarkStart", f"{W}name", Locus(
        "w:bookmarkStart/@w:name", "bookmark names, including _GoBack",
        DROP, "_GoBack records where the cursor sat at the last save")),
    (f"{W}attachedTemplate", "{http://schemas.openxmlformats.org/officeDocument/"
                             "2006/relationships}id", Locus(
        "w:attachedTemplate/@r:id", "relationship to the attached template",
        DROP, "resolves to a path, frequently a UNC share naming a user")),
]

ELEM_RULES: list[tuple[str, Locus]] = [
    (f"{W}rsids", Locus("w:rsids", "the settings.xml RSID pool", DROP,
                        "the full session inventory, in one element")),
    (f"{W}rsid", Locus("w:rsid (element)", "a standalone session id, one per style",
                       DROP,
                       "Word writes one inside every w:style in styles.xml, from the "
                       "same pool as the document's rsids -- neither the attribute "
                       "rule nor the w:rsids container rule reaches it")),
    (f"{W15}docId", Locus("w15:docId", "a persistent per-document GUID", DROP,
                          "stable across saves; links two copies of a document "
                          "outright, which no A1/A2/A3 tier even models")),
    (f"{W14}docId", Locus("w14:docId", "the same GUID in the older namespace", DROP,
                          "Word writes both; ruling only the newer one left this in "
                          "a real document until a test caught it")),
    (f"{W}attachedTemplate", Locus(
        "w:attachedTemplate", "template reference, frequently a UNC path", DROP,
        "commonly contains a username or a network share name")),
    (f"{W}proofState", Locus("w:proofState", "spell/grammar check state", DROP,
                             "reveals the proofing configuration of the machine")),
    (f"{W}proofErr", Locus("w:proofErr", "which words the speller flagged", DROP,
                           "the dictionary and language configuration, per word")),
    (f"{W}lastRenderedPageBreak", Locus(
        "w:lastRenderedPageBreak", "layout state from whichever renderer last opened it",
        DROP, "names the renderer's pagination, not the author's document")),
    (f"{W}documentProtection", Locus(
        "w:documentProtection", "protection hash and salt", DROP,
        "a password hash is a credential, not a setting")),
    (f"{W}mailMerge", Locus("w:mailMerge", "mail-merge data source path", DROP,
                            "an absolute path to a file on the author's machine")),
    (f"{W}ins", Locus("w:ins/w:del", "tracked changes with author and date", REFUSE,
                      "content that is displayed with markup on")),
    (f"{W}del", Locus("w:ins/w:del", "tracked changes with author and date", REFUSE,
                      "content that is displayed with markup on")),
    (f"{W}commentReference", Locus("w:commentReference", "a comment anchor", REFUSE,
                                   "content, per the comments* rule")),
]

# app.xml / core.xml children are all metadata by construction; named individually so
# the census reports WHICH ones a producer wrote rather than just "the part existed".
_PROPS_PARTS = {"docProps/app.xml", "docProps/core.xml", "docProps/custom.xml",
                "docProps/meta.xml"}


# Constructs with no name for a rule to match on. The part census keys on part
# names and the element/attribute census keys on element names -- an XML comment has
# neither, so it slipped past both until it was looked for directly. No producer in
# this corpus writes one, which is exactly why.
CONSTRUCT_RULES: list[tuple[str, re.Pattern, Locus]] = [
    ("xml_comment", re.compile(rb"<!--"), Locus(
        "<!-- xml comment -->", "arbitrary hidden text, never rendered",
        DROP, "invisible in Word, reachable by no name-based rule, and a perfect "
              "hiding place")),
    ("processing_instruction", re.compile(rb"<\?(?!xml[\s?])"), Locus(
        "<?processing instruction?>", "an instruction to a consuming application",
        DROP, "not content, and can carry arbitrary text")),
    ("doctype", re.compile(rb"<!DOCTYPE"), Locus(
        "<!DOCTYPE>", "entity definitions",
        REFUSE, "a data-hiding channel and an expansion attack; not modelled")),
]


@dataclass
class Finding:
    locus: Locus
    part: str
    count: int
    sample: str = ""


def _attr_prefixed(part_xml: bytes) -> list[str]:
    """Attribute names as WRITTEN, prefix and all, straight from the bytes.

    The parse-based pass sees resolved namespaces and misses anything in a part it
    could not parse; this sees the text. Both run, because they fail differently.
    """
    return re.findall(rb'\s([A-Za-z][\w]*:[\w]+)\s*=\s*"', part_xml) and [
        m.decode() for m in re.findall(rb'\s([A-Za-z][\w]*:[\w]+)\s*=\s*"', part_xml)]


def scan_part(name: str, body: bytes) -> list[Finding]:
    out: list[Finding] = []

    # 0. nameless constructs
    for _key, rx, loc in CONSTRUCT_RULES:
        hits = rx.findall(body)
        if hits:
            out.append(Finding(loc, name, len(hits)))

    # 1. raw attribute sweep
    counts: dict[str, int] = {}
    samples: dict[str, str] = {}
    for attr in _attr_prefixed(body) or []:
        for pat, loc in ATTR_RULES:
            if pat.match(attr):
                counts[loc.id] = counts.get(loc.id, 0) + 1
                if loc.id not in samples:
                    m = re.search(re.escape(attr).encode() + rb'="([^"]{0,40})"', body)
                    samples[loc.id] = m.group(1).decode("utf-8", "replace") if m else ""
    seen = {loc.id: loc for _, loc in ATTR_RULES}
    for lid, n in counts.items():
        out.append(Finding(seen[lid], name, n, samples.get(lid, "")))

    # 2. element sweep, by parse where possible and by tag text where not
    try:
        root = ET.fromstring(body)
        elems = list(root.iter())
        tags = [el.tag for el in elems]
    except ET.ParseError:
        elems, tags = [], []

    for parent_tag, attr, loc in CONTEXT_ATTR_RULES:
        vals = [el.get(attr) for el in elems
                if el.tag == parent_tag and el.get(attr) is not None]
        if vals:
            out.append(Finding(loc, name, len(vals), str(vals[0])[:40]))

    for tag, loc in ELEM_RULES:
        n = tags.count(tag) if tags else len(re.findall(
            rb"<" + tag.split("}")[-1].encode() + rb"[ />]", body))
        if n:
            out.append(Finding(loc, name, n))

    # 3. property parts: report every child element that carries text
    if name in _PROPS_PARTS:
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            return out
        for el in root.iter():
            if el is root or not (el.text or "").strip():
                continue
            short = el.tag.split("}")[-1]
            out.append(Finding(
                Locus(f"{name}:{short}", "document property", DROP,
                      "a named property with a value"),
                name, 1, (el.text or "")[:40]))
    return out


def classify_part(name: str) -> Locus | None:
    for pat, loc in PART_RULES:
        if pat.search(name):
            return loc
    return None


def census(path: str) -> dict:
    arc = read_archive(path)
    parts, findings, unclassified = [], [], []
    for e in arc.entries:
        if e.is_dir:
            continue
        loc = classify_part(e.name)
        if loc is None:
            unclassified.append(e.name)
        else:
            parts.append((e.name, loc))
        try:
            body = (zlib.decompress(e.data, -15) if e.method_cen == 8 else e.data)
        except zlib.error:
            continue
        if body[:1] in (b"<", b"\xef"):
            findings.extend(scan_part(e.name, body))
    return {"parts": parts, "findings": findings, "unclassified": unclassified}


def main() -> int:
    import tempfile

    from . import docx_corpus as C

    with tempfile.TemporaryDirectory() as td:
        paths = C.producers(td)
        results = {n: census(p) for n, p in sorted(paths.items())}

    print("# E-DOCX-LOCI — metadata loci by producer\n")
    all_loci: dict[str, Locus] = {}
    hits: dict[str, dict[str, int]] = {}
    for prod, r in results.items():
        for _, loc in r["parts"]:
            all_loci[loc.id] = loc
            hits.setdefault(loc.id, {}).setdefault(prod, 0)
            hits[loc.id][prod] += 1
        for f in r["findings"]:
            all_loci[f.locus.id] = f.locus
            hits.setdefault(f.locus.id, {})
            hits[f.locus.id][prod] = hits[f.locus.id].get(prod, 0) + f.count

    print(f"| locus | what it is | {' | '.join(results)} | disposition |")
    print("|---|---|" + "|".join("---" for _ in results) + "|---|")
    for lid in sorted(all_loci, key=lambda k: (all_loci[k].disposition, k)):
        loc = all_loci[lid]
        row = " | ".join(str(hits[lid].get(p, "·")) for p in results)
        print(f"| `{lid}` | {loc.what} | {row} | **{loc.disposition}** |")

    print("\n## Unclassified — every one of these is a hole in F1\n")
    any_un = False
    for prod, r in results.items():
        if r["unclassified"]:
            any_un = True
            print(f"- **{prod}**: {', '.join(r['unclassified'])}")
    if not any_un:
        print("(none — every part in the corpus has a disposition)")

    print("\n## Sampled values\n")
    for prod, r in results.items():
        vals = [f"{f.locus.id}={f.sample!r}" for f in r["findings"] if f.sample]
        if vals:
            print(f"- **{prod}**: " + "; ".join(vals[:12]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
