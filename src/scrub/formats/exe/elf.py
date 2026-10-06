"""ELF at F1: what the build left in the program, removed without moving a byte.

Phase 5 M1 (`docs/p5_executables_plan.md`). The acceptance test is that the program
runs the same, so the rule is the loader's: a section without `SHF_ALLOC` is never
mapped into memory, and nothing the program does can depend on it. Every such
section is zeroed in place -- deny by default -- except the ones the file's own
structure needs (`.symtab`, `.strtab`, `.shstrtab`) and the architecture attributes
tools read. That covers `.comment` (compiler and distribution versions), all DWARF
(the build directory, the full compiler command line), `.gnu_debuglink`, annobin
notes, and whatever a toolchain adds next.

What is loaded is never edited, with two exceptions that are identifiers rather than
behaviour, both measured to leave the program running the same (survey §2):

- the GNU build-id and the Go build ID are **recomputed** from the cleaned bytes (a
  hash of the file with the ID fields zeroed). A stripped build keeps an ID hashed
  from the paths strip removed (survey §3); a recomputed one no longer links to the
  original build, and unlike zeros it does not mark the file as cleaned (limit #9).
- Go's commit stamp (`vcs.revision`, `vcs.time`, and the main module's
  pseudo-version) and the main module's path (`github.com/<name>/...` more often
  than not) are blanked in both copies Go keeps -- but only when the program does
  not link `runtime/debug.ReadBuildInfo`, the one way it can read them.

Paths the program itself reads -- assert, panic and stack-trace locations, RPATH --
are program text: reported by `advise()`, never edited (survey §5).

Nothing moves: every edit has the length of what it replaces, so every offset in
every header stays valid. That is also limit #49: the sizes of the zeroed sections
still show how long what they held was.
"""
from __future__ import annotations

import hashlib
import re
import string
import struct
from dataclasses import dataclass

from ...errors import ParseError, UnsupportedFormatError

MAGIC = b"\x7fELF"
ET_EXEC, ET_DYN = 2, 3
_REFUSED_TYPES = {
    0: "an ELF file of no type",
    1: "a relocatable object (.o): its debug sections are still relocation targets "
       "for the link that has not happened yet",
    4: "a core dump: it is a copy of a process's memory, every byte of which is "
       "content",
}
SHT_SYMTAB, SHT_DYNAMIC, SHT_NOTE, SHT_NOBITS, SHT_SYMTAB_SHNDX = 2, 6, 7, 8, 18
SHF_ALLOC = 0x2
PT_LOAD = 1
STT_FILE = 4
NT_GNU_BUILD_ID, NT_GO_BUILDID = 3, 4
SHN_XINDEX = 0xFFFF
DT_RPATH, DT_RUNPATH = 15, 29

# Unloaded sections that stay. Everything else unloaded is zeroed: an allowlist,
# because a section nobody has seen yet is exactly the one a denylist would miss.
KEEP_UNLOADED = frozenset({".symtab", ".strtab", ".shstrtab",
                           ".ARM.attributes", ".riscv.attributes"})

READ_BUILD_INFO = b"runtime/debug.ReadBuildInfo"
_VCS_LINE = re.compile(rb"build\tvcs\.(?:revision|time)=([^\n]*)\n")
# The main module's version, when Go derived it from the checkout: a pseudo-version
# `vX.Y.Z-[pre.]yyyymmddhhmmss-<12 hex>` carries the commit time and hash too.
_MOD_LINE = re.compile(rb"\nmod\t[^\t\n]*\t(v[^\t\n]*)")
_PSEUDO = re.compile(rb"-(?:[0-9A-Za-z.]*\.)?(\d{14})-([0-9a-f]{12})")
# `path` opens the build info, straight after Go's 16-byte start marker (which
# ends `\xd6\x18\xe6`); `mod` follows it on the next line.
_MODULE_PATH = re.compile(rb"(?:\n|\xd6\x18\xe6)(?:path|mod)\t([^\t\n]+)")
_USER_DIR = re.compile(
    rb"(?:/home/|/Users/)[^/\x00\n]{1,64}/|[A-Za-z]:\\Users\\[^\\\x00\n]{1,64}\\")
_GO_ID_ALPHABET = (string.ascii_letters + string.digits + "-_").encode()


@dataclass(frozen=True)
class Section:
    index: int
    name: str
    type: int
    flags: int
    offset: int
    size: int
    link: int
    align: int
    entsize: int

    @property
    def loaded(self) -> bool:
        return bool(self.flags & SHF_ALLOC)

    @property
    def in_file(self) -> bool:
        return self.type != SHT_NOBITS and self.size > 0 and self.index != 0

    @property
    def end(self) -> int:
        return self.offset + self.size


@dataclass(frozen=True)
class Elf:
    bits: int
    order: str
    etype: int
    machine: int
    sections: list[Section]
    loads: list[tuple[int, int]]          # PT_LOAD file ranges (offset, end)
    shstrndx: int

    def section(self, name: str) -> Section | None:
        return next((s for s in self.sections if s.name == name), None)


def is_elf(data: bytes) -> bool:
    return data[:4] == MAGIC


def parse(data: bytes) -> Elf:
    """The headers, the section table and the loaded ranges -- checked, not trusted.

    Raises ParseError on anything that does not add up: a table past the end of the
    file, a section past the end of the file, names outside the name table. Every
    later edit is confined to ranges this function has bounds-checked.
    """
    if not is_elf(data) or len(data) < 52:
        raise ParseError("not an ELF file")
    cls, enc = data[4], data[5]
    if cls not in (1, 2) or enc not in (1, 2):
        raise ParseError(f"ELF: unknown class {cls} or byte order {enc}")
    bits, order = (32 if cls == 1 else 64), ("<" if enc == 1 else ">")
    hdr = order + ("HHIIIIIHHHHHH" if bits == 32 else "HHIQQQIHHHHHH")
    if len(data) < 16 + struct.calcsize(hdr):
        raise ParseError("ELF: header truncated")
    (etype, machine, _version, _entry, phoff, shoff, _flags, _ehsize, phentsize,
     phnum, shentsize, shnum, shstrndx) = struct.unpack_from(hdr, data, 16)

    ph_fmt = order + ("IIIIIIII" if bits == 32 else "IIQQQQQQ")
    loads = []
    if phnum:
        if phentsize != struct.calcsize(ph_fmt) or phoff + phnum * phentsize > len(data):
            raise ParseError("ELF: program header table does not fit the file")
        for i in range(phnum):
            p = struct.unpack_from(ph_fmt, data, phoff + i * phentsize)
            off, filesz = (p[1], p[4]) if bits == 32 else (p[2], p[5])
            if p[0] == PT_LOAD and filesz:
                loads.append((off, off + filesz))

    if shoff == 0:
        raise ParseError("ELF: no section headers, so its sections cannot be told "
                         "apart from its code -- not scrubbed")
    sh_fmt = order + ("IIIIIIIIII" if bits == 32 else "IIQQQQIIQQ")
    sh_size = struct.calcsize(sh_fmt)
    if shentsize != sh_size:
        raise ParseError(f"ELF: section header size {shentsize}, expected {sh_size}")

    def row(i: int) -> tuple:
        at = shoff + i * sh_size
        if at + sh_size > len(data):
            raise ParseError("ELF: section header table runs past the end of the file")
        return struct.unpack_from(sh_fmt, data, at)

    if shnum == 0:                         # extended numbering: the count is in [0]
        shnum = row(0)[5]
    if shstrndx == SHN_XINDEX:
        shstrndx = row(0)[6]
    if not 0 < shnum or shoff + shnum * sh_size > len(data):
        raise ParseError("ELF: section header table does not fit the file")
    rows = [row(i) for i in range(shnum)]
    if not 0 < shstrndx < shnum:
        raise ParseError("ELF: no section name table")
    names_off, names_size = rows[shstrndx][4], rows[shstrndx][5]
    if names_off + names_size > len(data):
        raise ParseError("ELF: section name table runs past the end of the file")
    names = data[names_off:names_off + names_size]

    sections = []
    for i, r in enumerate(rows):
        name_at, typ, flags, _addr, off, size, link, _info, align, entsize = r
        if i and not name_at < max(len(names), 1):
            raise ParseError(f"ELF: section {i} has a name outside the name table")
        end = names.find(b"\0", name_at)
        name = names[name_at:end if end >= 0 else len(names)].decode("latin-1")
        if i and typ != SHT_NOBITS and off + size > len(data):
            raise ParseError(f"ELF: section {name or i} runs past the end of the file")
        sections.append(Section(i, name, typ, flags, off, size, link, align, entsize))
    return Elf(bits, order, etype, machine, sections, loads, shstrndx)


def _align(n: int, to: int) -> int:
    return (n + to - 1) & ~(to - 1)


def notes(data: bytes, elf: Elf, sec: Section) -> list[tuple[int, bytes, int, int]]:
    """(type, name, descriptor offset, descriptor length) for each note in `sec`."""
    out = []
    a = 8 if sec.align == 8 else 4
    pos, end = sec.offset, sec.end
    while pos + 12 <= end:
        namesz, descsz, ntype = struct.unpack_from(elf.order + "III", data, pos)
        desc = pos + 12 + _align(namesz, a)
        nxt = desc + _align(descsz, a)
        if desc + descsz > end:
            raise ParseError(f"ELF: a note runs past the end of {sec.name}")
        out.append((ntype, bytes(data[pos + 12:pos + 12 + namesz]).rstrip(b"\0"),
                    desc, descsz))
        pos = nxt
    return out


def _build_ids(data, elf: Elf) -> list[tuple[bytes, int, int]]:
    """(owner, offset, length) of every GNU build-id and Go build ID descriptor."""
    out = []
    for s in elf.sections:
        if s.type == SHT_NOTE and s.in_file:
            for ntype, name, at, n in notes(data, elf, s):
                if (name, ntype) in ((b"GNU", NT_GNU_BUILD_ID), (b"Go", NT_GO_BUILDID)):
                    out.append((name, at, n))
    return out


def _unloaded(elf: Elf) -> list[Section]:
    """The sections F1 zeroes: unloaded, holding bytes, and not structure."""
    structure = {elf.shstrndx}
    for s in elf.sections:
        if s.type in (SHT_SYMTAB, SHT_SYMTAB_SHNDX):
            structure |= {s.index, s.link}
    return [s for s in elf.sections
            if s.in_file and not s.loaded and s.name not in KEEP_UNLOADED
            and s.index not in structure]


def _file_symbol_names(data, elf: Elf) -> list[tuple[int, int]]:
    """Byte ranges holding source-file names (`STT_FILE` symbols) that no other
    symbol's name shares. Linkers merge string tails, so `lo.c` may be the end of
    `hello.c`; only the prefix nobody else uses is ever blanked."""
    ranges = []
    for st in elf.sections:
        if st.type != SHT_SYMTAB or not st.in_file:
            continue
        if not st.link < len(elf.sections):
            raise ParseError("ELF: symbol table names a string table that is not there")
        strtab = elf.sections[st.link]
        fmt = elf.order + ("IIIBBH" if elf.bits == 32 else "IBBHQQ")
        ent = struct.calcsize(fmt)
        info_at = 3 if elf.bits == 32 else 1
        files, others = [], {}
        for i in range(st.size // ent):
            vals = struct.unpack_from(fmt, data, st.offset + i * ent)
            name = vals[0]
            if name == 0:
                continue
            if name >= strtab.size:
                raise ParseError("ELF: a symbol name lies outside its string table")
            start = strtab.offset + name
            end = data.find(b"\0", start, strtab.end)
            if end < 0:
                raise ParseError("ELF: a symbol name is not terminated")
            if vals[info_at] & 0xF == STT_FILE:
                files.append((start, end))
            else:
                others[end] = min(start, others.get(end, start))
        for start, end in files:
            ranges.append((start, max(start, min(end, others.get(end, end)))))
    return ranges


def _go_can_read_build_info(data) -> bool:
    return READ_BUILD_INFO in data


def _go_stamps(data) -> list[tuple[int, int]]:
    """Byte ranges of Go's commit stamp: vcs.revision / vcs.time values, and the
    time and hash inside a main-module pseudo-version. Every copy (Go keeps one in
    `.go.buildinfo` for `go version -m` and one in `.rodata` for the program)."""
    out = [m.span(1) for m in _VCS_LINE.finditer(data)]
    for m in _MOD_LINE.finditer(data):
        p = _PSEUDO.search(data, m.start(1), m.end(1))
        if p:
            out += [p.span(1), p.span(2)]
    return out


def _go_module_paths(data) -> list[tuple[int, int]]:
    """The main module's path in the build info's `path` and `mod` lines."""
    return [m.span(1) for m in _MODULE_PATH.finditer(data)]


def _blank_name(buf: bytearray, start: int, end: int) -> None:
    """Letters and digits to `0`; `.`, `/`, `-` and `_` kept, so the line still
    parses as a module path."""
    buf[start:end] = bytes(0x30 if chr(c).isalnum() else c for c in buf[start:end])


def _blank_stamp(buf: bytearray, start: int, end: int) -> None:
    """Hex and digits to `0`, separators kept: `2026-10-06T09:15:51Z` reads
    `0000-00-00T00:00:00Z`, still the shape a parser expects."""
    buf[start:end] = bytes(0x30 if chr(c) in string.hexdigits else c
                           for c in buf[start:end])


def _go_build_id(old: bytes, seed: bytes) -> bytes:
    """A Go build ID of the same shape: the `/` separators where they were, the
    other characters drawn from Go's alphabet by a hash of the cleaned file."""
    stream, counter = b"", 0
    while len(stream) < len(old):
        stream += hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
        counter += 1
    return bytes(c if c == 0x2F else _GO_ID_ALPHABET[stream[i] % 64]
                 for i, c in enumerate(old))


def _recompute_ids(buf: bytearray, ids: list[tuple[bytes, int, int]],
                   go_shapes: list[bytes]) -> None:
    for _, at, n in ids:
        buf[at:at + n] = bytes(n)
    digest = hashlib.sha256(buf).digest()
    shapes = iter(go_shapes)
    for i, (owner, at, n) in enumerate(ids):
        seed = digest + i.to_bytes(2, "big")
        if owner == b"Go":
            buf[at:at + n] = _go_build_id(next(shapes), seed)
        else:
            stream = b"".join(hashlib.sha256(seed + bytes([k])).digest()
                              for k in range(n // 32 + 1))
            buf[at:at + n] = stream[:n]


def _check_scrubbable(elf: Elf) -> None:
    if elf.etype not in (ET_EXEC, ET_DYN):
        raise UnsupportedFormatError(
            "ELF: " + _REFUSED_TYPES.get(elf.etype, f"type {elf.etype}")
            + " -- not scrubbed")
    for s in _unloaded(elf):
        for start, end in elf.loads:
            if s.offset < end and start < s.end:
                raise ParseError(
                    f"ELF: section {s.name} is marked not-loaded but lies inside a "
                    "loaded segment; zeroing it would change the running program")


def scrub(data: bytes) -> bytes:
    elf = parse(data)
    _check_scrubbable(elf)
    buf = bytearray(data)
    for s in _unloaded(elf):
        buf[s.offset:s.end] = bytes(s.size)
    for start, end in _file_symbol_names(data, elf):
        buf[start:end] = bytes(end - start)
    if not _go_can_read_build_info(data):
        for start, end in _go_stamps(data):
            _blank_stamp(buf, start, end)
        for start, end in _go_module_paths(data):
            _blank_name(buf, start, end)
    ids = _build_ids(data, elf)
    go_shapes = [bytes(data[at:at + n]) for owner, at, n in ids if owner == b"Go"]
    _recompute_ids(buf, ids, go_shapes)
    _check_loaded_bytes(data, buf, elf, ids)
    return bytes(buf)


def _check_loaded_bytes(src: bytes, out: bytearray, elf: Elf, ids) -> None:
    """The acceptance rule, enforced on every scrub: inside the loaded segments,
    only the build IDs and the Go stamp may differ. Anything else is a bug in this
    module, and the program would run differently -- so nothing is written."""
    allowed = [(at, at + n) for _, at, n in ids]
    if not _go_can_read_build_info(src):
        allowed += _go_stamps(src) + _go_module_paths(src)
    probe = bytearray(out)
    for a, b in allowed:
        probe[a:b] = src[a:b]
    view_src, view_out = memoryview(src), memoryview(probe)
    for start, end in elf.loads:
        if view_src[start:end] != view_out[start:end]:
            first = next(i for i in range(start, end) if src[i] != probe[i])
            raise ParseError(f"ELF F1 changed loaded byte {first:#x}; refused")


def residuals(data: bytes) -> list[str]:
    """What a cleaned ELF must not still hold. Reads bytes, not a tool's report."""
    elf = parse(data)
    out = [f"section {s.name} still holds data" for s in _unloaded(elf)
           if any(data[s.offset:s.end])]
    if any(any(data[a:b]) for a, b in _file_symbol_names(data, elf)):
        out.append("a source-file name is still in the symbol table")
    if not _go_can_read_build_info(data):
        for a, b in _go_stamps(data):
            if any(c not in b"0-:TZ" for c in data[a:b]):
                out.append("Go's commit stamp is still in the build info")
                break
        if any(chr(c).isalnum() and c != 0x30 for a, b in _go_module_paths(data)
               for c in data[a:b]):
            out.append("the Go module path is still in the build info")
    ids = _build_ids(data, elf)
    if ids:
        expect = bytearray(data)
        _recompute_ids(expect, ids, [bytes(data[a:a + n]) for o, a, n in ids
                                     if o == b"Go"])
        if any(expect[a:a + n] != data[a:a + n] for _, a, n in ids):
            out.append("a build ID is not the one derived from the cleaned file")
    return out


def advise(data: bytes) -> list[str]:
    """What stays because the program itself reads it (survey §5)."""
    elf = parse(data)
    out = []
    found: dict[str, str] = {}
    for s in elf.sections:
        if s.loaded and s.in_file:
            for m in _USER_DIR.finditer(data, s.offset, s.end):
                found.setdefault(m.group().decode("latin-1"), s.name)
    if found:
        where = ", ".join(f"{p}... in {sec}" for p, sec in list(found.items())[:3])
        out.append("the program's own text contains a user directory path, which it "
                   f"can print (an assert, panic or stack trace), so it is kept: "
                   f"{where}. Rebuilding with path remapping removes it (limit #50).")
    for path in _runpaths(data, elf):
        if _USER_DIR.search(path):
            out.append(f"the library search path {path.decode('latin-1')!r} names a "
                       "user directory; the loader reads it, so it is kept (limit #50)")
    if _go_can_read_build_info(data) and (_go_stamps(data) or _go_module_paths(data)):
        out.append("this Go program can read its own build information "
                   "(runtime/debug.ReadBuildInfo), so its module path and any commit "
                   "hash and time in it are kept; -buildvcs=false leaves the commit "
                   "out at build time")
    paths = {bytes(data[a:b]) for a, b in _go_module_paths(data)}
    elsewhere = [p for p in paths if p != b"command-line-arguments"
                 and data.count(p) > sum(1 for a, b in _go_module_paths(data)
                                         if data[a:b] == p)]
    if elsewhere:
        out.append(f"the Go module path {elsewhere[0].decode('latin-1')!r} is also "
                   "in the program's function names, which stack traces print, so "
                   "those copies are kept (limit #50)")
    return out


def _runpaths(data: bytes, elf: Elf) -> list[bytes]:
    dyn = next((s for s in elf.sections if s.type == SHT_DYNAMIC and s.in_file), None)
    if dyn is None or not dyn.link < len(elf.sections):
        return []
    strtab = elf.sections[dyn.link]
    fmt = elf.order + ("iI" if elf.bits == 32 else "qQ")
    ent = struct.calcsize(fmt)
    out = []
    for i in range(dyn.size // ent):
        tag, val = struct.unpack_from(fmt, data, dyn.offset + i * ent)
        if tag == 0:
            break
        if tag in (DT_RPATH, DT_RUNPATH) and val < strtab.size:
            start = strtab.offset + val
            out.append(bytes(data[start:data.find(b"\0", start, strtab.end)]))
    return out


def describe(data: bytes) -> dict[str, str]:
    """What the build left, for the scrub report."""
    out: dict[str, str] = {}
    try:
        elf = parse(data)
    except ParseError:
        return out
    comment = elf.section(".comment")
    if comment is not None and comment.in_file:
        lines = [x.decode("latin-1") for x in
                 bytes(data[comment.offset:comment.end]).split(b"\0") if x]
        if lines:
            out["Compiler (.comment)"] = " | ".join(dict.fromkeys(lines))
    try:
        for owner, at, n in _build_ids(data, elf):
            value = data[at:at + n]
            out[f"{owner.decode()} build ID"] = (value.decode("latin-1")
                                                 if owner == b"Go" else value.hex())
    except ParseError:
        pass
    debug = [s for s in _unloaded(elf) if s.name != ".comment"]
    if debug:
        out["Debug and other unloaded sections"] = (
            f"{len(debug)}, {sum(s.size for s in debug)} bytes: "
            + ", ".join(s.name for s in debug[:6]) + (" ..." if len(debug) > 6 else ""))
    try:
        files = {bytes(data[a:b]).decode("latin-1") for a, b in
                 _file_symbol_names(data, elf) if b > a}
    except ParseError:
        files = set()
    if files:
        out["Source file names (symbol table)"] = ", ".join(sorted(files)[:6])
    for m in _VCS_LINE.finditer(data):
        key = m.group(0).split(b"=")[0].split(b"\t")[1].decode()
        out.setdefault(f"Go {key}", m.group(1).decode("latin-1"))
    return out
