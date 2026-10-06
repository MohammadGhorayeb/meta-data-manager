"""Mach-O at F1: what the build left in a Mac program, removed without moving a byte.

Phase 5 M2 (`docs/p5_executables_plan.md` §1, §2, §9). The rule mirrors ELF's, in
Mach-O's terms. A segment with no memory size is never mapped -- Go's `__DWARF` is
one -- so it is zeroed. Everything mapped is kept byte for byte except:

- the **debug map**: the stab symbols naming the build directory, every object
  file (in the per-user temp directory for a one-step build) and, for Rust, the
  home directory (`~/.rustup/...`, in every plain `rustc -O` build). Their strings
  are blanked and `N_OSO`'s value -- the object file's modification time, a build
  timestamp to the second -- zeroed. The loader never reads stabs.
- `LC_UUID`, recomputed from the cleaned bytes. A one-step `clang -g` build's UUID
  is random per build and matches that build's debug symbols and crash reports;
  `strip` keeps it (survey §3). The recomputed one keeps the version-3 bits `ld64`
  sets on its own deterministic UUIDs.
- Go's build ID at the start of the text, and its build info (`go.py`).
- the code signature: identifier rewritten, page hashes recomputed (`codesign.py`),
  which is what lets any of the above run at all on Apple Silicon.

A universal (fat) file is each of its slices, cleaned in place; the fat header's
offsets stay valid because nothing changes size. Program text (paths in `__cstring`,
Go's pclntab, a dylib's install name, `LC_RPATH`) is kept and reported (limit #50).
"""
from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field

from ...errors import ParseError, UnsupportedFormatError
from . import codesign, common, go

MH_MAGIC, MH_MAGIC_64 = 0xFEEDFACE, 0xFEEDFACF
FAT_MAGIC, FAT_MAGIC_64 = 0xCAFEBABE, 0xCAFEBABF
MH_EXECUTE, MH_DYLIB, MH_BUNDLE = 2, 6, 8
_REFUSED_TYPES = {
    1: "a compiler object (.o): its debug information is still waiting for the link",
    4: "a core dump: it is a copy of a process's memory, every byte of which is "
       "content",
    10: "a dSYM debug-symbol file: it IS the debug information, nothing in it is "
        "anything but what F1 removes",
}
LC_SEGMENT, LC_SEGMENT_64, LC_SYMTAB, LC_UUID = 0x1, 0x19, 0x2, 0x1B
LC_CODE_SIGNATURE, LC_BUILD_VERSION = 0x1D, 0x32
LC_ID_DYLIB, LC_LOAD_DYLIB, LC_RPATH = 0xD, 0xC, 0x8000001C
_DYLIB_PATHS = {0xC, 0xD, 0x18 | 0x80000000, 0x1F | 0x80000000, 0x23 | 0x80000000}
N_STAB, N_OSO = 0xE0, 0x66
# Stab types whose string is a path: source directory and file, object file,
# included file, Swift module, include-file begin.
STAB_PATHS = {0x64: "N_SO", 0x66: "N_OSO", 0x84: "N_SOL", 0x32: "N_AST",
              0x82: "N_BINCL"}
# Java class files share 0xCAFEBABE. Their next four bytes are the class-file
# version, whose major part starts at 45, so a fat header claims fewer.
_MAX_FAT_ARCHS = 45


@dataclass
class Segment:
    name: str
    vmsize: int
    fileoff: int
    filesize: int
    sections: list[tuple[str, int, int]] = field(default_factory=list)

    @property
    def mapped(self) -> bool:
        return self.vmsize > 0


@dataclass
class Slice:
    bits: int
    order: str
    filetype: int
    segments: list[Segment]
    symtab: tuple[int, int, int, int] | None = None      # symoff nsyms stroff strsize
    uuid_at: int | None = None
    signature: tuple[int, int] | None = None
    dylib_paths: list[bytes] = field(default_factory=list)
    build_version: str = ""


def is_macho(data: bytes) -> bool:
    if len(data) < 8:
        return False
    le, be = struct.unpack_from("<I", data)[0], struct.unpack_from(">I", data)[0]
    if le in (MH_MAGIC, MH_MAGIC_64) or be in (MH_MAGIC, MH_MAGIC_64):
        return True
    return be in (FAT_MAGIC, FAT_MAGIC_64) and \
        0 < struct.unpack_from(">I", data, 4)[0] < _MAX_FAT_ARCHS


def slices(data: bytes) -> list[tuple[int, int]]:
    """(offset, size) of each architecture: the whole file when thin."""
    magic = struct.unpack_from(">I", data)[0]
    if magic not in (FAT_MAGIC, FAT_MAGIC_64):
        return [(0, len(data))]
    n = struct.unpack_from(">I", data, 4)[0]
    if not 0 < n < _MAX_FAT_ARCHS:
        raise ParseError("Mach-O: not a universal binary (a Java class file?)")
    fmt, step = (">IIIII", 20) if magic == FAT_MAGIC else (">IIQQII", 32)
    if 8 + n * step > len(data):
        raise ParseError("Mach-O: universal header truncated")
    out = []
    for i in range(n):
        _cpu, _sub, off, size = struct.unpack_from(fmt, data, 8 + i * step)[:4]
        if off < 8 + n * step or off + size > len(data):
            raise ParseError("Mach-O: a universal slice lies outside the file")
        out.append((off, size))
    out.sort()
    if any(a[0] + a[1] > b[0] for a, b in zip(out, out[1:], strict=False)):
        raise ParseError("Mach-O: universal slices overlap")
    return out


def parse_slice(data: bytes) -> Slice:
    if len(data) < 28:
        raise ParseError("Mach-O: header truncated")
    for order in ("<", ">"):
        magic = struct.unpack_from(order + "I", data)[0]
        if magic in (MH_MAGIC, MH_MAGIC_64):
            break
    else:
        raise ParseError("not a Mach-O file")
    bits = 64 if magic == MH_MAGIC_64 else 32
    _cpu, _sub, filetype, ncmds, sizeofcmds, _flags = struct.unpack_from(
        order + "IIIIII", data, 4)
    pos = 32 if bits == 64 else 28
    if pos + sizeofcmds > len(data):
        raise ParseError("Mach-O: load commands run past the end of the file")
    m = Slice(bits, order, filetype, [])
    end = pos + sizeofcmds
    for _ in range(ncmds):
        if pos + 8 > end:
            raise ParseError("Mach-O: load commands overrun their declared size")
        cmd, size = struct.unpack_from(order + "II", data, pos)
        if size < 8 or pos + size > end:
            raise ParseError(f"Mach-O: load command {cmd:#x} has a bad size")
        _command(data, m, cmd, pos, size)
        pos += size
    for seg in m.segments:
        if seg.filesize and seg.fileoff + seg.filesize > len(data):
            raise ParseError(f"Mach-O: segment {seg.name} runs past the end of the file")
    return m


def _command(data: bytes, m: Slice, cmd: int, pos: int, size: int) -> None:
    o = m.order
    if cmd in (LC_SEGMENT, LC_SEGMENT_64):
        wide = cmd == LC_SEGMENT_64
        fmt = o + ("16sQQQQiiII" if wide else "16sIIIIiiII")
        name, _vmaddr, vmsize, fileoff, filesize, _maxp, _initp, nsects, _f = \
            struct.unpack_from(fmt, data, pos + 8)
        seg = Segment(name.rstrip(b"\0").decode("latin-1"), vmsize, fileoff, filesize)
        sect_fmt = o + ("16s16sQQIIIIIIII" if wide else "16s16sIIIIIIIII")
        step = struct.calcsize(sect_fmt)
        at = pos + 8 + struct.calcsize(fmt)
        if at + nsects * step > pos + size:
            raise ParseError(f"Mach-O: segment {seg.name}'s sections overrun it")
        for i in range(nsects):
            v = struct.unpack_from(sect_fmt, data, at + i * step)
            seg.sections.append((v[0].rstrip(b"\0").decode("latin-1"), v[4], v[3]))
        m.segments.append(seg)
    elif cmd == LC_SYMTAB:
        m.symtab = struct.unpack_from(o + "IIII", data, pos + 8)
    elif cmd == LC_UUID:
        m.uuid_at = pos + 8
    elif cmd == LC_CODE_SIGNATURE:
        m.signature = struct.unpack_from(o + "II", data, pos + 8)
    elif cmd in _DYLIB_PATHS or cmd == LC_RPATH:
        off = struct.unpack_from(o + "I", data, pos + 8)[0]
        if off < size:
            m.dylib_paths.append(bytes(data[pos + off:pos + size]).split(b"\0")[0])
    elif cmd == LC_BUILD_VERSION:
        platform, minos, sdk, ntools = struct.unpack_from(o + "IIII", data, pos + 8)
        ver = lambda v: f"{v >> 16}.{(v >> 8) & 0xFF}.{v & 0xFF}"   # noqa: E731
        tools = [struct.unpack_from(o + "II", data, pos + 24 + 8 * i)
                 for i in range(min(ntools, (size - 24) // 8))]
        m.build_version = (f"platform {platform}, min OS {ver(minos)}, SDK {ver(sdk)}"
                           + "".join(f", tool {t} {ver(v)}" for t, v in tools))


def _mapped(m: Slice) -> list[tuple[int, int]]:
    return [(s.fileoff, s.fileoff + s.filesize) for s in m.segments
            if s.mapped and s.filesize]


def _unmapped(m: Slice) -> list[Segment]:
    return [s for s in m.segments if not s.mapped and s.filesize]


def _stab_paths(data, m: Slice) -> tuple[list[tuple[int, int]], list[int]]:
    """(string ranges to blank, offsets of N_OSO values) in the symbol table.
    A string another, non-path symbol shares the tail of is blanked only up to
    where the sharing starts."""
    if m.symtab is None:
        return [], []
    symoff, nsyms, stroff, strsize = m.symtab
    ent = 16 if m.bits == 64 else 12
    if symoff + nsyms * ent > len(data) or stroff + strsize > len(data):
        raise ParseError("Mach-O: the symbol table runs past the end of the file")
    paths, others, mtimes = [], {}, []
    for i in range(nsyms):
        at = symoff + i * ent
        strx, ntype = struct.unpack_from(m.order + "IB", data, at)
        is_path = bool(ntype & N_STAB) and ntype in STAB_PATHS
        if is_path and ntype == N_OSO:
            mtimes.append(at + 8)
        if strx == 0:
            continue
        if strx >= strsize:
            raise ParseError("Mach-O: a symbol name lies outside the string table")
        start = stroff + strx
        end = data.find(b"\0", start, stroff + strsize)
        if end < 0:
            raise ParseError("Mach-O: a symbol name is not terminated")
        if is_path:
            paths.append((start, end))
        else:
            others[end] = min(start, others.get(end, start))
    return ([(s, max(s, min(e, others.get(e, e)))) for s, e in paths], mtimes)


def _check_scrubbable(data, m: Slice) -> codesign.Signature | None:
    if m.filetype not in (MH_EXECUTE, MH_DYLIB, MH_BUNDLE):
        raise UnsupportedFormatError(
            "Mach-O: " + _REFUSED_TYPES.get(m.filetype, f"file type {m.filetype}")
            + " -- not scrubbed")
    for seg in _unmapped(m):
        for start, end in _mapped(m):
            if seg.fileoff < end and start < seg.fileoff + seg.filesize:
                raise ParseError(f"Mach-O: unmapped segment {seg.name} overlaps a "
                                 "mapped one; zeroing it would change the program")
    if m.signature is None:
        return None
    sig = codesign.parse(data, *m.signature)
    if not sig.adhoc or (sig.cms_length or 0) > 8 or any(
            codesign.team(data, cd) for cd in sig.directories):
        who = next((codesign.team(data, cd) for cd in sig.directories
                    if codesign.team(data, cd)), b"")
        raise UnsupportedFormatError(
            "Mach-O: signed with a developer identity"
            + (f" (team {who.decode('latin-1')})" if who else "")
            + ". Any edit invalidates that signature and the program would then be "
            "blocked on the recipient's Mac, and the certificate is the publisher's "
            "identity by design -- not scrubbed (limit #52)")
    return sig


def _derived_ids(buf: bytearray, m: Slice, ids: list[tuple[str, int, int]],
                 shapes: list[bytes]) -> None:
    """UUID and Go build ID from a hash of the cleaned slice, with the ID fields
    zeroed and the signature (which hashes them) left out."""
    for _, at, n in ids:
        buf[at:at + n] = bytes(n)
    h = hashlib.sha256()
    if m.signature:
        at, size = m.signature
        h.update(memoryview(buf)[:at])
        h.update(memoryview(buf)[at + size:])
    else:
        h.update(buf)
    digest = h.digest()
    shapes_it = iter(shapes)
    for kind, at, n in ids:
        if kind == "uuid":
            u = bytearray(hashlib.sha256(digest + b"uuid").digest()[:16])
            u[6] = (u[6] & 0x0F) | 0x30
            u[8] = (u[8] & 0x3F) | 0x80
            buf[at:at + n] = u
        else:
            buf[at:at + n] = go.build_id(next(shapes_it), digest + kind.encode())


def _ids(data, m: Slice) -> list[tuple[str, int, int]]:
    ids = [("uuid", m.uuid_at, 16)] if m.uuid_at is not None else []
    text = next((s for s in m.segments if s.name == "__TEXT"), None)
    if text is not None:
        ids += [(f"go{i}", a, b - a) for i, (a, b) in enumerate(
            go.text_build_ids(data[:text.fileoff + text.filesize]))]
    return ids


def _scrub_slice(src: bytes) -> bytes:
    m = parse_slice(src)
    sig = _check_scrubbable(src, m)
    buf = bytearray(src)
    for seg in _unmapped(m):
        buf[seg.fileoff:seg.fileoff + seg.filesize] = bytes(seg.filesize)
    paths, mtimes = _stab_paths(src, m)
    for start, end in paths:
        buf[start:end] = bytes(end - start)
    for at in mtimes:
        buf[at:at + (8 if m.bits == 64 else 4)] = bytes(8 if m.bits == 64 else 4)
    edited = go.blank(buf, src)
    ids = _ids(src, m)
    _derived_ids(buf, m, ids, [bytes(src[a:a + n]) for k, a, n in ids if k != "uuid"])
    allowed = paths + [(a, a + 8) for a in mtimes] + edited + \
        [(a, a + n) for _, a, n in ids]
    if sig is not None:
        codesign.rename(buf, sig)
        codesign.resign(buf, sig)
        allowed.append((sig.at, sig.at + sig.size))
    common.check_unchanged(src, buf, _mapped(m), allowed, "Mach-O")
    return bytes(buf)


def scrub(data: bytes) -> bytes:
    buf = bytearray(data)
    for start, size in slices(data):
        buf[start:start + size] = _scrub_slice(bytes(data[start:start + size]))
    return bytes(buf)


def residuals(data: bytes) -> list[str]:
    """What a cleaned Mach-O must not still hold, read from the bytes -- including
    our own check of the signature the kernel will check at launch."""
    out = []
    for start, size in slices(data):
        s = bytes(data[start:start + size])
        m = parse_slice(s)
        out += [f"segment {g.name} still holds data" for g in _unmapped(m)
                if any(s[g.fileoff:g.fileoff + g.filesize])]
        paths, mtimes = _stab_paths(s, m)
        if any(any(s[a:b]) for a, b in paths):
            out.append("the debug map still names a build path or object file")
        if any(any(s[a:a + 8 if m.bits == 64 else a + 4]) for a in mtimes):
            out.append("the debug map still records an object file's time")
        out += go.residuals(s)
        ids = _ids(s, m)
        if ids:
            expect = bytearray(s)
            _derived_ids(expect, m, ids,
                         [bytes(s[a:a + n]) for k, a, n in ids if k != "uuid"])
            if any(expect[a:a + n] != s[a:a + n] for _, a, n in ids):
                out.append("the UUID or Go build ID is not the one derived from the "
                           "cleaned file")
        if m.signature is not None:
            sig = codesign.parse(s, *m.signature)
            out += codesign.verify(s, sig)
            for cd in sig.directories:
                name = codesign.identifier(s, cd)
                if name != codesign.CANONICAL_IDENTIFIER and \
                        codesign._identifier_room(cd) > len(codesign.CANONICAL_IDENTIFIER):
                    out.append(f"the signature still names the program {name!r}")
    return out


def advise(data: bytes) -> list[str]:
    out, found = [], {}
    for start, size in slices(data):
        s = bytes(data[start:start + size])
        m = parse_slice(s)
        # Sections, not segments: `__TEXT` also maps the load commands, whose paths
        # (install names, rpaths) are reported below as what the loader reads.
        found.update(common.user_dirs_in(s, [
            (f"{g.name},{name}", off, off + size) for g in m.segments
            if g.mapped and g.name != "__LINKEDIT" for name, off, size in g.sections
            if off and size and off + size <= len(s)]))
        for path in m.dylib_paths:
            if common.USER_DIR.search(path):
                out.append(f"the library path {path.decode('latin-1')!r} names a user "
                           "directory; the loader reads it, so it is kept (limit #50)")
        if any(name == "__info_plist" for g in m.segments for name, _, _ in g.sections):
            out.append("an Info.plist is embedded in the program, which the program "
                       "can read, so it is kept")
        if m.signature is not None:
            sig = codesign.parse(s, *m.signature)
            for cd in sig.directories:
                name = codesign.identifier(s, cd)
                if codesign._identifier_room(cd) <= len(codesign.CANONICAL_IDENTIFIER) \
                        and name != codesign.CANONICAL_IDENTIFIER:
                    out.append(f"the signature names the program "
                               f"'{name.decode('latin-1')}' (its name "
                               "when it was linked); too short to replace in place, "
                               "so it is kept")
    return common.program_text_advice(found) + list(dict.fromkeys(out)) + \
        go.advise(data)


def describe(data: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        parts = slices(data)
    except ParseError:
        return out
    for n, (start, size) in enumerate(parts):
        s = bytes(data[start:start + size])
        tag = f"[{n}] " if len(parts) > 1 else ""
        try:
            m = parse_slice(s)
        except ParseError:
            continue
        if m.uuid_at is not None:
            out[f"{tag}UUID"] = s[m.uuid_at:m.uuid_at + 16].hex()
        if m.build_version:
            out[f"{tag}Build version"] = m.build_version
        try:
            paths, mtimes = _stab_paths(s, m)
        except ParseError:
            paths, mtimes = [], []
        named = [s[a:b].decode("latin-1") for a, b in paths if b > a]
        if named:
            out[f"{tag}Debug map"] = (f"{len(named)} paths, e.g. "
                                      + ", ".join(named[:3]))
        stamps = sorted({struct.unpack_from(m.order + ("Q" if m.bits == 64 else "I"),
                                            s, a)[0] for a in mtimes} - {0})
        if stamps:
            out[f"{tag}Object file times"] = ", ".join(str(t) for t in stamps[:3])
        dwarf = _unmapped(m)
        if dwarf:
            out[f"{tag}Unmapped segments"] = ", ".join(
                f"{g.name} ({g.filesize} bytes)" for g in dwarf)
        if m.signature is not None:
            try:
                sig = codesign.parse(s, *m.signature)
                cd = sig.directories[0]
                kind = ("ad hoc, by the linker" if sig.linker_signed else "ad hoc"
                        if sig.adhoc else "developer identity")
                out[f"{tag}Code signature"] = (
                    f"{kind}; identifier {codesign.identifier(s, cd).decode('latin-1')}")
            except ParseError:
                pass
    out.update(go.describe(data))
    return out
