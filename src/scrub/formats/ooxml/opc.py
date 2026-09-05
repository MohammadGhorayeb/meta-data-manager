"""OPC layer: content types, the relationship graph, and what we refuse.

A ZIP is only the envelope. The Open Packaging Conventions layer on top of it says
which entries are *parts*, what each part is, and how parts point at each other. Two
consequences drive every design choice here:

1. **Dropping a part means dropping its relationship and its content-type override.**
   Leave either behind and Word shows "unreadable content" — the scrub has destroyed
   the document while reporting success. So the graph is not a nicety; it is the thing
   that makes deletion safe, and `orphans()` is what F1 will check itself against.

2. **`PK\\x03\\x04` is the magic of every ZIP ever made.** Unlike every format so far,
   the prefix identifies nothing: XLSX, PPTX, ODT, EPUB, JAR and a plain archive all
   start the same way. Identification has to read the central directory and look for
   the parts that make a package *this* format, which is what `main_part()` is for.

XML is parsed with the stdlib. `ElementTree` does not expand external entities and
raises on undefined ones, so the classic XXE and entity-expansion attacks against an
untrusted package do not apply — but the parse is still wrapped, because a
deliberately malformed part must fail closed rather than propagate a stdlib
exception type the CLI does not map.
"""
from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

from ...errors import ParseError
from . import zipread

CONTENT_TYPES = "[Content_Types].xml"

_CT_NS = "{http://schemas.openxmlformats.org/package/2006/content-types}"
_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"

# The main part of each OOXML flavour. Identification is by part presence, never by
# extension -- the caller may have been handed a .zip that is really a .docx.
MAIN_PARTS = {
    "docx": ("word/document.xml",),
    "xlsx": ("xl/workbook.xml",),
    "pptx": ("ppt/presentation.xml",),
}

# Parts whose presence means we cannot vouch for a full scrub. Refusing is the
# project's standing answer (PDF refuses signed, encrypted and attachment-bearing
# files); each entry names why, because a refusal without a reason is just a failure.
REFUSE_PARTS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^_xmlsignatures/"),
     "digitally signed package: any rewrite invalidates the signature, and handing "
     "back a broken signature is worse than saying so"),
    (re.compile(r"(^|/)vbaProject\.bin$"),
     "macro-enabled: an executable payload we do not parse, carrying its own "
     "authorship and paths"),
    (re.compile(r"^word/embeddings/|^xl/embeddings/|^ppt/embeddings/"),
     "embedded OLE object: a whole document of its own, needing its own pass"),
    (re.compile(r"(^|/)EncryptedPackage$|(^|/)EncryptionInfo$"),
     "encrypted package"),
]


@dataclass
class Relationship:
    id: str
    type: str
    target: str
    mode: str            # "Internal" or "External"
    source_part: str     # "" for the package-level _rels/.rels

    @property
    def is_external(self) -> bool:
        return self.mode == "External"


@dataclass
class Package:
    archive: zipread.ZipArchive
    defaults: dict[str, str]                 # extension -> content type
    overrides: dict[str, str]                # /part/name -> content type
    rels: list[Relationship]
    flavour: str | None = None
    notes: list[str] = field(default_factory=list)

    # ----------------------------------------------------------------- lookups
    def content_type(self, part: str) -> str | None:
        """The declared type of a part: an override wins over the extension default."""
        override = self.overrides.get("/" + part.lstrip("/"))
        if override:
            return override
        ext = part.rsplit(".", 1)[-1].lower() if "." in part else ""
        return self.defaults.get(ext)

    def parts(self) -> list[str]:
        return [e.name for e in self.archive.entries if not e.is_dir]

    def undeclared_parts(self) -> list[str]:
        """Entries no content type covers.

        Not an error by itself -- `[Content_Types].xml` and the `_rels` parts are
        covered by the `rels`/`xml` defaults, and an AppleDouble entry is covered by
        nothing because it is not a part at all. It is reported so F1 decides
        deliberately instead of by omission.
        """
        return [p for p in self.parts()
                if p != CONTENT_TYPES and self.content_type(p) is None]

    def orphans(self, kept: set[str]) -> list[str]:
        """Relationships in `kept` whose internal target is not in `kept`.

        The check F1 must pass to be allowed to delete anything: a surviving
        relationship pointing at a part we removed is exactly the "unreadable
        content" failure.
        """
        out = []
        for r in self.rels:
            if r.is_external or r.source_part not in kept and r.source_part != "":
                continue
            target = resolve(r.source_part, r.target)
            if target not in kept:
                out.append(f"{r.source_part or '<package>'} -> {target} ({r.id})")
        return out

    def external_targets(self) -> list[Relationship]:
        """Relationships pointing outside the package: URLs, and absolute local
        paths such as an attached template on a UNC share."""
        return [r for r in self.rels if r.is_external]

    def refusals(self) -> list[str]:
        out = []
        for name in self.parts():
            for pat, why in REFUSE_PARTS:
                if pat.search(name):
                    out.append(f"{name}: {why}")
        return out


def resolve(source_part: str, target: str) -> str:
    """Resolve a relationship target against the part that declared it.

    Targets are relative to the *directory of the source part*, so
    `word/document.xml` + `media/image1.png` is `word/media/image1.png`, and the
    package-level `_rels/.rels` resolves against the root.
    """
    if target.startswith("/"):
        return target.lstrip("/")
    base = posixpath.dirname(source_part)
    return posixpath.normpath(posixpath.join(base, target)).lstrip("/")


def rels_part_for(part: str) -> str:
    """Where a part's relationships live: `a/b.xml` -> `a/_rels/b.xml.rels`."""
    d, base = posixpath.split(part)
    return posixpath.join(d, "_rels", base + ".rels") if d else f"_rels/{base}.rels"


def _parse(name: str, body: bytes) -> ET.Element:
    try:
        return ET.fromstring(body)
    except ET.ParseError as exc:
        raise ParseError(f"{name}: malformed XML ({exc})") from exc


def _read_content_types(arc: zipread.ZipArchive) -> tuple[dict, dict]:
    entry = arc.by_name(CONTENT_TYPES)
    if entry is None:
        raise ParseError("no [Content_Types].xml: not an OPC package")
    root = _parse(CONTENT_TYPES, entry.content())
    defaults, overrides = {}, {}
    for el in root:
        if el.tag == f"{_CT_NS}Default":
            ext = (el.get("Extension") or "").lower()
            if ext:
                defaults[ext] = el.get("ContentType") or ""
        elif el.tag == f"{_CT_NS}Override":
            pn = el.get("PartName") or ""
            if pn:
                overrides[pn] = el.get("ContentType") or ""
    return defaults, overrides


def _read_rels(arc: zipread.ZipArchive) -> list[Relationship]:
    out: list[Relationship] = []
    for e in arc.entries:
        if not e.name.endswith(".rels"):
            continue
        # `_rels/.rels` describes the package; `a/_rels/b.xml.rels` describes a/b.xml.
        d = posixpath.dirname(posixpath.dirname(e.name))
        base = posixpath.basename(e.name)[:-len(".rels")]
        source = posixpath.join(d, base) if base else ""
        for el in _parse(e.name, e.content()):
            if el.tag != f"{_REL_NS}Relationship":
                continue
            out.append(Relationship(
                id=el.get("Id") or "", type=el.get("Type") or "",
                target=el.get("Target") or "",
                mode=el.get("TargetMode") or "Internal", source_part=source))
    return out


def main_part(arc: zipread.ZipArchive) -> str | None:
    """Which OOXML flavour this package is, by the part that defines it."""
    names = set(arc.names)
    for flavour, mains in MAIN_PARTS.items():
        if any(m in names for m in mains):
            return flavour
    return None


def parse(data: bytes) -> Package:
    """Read a package. Raises ParseError on anything we cannot fully account for."""
    arc = zipread.read(data)
    defaults, overrides = _read_content_types(arc)
    pkg = Package(archive=arc, defaults=defaults, overrides=overrides,
                  rels=_read_rels(arc), flavour=main_part(arc))
    if pkg.flavour is None:
        raise ParseError("OPC package with no recognised main part "
                         "(not a DOCX, XLSX or PPTX)")
    undeclared = [p for p in pkg.undeclared_parts()
                  if not p.startswith("__MACOSX/")
                  and not posixpath.basename(p).startswith("._")]
    if undeclared:
        pkg.notes.append("parts with no declared content type: "
                         + ", ".join(sorted(undeclared)))
    return pkg


def looks_like(data: bytes, flavour: str) -> bool:
    """Cheap identification for `claims()`: is this that OOXML flavour?

    Never raises. `PK\\x03\\x04` cannot decide anything on its own, so this opens the
    central directory -- but a malformed archive must make the handler decline, not
    crash dispatch for every other format.
    """
    try:
        arc = zipread.read(data)
    except Exception:                                     # noqa: BLE001
        return False
    return (CONTENT_TYPES in arc.names
            and any(m in arc.names for m in MAIN_PARTS.get(flavour, ())))
