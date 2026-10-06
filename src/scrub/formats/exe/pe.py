"""PE at F1: what the build left in a Windows program, removed without moving a byte.

Phase 5 M3 (`docs/p5_executables_plan.md` §1, §10). What a Windows build writes:

- the link time, in the COFF header (and again in the export, resource, load-config
  and debug directories);
- the **Rich header**, between the DOS stub and the PE header: every Microsoft tool
  that touched the program, its build number and how many objects it made -- a
  per-project fingerprint (Webb). MSVC only;
- a **CodeView** debug entry: the PDB's path (pip's own launcher ships
  `C:\\Users\\<author>\\Projects\\...`) and a GUID matching that one build's PDB;
  `/Brepro` adds a REPRO entry, a hash of the build;
- DWARF sections (mingw, clang, Go), naming the build directory -- in **every**
  mingw build, from the C runtime's own debug info, even without `-g`;
- `.file` records in the COFF symbol table: the source files' names;
- the version resource: company, copyright, original file name;
- Go's build ID and build info (`go.py`).

Unlike ELF, Windows maps every section, debug ones included. So "never loaded" is
not available as a rule; what is checked instead is that nothing refers to them:
a DWARF section is zeroed only when no base relocation patches it and no data
directory points into it, or the file is refused. Everything else is a header
field, a debug-directory entry, a string value or a stamp, edited at its own
length, and `common.check_unchanged` proves on every scrub that nothing outside
those ranges moved. The checksum is recomputed when the file had one.

The version strings stay when the program imports `version.dll`, which is how a
program reads its own version information (the `ReadBuildInfo` rule, again).
Refused: Authenticode-signed files (limit #52) and .NET assemblies (limit #54).
"""
from __future__ import annotations

import array
import hashlib
import struct
import sys
from dataclasses import dataclass, field

from ...errors import ParseError, UnsupportedFormatError
from . import common, go

PE32, PE32_PLUS = 0x10B, 0x20B
DIR_EXPORT, DIR_RESOURCE, DIR_SECURITY, DIR_BASERELOC = 0, 2, 4, 5
DIR_DEBUG, DIR_LOAD_CONFIG, DIR_DELAY_IMPORT, DIR_CLR = 6, 10, 13, 14
DIR_IMPORT = 1
DEBUG_CODEVIEW, DEBUG_MISC, DEBUG_REPRO = 2, 4, 16
# Debug entries that name or identify the build: removed. Others (POGO, VC_FEATURE,
# and EX_DLLCHARACTERISTICS, which the loader reads for CET) stay.
_DEBUG_DROP = {DEBUG_CODEVIEW: "CodeView (PDB path and GUID)", DEBUG_MISC: "MISC",
               DEBUG_REPRO: "REPRO (build hash)"}
C_FILE = 103
RT_VERSION = 16
DANS = 0x536E6144                         # "DanS", the Rich header's masked start
# Version-resource strings that name a person or an origin. ProductName,
# FileDescription and the versions describe the program and stay.
VERSION_IDENTITY = ("CompanyName", "LegalCopyright", "LegalTrademarks", "Comments",
                    "InternalName", "OriginalFilename", "PrivateBuild",
                    "SpecialBuild")


def _is_dwarf(name: str) -> bool:
    return name.startswith((".debug_", ".zdebug_")) or name == ".debug_gdb_scripts"


@dataclass
class Section:
    name: str
    va: int
    vsize: int
    raw: int
    rawsize: int
    flags: int

    def holds(self, rva: int) -> bool:
        return self.va <= rva < self.va + max(self.vsize, self.rawsize)


@dataclass
class Pe:
    lfanew: int
    coff: int                       # offset of the COFF file header
    opt: int                        # offset of the optional header
    plus: bool
    headers_size: int
    checksum_at: int
    dirs: list[tuple[int, int]]     # (rva, size) per data directory
    dirs_at: int
    sections: list[Section]
    symtab: tuple[int, int]         # (offset, count) of the COFF symbol table
    extra: dict = field(default_factory=dict)

    def offset(self, rva: int, length: int = 1) -> int:
        """File offset of an RVA, checked to hold `length` bytes."""
        if rva + length <= self.headers_size:
            return rva
        for s in self.sections:
            if s.holds(rva):
                at = s.raw + (rva - s.va)
                if at + length > s.raw + s.rawsize:
                    raise ParseError("PE: a structure runs past its section's data")
                return at
        raise ParseError(f"PE: RVA {rva:#x} is in no section")


def is_pe(data: bytes) -> bool:
    if data[:2] != b"MZ" or len(data) < 0x40:
        return False
    lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    return lfanew + 4 <= len(data) and data[lfanew:lfanew + 4] == b"PE\0\0"


def parse(data: bytes) -> Pe:
    if not is_pe(data):
        raise ParseError("not a PE file")
    lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    coff = lfanew + 4
    if coff + 20 > len(data):
        raise ParseError("PE: COFF header truncated")
    _machine, nsec, _ts, symptr, nsyms, optsize, _chars = struct.unpack_from(
        "<HHIIIHH", data, coff)
    opt = coff + 20
    if opt + optsize > len(data) or optsize < 96:
        raise ParseError("PE: optional header truncated")
    magic = struct.unpack_from("<H", data, opt)[0]
    if magic not in (PE32, PE32_PLUS):
        raise ParseError(f"PE: unknown optional-header magic {magic:#x}")
    plus = magic == PE32_PLUS
    headers_size = struct.unpack_from("<I", data, opt + 60)[0]
    n_dirs_at = opt + (108 if plus else 92)
    n_dirs = struct.unpack_from("<I", data, n_dirs_at)[0]
    dirs_at = n_dirs_at + 4
    if n_dirs > 16 or dirs_at + 8 * n_dirs > opt + optsize:
        raise ParseError("PE: data directories do not fit the optional header")
    dirs = [struct.unpack_from("<II", data, dirs_at + 8 * i) for i in range(n_dirs)]
    dirs += [(0, 0)] * (16 - n_dirs)
    sect_at = opt + optsize
    if sect_at + 40 * nsec > len(data):
        raise ParseError("PE: section table runs past the end of the file")
    if symptr and symptr + 18 * nsyms > len(data):
        raise ParseError("PE: the COFF symbol table runs past the end of the file")
    strtab = symptr + 18 * nsyms if symptr else 0
    sections = []
    for i in range(nsec):
        raw_name, vsize, va, rawsize, raw = struct.unpack_from("<8sIIII", data,
                                                                sect_at + 40 * i)
        flags = struct.unpack_from("<I", data, sect_at + 40 * i + 36)[0]
        name = raw_name.rstrip(b"\0").decode("latin-1")
        if name.startswith("/") and name[1:].isdigit() and strtab:
            at = strtab + int(name[1:])
            end = data.find(b"\0", at)
            if at >= len(data) or end < 0:
                raise ParseError("PE: a long section name lies outside the string table")
            name = data[at:end].decode("latin-1")
        if rawsize and raw + rawsize > len(data):
            raise ParseError(f"PE: section {name} runs past the end of the file")
        sections.append(Section(name, va, vsize, raw, rawsize, flags))
    return Pe(lfanew, coff, opt, plus, headers_size, opt + 64, dirs, dirs_at,
              sections, (symptr, nsyms))


def _loaded(pe: Pe) -> list[tuple[int, int]]:
    return [(0, pe.headers_size)] + [(s.raw, s.raw + s.rawsize) for s in pe.sections
                                     if s.rawsize]


# --------------------------------------------------------------------------- #
# Loci
# --------------------------------------------------------------------------- #
def rich_header(data, pe: Pe) -> tuple[int, int] | None:
    """(start, end) of the Rich header: from the masked `DanS` to `Rich` + key."""
    rich = data.find(b"Rich", 0x40, pe.lfanew)
    if rich < 0 or rich + 8 > pe.lfanew:
        return None
    key = struct.unpack_from("<I", data, rich + 4)[0]
    for at in range(0x40, rich, 4):
        if struct.unpack_from("<I", data, at)[0] ^ key == DANS:
            return at, rich + 8
    return None


def _relocated_pages(data, pe: Pe) -> list[tuple[int, int]]:
    rva, size = pe.dirs[DIR_BASERELOC]
    out = []
    if not size:
        return out
    at = pe.offset(rva, size)
    end = at + size
    while at + 8 <= end:
        page, block = struct.unpack_from("<II", data, at)
        if block < 8:
            break
        out.append((page, page + 0x1000))
        at += block
    return out


def dwarf_sections(data, pe: Pe) -> list[Section]:
    """DWARF sections nothing refers to. One that a relocation patches or a data
    directory points into is refused: it is not debug information alone."""
    out = []
    pages = _relocated_pages(data, pe)
    for s in pe.sections:
        if not (_is_dwarf(s.name) and s.rawsize):
            continue
        end = s.va + max(s.vsize, s.rawsize)
        if any(a < end and s.va < b for a, b in pages) or any(
                size and s.va <= rva < end for rva, size in pe.dirs):
            raise ParseError(f"PE: debug section {s.name} is referenced by the "
                             "program's own tables; zeroing it could change it")
        out.append(s)
    return out


def debug_entries(data, pe: Pe) -> list[tuple[int, int, int, int]]:
    """(entry offset, type, data offset, data size) per debug-directory entry."""
    rva, size = pe.dirs[DIR_DEBUG]
    if not size:
        return []
    at = pe.offset(rva, size)
    out = []
    for i in range(size // 28):
        e = at + 28 * i
        typ, dsize, _drva, dptr = struct.unpack_from("<IIII", data, e + 12)
        if dsize and dptr + dsize > len(data):
            raise ParseError("PE: debug data runs past the end of the file")
        out.append((e, typ, dptr, dsize))
    return out


def file_records(data, pe: Pe) -> list[tuple[int, int]]:
    """Byte ranges of the aux records of every `.file` symbol: source file names."""
    symptr, nsyms = pe.symtab
    out, i = [], 0
    while symptr and i < nsyms:
        at = symptr + 18 * i
        sclass, naux = data[at + 16], data[at + 17]
        if i + 1 + naux > nsyms:
            raise ParseError("PE: a COFF symbol's aux records overrun the table")
        if sclass == C_FILE and naux:
            out.append((at + 18, at + 18 * (1 + naux)))
        i += 1 + naux
    return out


def _resource_tree(data, pe: Pe):
    """Yield ('dir', offset) for every resource directory table and
    ('leaf', type_id, data_offset, size) for every resource."""
    rva, size = pe.dirs[DIR_RESOURCE]
    if not size:
        return
    base = pe.offset(rva, 16)
    seen = set()

    def walk(at: int, depth: int, type_id: int | None):
        if at in seen or depth > 3:
            raise ParseError("PE: resource directory loops")
        seen.add(at)
        yield ("dir", at)
        named, ids = struct.unpack_from("<HH", data, at + 12)
        for i in range(named + ids):
            name, off = struct.unpack_from("<II", data, at + 16 + 8 * i)
            tid = type_id if depth else (None if name & 0x80000000 else name)
            child = base + (off & 0x7FFFFFFF)
            if child + 16 > len(data):
                raise ParseError("PE: a resource entry lies outside the file")
            if off & 0x80000000:
                yield from walk(child, depth + 1, tid)
            else:
                drva, dsize = struct.unpack_from("<II", data, child)
                yield ("leaf", tid, pe.offset(drva, dsize), dsize)

    yield from walk(base, 0, None)


def _version_strings(data, at: int, size: int) -> list[tuple[str, int, int]]:
    """(key, value start, value end) of every String in a VS_VERSIONINFO block."""
    out = []

    def node(pos: int, end: int, depth: int):
        if pos + 6 > end or depth > 4:
            return
        length, vlen, vtype = struct.unpack_from("<HHH", data, pos)
        if length < 6 or pos + length > end:
            return
        key_end = pos + 6
        while key_end + 1 < pos + length and data[key_end:key_end + 2] != b"\0\0":
            key_end += 2
        key = bytes(data[pos + 6:key_end]).decode("utf-16-le", "replace")
        value = (key_end + 2 + 3) & ~3
        if depth == 3 and vtype == 1:               # a String: value is UTF-16 text
            out.append((key, value, min(value + 2 * vlen, pos + length)))
            return
        child = value + (vlen if vtype == 0 else 2 * vlen)
        child = (child + 3) & ~3
        while child + 6 <= pos + length:
            clen = struct.unpack_from("<H", data, child)[0]
            if clen < 6:
                break
            node(child, pos + length, depth + 1)
            child = (child + clen + 3) & ~3

    node(at, at + size, 0)
    return out


def _version_fixed_date(data, at: int, size: int) -> int | None:
    """Offset of VS_FIXEDFILEINFO's dwFileDateMS/LS (8 bytes), if present."""
    sig = data.find(struct.pack("<I", 0xFEEF04BD), at, at + size)
    return sig + 44 if sig >= 0 and sig + 52 <= at + size else None


def _imports(data, pe: Pe) -> set[str]:
    names = set()
    for d, step, name_at in ((DIR_IMPORT, 20, 12), (DIR_DELAY_IMPORT, 32, 4)):
        rva, size = pe.dirs[d]
        if not size:
            continue
        at = pe.offset(rva, 1)
        for i in range(size // step):
            name_rva = struct.unpack_from("<I", data, at + step * i + name_at)[0]
            if not name_rva:
                break
            try:
                s = pe.offset(name_rva)
            except ParseError:
                continue
            names.add(bytes(data[s:data.find(b"\0", s)]).decode("latin-1").lower())
    return names


def can_read_version_info(data, pe: Pe) -> bool:
    return "version.dll" in _imports(data, pe)


def _version_blocks(data, pe: Pe) -> list[tuple[int, int]]:
    return [(at, size) for kind, *rest in _resource_tree(data, pe) if kind == "leaf"
            for tid, at, size in [rest] if tid == RT_VERSION]


# --------------------------------------------------------------------------- #
# F1
# --------------------------------------------------------------------------- #
def _check_scrubbable(pe: Pe) -> None:
    if pe.dirs[DIR_SECURITY][1]:
        raise UnsupportedFormatError(
            "PE: signed with a publisher's certificate (Authenticode). Any edit "
            "invalidates the signature and Windows would then warn about or block "
            "the program, and the certificate is the publisher's identity by "
            "design -- not scrubbed (limit #52)")
    if pe.dirs[DIR_CLR][1]:
        raise UnsupportedFormatError(
            "PE: a .NET assembly. Its metadata -- assembly attributes, a per-build "
            "module GUID -- is a database inside the code that F1 does not model "
            "yet -- not scrubbed (limit #54)")


def _zero(buf: bytearray, at: int, n: int, edits: list) -> None:
    buf[at:at + n] = bytes(n)
    edits.append((at, at + n))


def _drop_debug_entries(buf, data, pe: Pe, edits: list) -> None:
    entries = debug_entries(data, pe)
    if not entries:
        return
    rva, size = pe.dirs[DIR_DEBUG]
    start = pe.offset(rva, size)
    kept = []
    for e, typ, dptr, dsize in entries:
        if typ in _DEBUG_DROP:
            if dsize:
                _zero(buf, dptr, dsize, edits)
        else:
            entry = bytearray(data[e:e + 28])
            entry[4:8] = bytes(4)                           # TimeDateStamp
            kept.append(bytes(entry))
    table = b"".join(kept).ljust(size, b"\0")
    buf[start:start + size] = table
    edits.append((start, start + size))
    new_size = 28 * len(kept)
    struct.pack_into("<II", buf, pe.dirs_at + 8 * DIR_DEBUG,
                     rva if new_size else 0, new_size)
    edits.append((pe.dirs_at + 8 * DIR_DEBUG, pe.dirs_at + 8 * DIR_DEBUG + 8))


def _zero_stamps(buf, data, pe: Pe, edits: list) -> None:
    _zero(buf, pe.coff + 4, 4, edits)
    rva, size = pe.dirs[DIR_EXPORT]
    if size >= 40:
        _zero(buf, pe.offset(rva, 40) + 4, 4, edits)
    rva, size = pe.dirs[DIR_LOAD_CONFIG]
    if size >= 8:
        _zero(buf, pe.offset(rva, 8) + 4, 4, edits)
    for kind, *rest in _resource_tree(data, pe):
        if kind == "dir":
            _zero(buf, rest[0] + 4, 4, edits)


def _blank_version(buf, data, pe: Pe, edits: list) -> None:
    if can_read_version_info(data, pe):
        return
    for at, size in _version_blocks(data, pe):
        for key, start, end in _version_strings(data, at, size):
            if key in VERSION_IDENTITY and end > start:
                _zero(buf, start, end - start, edits)
        date = _version_fixed_date(data, at, size)
        if date is not None:
            _zero(buf, date, 8, edits)


def _text_ids(data, pe: Pe) -> list[tuple[int, int]]:
    text = next((s for s in pe.sections if s.flags & 0x20 and s.rawsize), None)
    if text is None:
        return []
    return [(a, b) for a, b in go.text_build_ids(data[:text.raw + text.rawsize])
            if a >= text.raw]


def _derive_go_ids(buf: bytearray, pe: Pe, ids: list[tuple[int, int]],
                   shapes: list[bytes]) -> None:
    """Go's build ID from a hash of the cleaned file, with the IDs and the checksum
    (which is computed over them) taken as zero."""
    if not ids:
        return
    saved = bytes(buf[pe.checksum_at:pe.checksum_at + 4])
    for a, b in ids:
        buf[a:b] = bytes(b - a)
    buf[pe.checksum_at:pe.checksum_at + 4] = bytes(4)
    digest = hashlib.sha256(buf).digest()
    buf[pe.checksum_at:pe.checksum_at + 4] = saved
    for i, ((a, b), old) in enumerate(zip(ids, shapes, strict=True)):
        buf[a:b] = go.build_id(old, digest + i.to_bytes(2, "big"))


def checksum(data, at: int) -> int:
    """The PE checksum: 16-bit one's-complement sum of the file with the checksum
    field taken as zero, plus the file's length."""
    even = len(data) - len(data) % 2
    words = array.array("H", bytes(data[:even]))
    if sys.byteorder == "big":
        words.byteswap()
    total = sum(words) + (data[-1] if len(data) % 2 else 0)
    total -= int.from_bytes(data[at:at + 2], "little") + \
        int.from_bytes(data[at + 2:at + 4], "little")
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (total + len(data)) & 0xFFFFFFFF


def scrub(data: bytes) -> bytes:
    pe = parse(data)
    _check_scrubbable(pe)
    buf = bytearray(data)
    edits: list[tuple[int, int]] = []
    _zero_stamps(buf, data, pe, edits)
    rich = rich_header(data, pe)
    if rich:
        _zero(buf, rich[0], rich[1] - rich[0], edits)
    _drop_debug_entries(buf, data, pe, edits)
    for s in dwarf_sections(data, pe):
        _zero(buf, s.raw, s.rawsize, edits)
    for a, b in file_records(data, pe):
        _zero(buf, a, b - a, edits)
    _blank_version(buf, data, pe, edits)
    edits += go.blank(buf, data)
    ids = _text_ids(data, pe)
    _derive_go_ids(buf, pe, ids, [bytes(data[a:b]) for a, b in ids])
    edits += ids
    if struct.unpack_from("<I", data, pe.checksum_at)[0]:
        struct.pack_into("<I", buf, pe.checksum_at, checksum(buf, pe.checksum_at))
    edits.append((pe.checksum_at, pe.checksum_at + 4))
    common.check_unchanged(data, buf, _loaded(pe), edits, "PE")
    return bytes(buf)


def residuals(data: bytes) -> list[str]:
    """What a cleaned PE must not still hold, read from the bytes."""
    pe = parse(data)
    out = []
    if struct.unpack_from("<I", data, pe.coff + 4)[0]:
        out.append("the link time is still in the COFF header")
    if rich_header(data, pe):
        out.append("the Rich header (Microsoft toolchain fingerprint) is still there")
    for _, typ, _, _ in debug_entries(data, pe):
        if typ in _DEBUG_DROP:
            out.append(f"a {_DEBUG_DROP[typ]} debug entry is still there")
    out += [f"debug section {s.name} still holds data"
            for s in dwarf_sections(data, pe) if any(data[s.raw:s.raw + s.rawsize])]
    if any(any(data[a:b]) for a, b in file_records(data, pe)):
        out.append("a source-file name is still in the COFF symbol table")
    if not can_read_version_info(data, pe):
        for at, size in _version_blocks(data, pe):
            if any(k in VERSION_IDENTITY and any(data[a:b])
                   for k, a, b in _version_strings(data, at, size)):
                out.append("the version resource still names a company, author or "
                           "original file name")
    out += go.residuals(data)
    ids = _text_ids(data, pe)
    if ids:
        expect = bytearray(data)
        _derive_go_ids(expect, pe, ids, [bytes(data[a:b]) for a, b in ids])
        if any(expect[a:b] != data[a:b] for a, b in ids):
            out.append("the Go build ID is not the one derived from the cleaned file")
    stored = struct.unpack_from("<I", data, pe.checksum_at)[0]
    if stored and stored != checksum(data, pe.checksum_at):
        out.append("the PE checksum does not match the file")
    return out


def advise(data: bytes) -> list[str]:
    pe = parse(data)
    out = common.program_text_advice(common.user_dirs_in(
        data, [(s.name, s.raw, s.raw + s.rawsize) for s in pe.sections
               if s.rawsize and not _is_dwarf(s.name)]))
    if can_read_version_info(data, pe) and _version_blocks(data, pe):
        out.append("this program imports version.dll, so it can read its own version "
                   "information; the company, copyright and original file name in it "
                   "are kept")
    return out + go.advise(data)


def describe(data: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        pe = parse(data)
    except ParseError:
        return out
    ts = struct.unpack_from("<I", data, pe.coff + 4)[0]
    if ts:
        out["Link time (COFF header)"] = str(ts)
    major, minor = data[pe.opt + 2], data[pe.opt + 3]
    out["Linker version"] = f"{major}.{minor}"
    rich = rich_header(data, pe)
    if rich:
        # `DanS` and three padding words (16 bytes), 8 per entry, `Rich` + key (8).
        out["Rich header"] = f"{(rich[1] - rich[0] - 24) // 8} Microsoft tool entries"
    try:
        for _, typ, dptr, dsize in debug_entries(data, pe):
            if typ == DEBUG_CODEVIEW and dsize > 24 and data[dptr:dptr + 4] == b"RSDS":
                out["PDB path"] = bytes(data[dptr + 24:dptr + dsize]).split(b"\0")[0] \
                    .decode("latin-1")
                out["PDB GUID"] = data[dptr + 4:dptr + 20].hex()
        dwarf = dwarf_sections(data, pe)
        if dwarf:
            out["Debug sections"] = (f"{len(dwarf)}, "
                                     f"{sum(s.rawsize for s in dwarf)} bytes")
        files = [bytes(data[a:b]).rstrip(b"\0").decode("latin-1")
                 for a, b in file_records(data, pe)]
        if files:
            out["Source file names (COFF symbols)"] = ", ".join(files[:6])
        for at, size in _version_blocks(data, pe):
            for key, a, b in _version_strings(data, at, size):
                value = bytes(data[a:b]).decode("utf-16-le", "replace").rstrip("\0")
                if value:
                    out[f"Version: {key}"] = value
    except ParseError:
        pass
    out.update(go.describe(data))
    return out
