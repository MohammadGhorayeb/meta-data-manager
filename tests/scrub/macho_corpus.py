"""Mach-O corpus for CI: Mac programs written byte by byte, plus real builds where
macOS and a compiler exist.

The hand-built files carry every locus the Phase 5 survey measured
(`docs/p5_executables_plan.md` §1, §9) with a value derived from who built it: a
debug map naming the home directory, the per-user temp directory and an object
file's modification time; a Swift module path; an unmapped `__DWARF` segment; a
UUID; a code signature whose identifier is the link-time name (or, `codesign`
style, carries the UUID); Go's build ID and build info; a dylib install name.
They are signed ad hoc by this module's own signer -- a second implementation of
the page-hash scheme, so a misreading in the scrubber's cannot verify itself.

The code bytes are filler, so they never run; whether a scrubbed program still RUNS
is the real builds' job (`compile()`), which needs macOS.

Imports nothing from `src` (a test walks the imports).
"""
from __future__ import annotations

import hashlib
import os
import shutil
import struct
import subprocess
import sys

from . import elf_corpus as ec  # Go build info is the same in every container

CPU_ARM64, CPU_X86_64, CPU_PPC = 0x0100000C, 0x01000007, 18
MH_OBJECT, MH_EXECUTE, MH_DYLIB, MH_DSYM = 1, 2, 6, 10
PRODUCER = b"Apple clang version SENTINEL (clang-1500.3.9.4)"
SOURCE_FILE = b"sentinel_tool.c"
CODE = bytes(range(0x20, 0xA0))
PROGRAM_TEXT = b"hello: %d args\n\0"
PAGE = 4096


def home(user: str) -> bytes:
    return f"/Users/{user}/proj/".encode()


def temp_object(user: str) -> bytes:
    tag = hashlib.sha1(user.encode()).hexdigest()
    return f"/private/var/folders/{tag[:2]}/{tag[2:30]}/T/sentinel_tool-{tag[30:36]}.o" \
        .encode()


def swift_module(user: str) -> bytes:
    return home(user) + b"Sentinel.swiftmodule"


def mtime(user: str) -> int:
    return 1_791_279_045 + sum(user.encode())


def uuid(user: str) -> bytes:
    u = bytearray(hashlib.md5(b"uuid " + user.encode()).digest())
    u[6] = (u[6] & 0x0F) | 0x40                 # random (v4), as one-step clang -g
    return bytes(u)


def link_name(user: str, style: str = "linker") -> bytes:
    if style == "codesign":
        return b"sentinel_tool-55554944" + uuid(user).hex().encode()
    return b"sentinel_tool-arm64.out"


def install_name(user: str) -> bytes:
    return home(user) + b"libsentinel.dylib"


def _cd(code: bytes, ident: bytes, flags: int, team: bytes = b"") -> bytes:
    """An ad-hoc CodeDirectory (version 0x20400) over `code`, 4 KiB pages, SHA-256."""
    n = -(-len(code) // PAGE)
    header_len = 88
    ident_at = header_len
    team_at = ident_at + len(ident) + 1 if team else 0
    hash_at = header_len + len(ident) + 1 + (len(team) + 1 if team else 0)
    hashes = b"".join(hashlib.sha256(code[i * PAGE:(i + 1) * PAGE]).digest()
                      for i in range(n))
    length = hash_at + len(hashes)
    head = struct.pack(">IIIIIIIIIBBBBIIIIQQQQ", 0xFADE0C02, length, 0x20400, flags,
                       hash_at, ident_at, 0, n, len(code), 32, 2, 0, 12, 0, 0,
                       team_at, 0, 0, 0, 0, 0)
    head = head.ljust(header_len, b"\0")
    return head + ident + b"\0" + (team + b"\0" if team else b"") + hashes


def _superblob(blobs: list[tuple[int, bytes]]) -> bytes:
    at = 12 + 8 * len(blobs)
    index, body = b"", b""
    for btype, blob in blobs:
        index += struct.pack(">II", btype, at + len(body))
        body += blob
    return struct.pack(">III", 0xFADE0CC0, at + len(body), len(blobs)) + index + body


def build(user: str = "alice", *, filetype: int = MH_EXECUTE, cpu: int = CPU_ARM64,
          bits: int = 64, order: str = "<", signed: bool = True,
          sign_style: str = "linker", identity: bool = False, go: bool = False,
          read_build_info: bool = False, text_path: bool = False,
          dwarf_overlaps: bool = False, seed: int = 0) -> bytes:
    """One Mach-O slice whose every build-dependent value derives from `user`."""
    o = order
    seg_fmt = o + ("II16sQQQQiiII" if bits == 64 else "II16sIIIIiiII")
    sect_fmt = o + ("16s16sQQIIIIIIII" if bits == 64 else "16s16sIIIIIIIII")
    hdr_len = 32 if bits == 64 else 28
    seg_len, sect_len = struct.calcsize(seg_fmt), struct.calcsize(sect_fmt)

    cstring = (PROGRAM_TEXT if not seed else ec.program_text(seed)) + (
        home(user) + b"hello.c\0" if text_path else b"")
    text_code = CODE if not seed else ec.code(seed)
    if go:
        text_code = (b'\xff Go build ID: "' + ec.go_build_id(user) + b'"\n \xff'
                     + text_code)
        cstring += ec.modinfo(user, seed) + (ec.READ_BUILD_INFO + b"\0"
                                             if read_build_info else b"")
    data_sect = ec.modinfo(user, seed) if go else bytes([1, 2, 3, 4 + seed])

    # Load commands other than the segments, sized first so offsets can be fixed.
    dylib = filetype == MH_DYLIB
    id_dylib = b""
    if dylib:
        name = install_name(user) + b"\0"
        size = (24 + len(name) + 7) & ~7
        id_dylib = struct.pack(o + "IIIIII", 0xD, size, 24, 0, 0x10000, 0x10000) \
            + name.ljust(size - 24, b"\0")
    n_segs = 5
    sizeofcmds = (n_segs * seg_len + 3 * sect_len + 24 + 24 + 32
                  + len(id_dylib) + (16 if signed else 0))
    text_start = hdr_len + sizeofcmds
    text_off = (text_start + 15) & ~15
    cstr_off = text_off + len(text_code)
    text_end = cstr_off + len(cstring)
    data_off = (text_end + PAGE - 1) & ~(PAGE - 1)
    dwarf_off = data_off + len(data_sect)
    dwarf = b"\0" * 11 + PRODUCER + b"\0" + home(user) + b"\0" + SOURCE_FILE + b"\0"
    link_off = (dwarf_off + len(dwarf) + 15) & ~15

    # Symbol table: the debug map, then `_main`.
    strtab = bytearray(b" \0")
    def s(name: bytes) -> int:
        at = len(strtab)
        strtab.extend(name + b"\0")
        return at
    syms = [(0, 0x32, 0, 0, swift_module(user)), (0, 0x64, 0, 0, b""),
            (0, 0x64, 0, 0, home(user)), (0, 0x64, 0, 0, SOURCE_FILE),
            (0, 0x66, 0, 1, temp_object(user)), (0, 0x2E, 1, 0, b"_main"),
            (0, 0x24, 1, 0, b"_main"), (0, 0x4E, 1, 0, b""), (0, 0x64, 0, 0, b""),
            (0, 0x0F, 1, 0, b"_main")]
    nl_fmt = o + ("IBBHQ" if bits == 64 else "IBBHI")
    symtab = b"".join(
        struct.pack(nl_fmt, s(name) if name else 0, ntype, sect, desc,
                    mtime(user) if ntype == 0x66 else 0)
        for _, ntype, sect, desc, name in syms)
    sym_off = link_off
    str_off = sym_off + len(symtab)
    strtab_b = bytes(strtab).ljust((len(strtab) + 15) & ~15, b"\0")
    sig_off = str_off + len(strtab_b)

    def seg(name, vmaddr, vmsize, fileoff, filesize, sects):
        body = struct.pack(seg_fmt, 0x19 if bits == 64 else 0x1,
                           seg_len + sect_len * len(sects), name.ljust(16, b"\0"),
                           vmaddr, vmsize, fileoff, filesize, 7, 5 if vmsize else 0,
                           len(sects), 0)
        for sname, addr, size, off in sects:
            body += struct.pack(sect_fmt, sname.ljust(16, b"\0"), name.ljust(16, b"\0"),
                                addr, size, off, 4, 0, 0, 0, 0, 0,
                                *(() if bits == 32 else (0,)))
        return body

    base = 0x100000000 if bits == 64 else 0x1000
    cmds = seg(b"__PAGEZERO", 0, base, 0, 0, [])
    cmds += seg(b"__TEXT", base, data_off, 0, text_end,
                [(b"__text", base + text_off, len(text_code), text_off),
                 (b"__cstring", base + cstr_off, len(cstring), cstr_off)])
    cmds += seg(b"__DATA", base + data_off, PAGE, data_off, len(data_sect),
                [(b"__go_buildinfo" if go else b"__data", base + data_off,
                  len(data_sect), data_off)])
    # Never mapped (no memory size). The overlap variant points it into __DATA.
    cmds += seg(b"__DWARF", 0, 0, data_off if dwarf_overlaps else dwarf_off,
                len(dwarf), [])
    link_size_placeholder = 0
    cmds += seg(b"__LINKEDIT", base + link_off + PAGE, PAGE, link_off,
                link_size_placeholder, [])
    cmds += struct.pack(o + "IIIIII", 0x2, 24, sym_off, len(syms), str_off,
                        len(strtab_b))
    cmds += struct.pack(o + "II", 0x1B, 24) + uuid(user)
    cmds += struct.pack(o + "IIIIII", 0x32, 32, 1, 0x000E0400, 0x000E0400, 1) \
        + struct.pack(o + "II", 3, (1053 << 16) | (12 << 8))
    cmds += id_dylib
    sig_cmd_at = len(cmds)
    if signed:
        cmds += struct.pack(o + "IIII", 0x1D, 16, sig_off, 0)
    magic = 0xFEEDFACF if bits == 64 else 0xFEEDFACE
    header = struct.pack(o + "IIIIIII", magic, cpu, 0, filetype,
                         n_segs + 3 + bool(dylib) + bool(signed),
                         len(cmds), 0x200085)
    if bits == 64:
        header += b"\0\0\0\0"
    assert len(cmds) == sizeofcmds, (len(cmds), sizeofcmds)

    out = bytearray(header + cmds)
    out += bytes(text_off - len(out)) + text_code + cstring
    out += bytes(data_off - len(out)) + data_sect
    if not dwarf_overlaps:
        out += dwarf
    out += bytes(link_off - len(out)) + symtab + strtab_b
    if not signed:
        link_size = len(out) - link_off
        _patch_linkedit(out, o, bits, hdr_len, seg_len, link_size)
        return bytes(out)

    ident = link_name(user, sign_style)
    blobs_for = lambda code: [(0, _cd(code, ident,                       # noqa: E731
                                      0 if identity else 0x20002,
                                      b"SENTINELTM" if identity else b""))]
    probe = _superblob(blobs_for(bytes(sig_off)))
    sig_size = len(probe) + (64 if identity else 0)
    link_size = sig_off + sig_size - link_off
    _patch_linkedit(out, o, bits, hdr_len, seg_len, link_size)
    struct.pack_into(o + "I", out, hdr_len + sig_cmd_at + 12, sig_size)
    blobs = blobs_for(bytes(out[:sig_off]))
    if identity:
        blobs.append((0x10000, struct.pack(">II", 0xFADE0B01, 56) + b"CMS" * 16))
    sig = _superblob(blobs)
    return bytes(out + sig.ljust(sig_size, b"\0"))


def _patch_linkedit(out: bytearray, o: str, bits: int, hdr_len: int, seg_len: int,
                    size: int) -> None:
    """__LINKEDIT is the fifth segment command; its filesize is known last."""
    at = hdr_len + 4 * seg_len + 3 * (80 if bits == 64 else 68)
    field = at + (8 + 16 + 24 if bits == 64 else 8 + 16 + 12)
    struct.pack_into(o + ("Q" if bits == 64 else "I"), out, field, size)


def fat(user: str = "alice", seed: int = 0) -> bytes:
    """A universal file: an unsigned x86_64 slice and a signed arm64 one, the shape
    `clang -arch arm64 -arch x86_64` writes (survey §9)."""
    x86 = build(user, cpu=CPU_X86_64, signed=False, seed=seed)
    arm = build(user, seed=seed)
    align = 1 << 14
    first = align
    second = (first + len(x86) + align - 1) & ~(align - 1)
    head = struct.pack(">II", 0xCAFEBABE, 2)
    head += struct.pack(">IIIII", CPU_X86_64, 3, first, len(x86), 14)
    head += struct.pack(">IIIII", CPU_ARM64, 0, second, len(arm), 14)
    out = bytearray(head.ljust(first, b"\0")) + x86
    return bytes(out.ljust(second, b"\0") + arm)


def java_class() -> bytes:
    """`\\xca\\xfe\\xba\\xbe` too -- the version that follows tells it apart."""
    return b"\xca\xfe\xba\xbe\x00\x00\x00\x41" + b"\x00" * 64


def signature_pages_ok(data: bytes) -> bool:
    """This module's own check of a thin slice's page hashes."""
    ncmds = struct.unpack_from("<I", data, 16)[0]
    pos, sig = 32, None
    for _ in range(ncmds):
        cmd, size = struct.unpack_from("<II", data, pos)
        if cmd == 0x1D:
            sig = struct.unpack_from("<II", data, pos + 8)
        pos += size
    at, _ = sig
    count = struct.unpack_from(">I", data, at + 8)[0]
    for i in range(count):
        _t, off = struct.unpack_from(">II", data, at + 12 + 8 * i)
        cd = at + off
        if struct.unpack_from(">I", data, cd)[0] != 0xFADE0C02:
            continue
        hash_at, _id, _ns, n, limit = struct.unpack_from(">IIIII", data, cd + 16)
        for k in range(n):
            want = hashlib.sha256(data[k * PAGE:min((k + 1) * PAGE, limit)]).digest()
            if data[cd + hash_at + 32 * k:cd + hash_at + 32 * (k + 1)] != want:
                return False
    return True


# --------------------------------------------------------------------------- #
# Real builds: the "runs the same" check needs a program that runs.
# --------------------------------------------------------------------------- #
CAN_RUN = sys.platform == "darwin"


def have(tool: str) -> bool:
    return CAN_RUN and shutil.which(tool) is not None


def compile_c(workdir: str, flags: tuple[str, ...] = ("-g",),
              name: str = "hello") -> str:
    os.makedirs(workdir, exist_ok=True)
    with open(os.path.join(workdir, "hello.c"), "w") as f:
        f.write(ec.HELLO_C)
    subprocess.run(["clang", *flags, "hello.c", "-o", name], cwd=workdir, check=True,
                   capture_output=True)
    return os.path.join(workdir, name)


def run(path: str, *args: str) -> tuple[int, bytes, bytes]:
    r = subprocess.run([path, *args], capture_output=True, timeout=30)
    return r.returncode, r.stdout, r.stderr
