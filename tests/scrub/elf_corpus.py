"""ELF corpus for CI: executables written byte by byte, plus real builds where a
toolchain exists.

The hand-built files carry every locus the Phase 5 survey measured
(`docs/p5_executables_plan.md` §1) with a distinctive value in each: a `.comment`,
DWARF with a build directory and a compiler command line, `.gnu_debuglink`, a
source-file symbol, a GNU build-id and a Go build ID derived from who built it, and
Go's build info in both copies (`.go.buildinfo` and `.rodata`) with a module path,
commit hash, commit time and pseudo-version. They are never executed -- the code
bytes are filler -- so they run on any machine, in both widths and byte orders.
Whether a scrubbed program still RUNS is the real builds' job (`compile()`), which
needs Linux and a compiler.

Imports nothing from `src` (a test walks the imports), so a shared misreading of
ELF cannot cancel itself out between the corpus and the scrubber.
"""
from __future__ import annotations

import base64
import hashlib
import os
import shutil
import struct
import subprocess
import sys

COMPILER = b"GCC: (SENTINEL-DISTRO 13.3.0-6) 13.3.0"
PRODUCER = b"GNU C17 13.3.0 -g -O0 -fSENTINEL-FLAG"
SOURCE_FILE = b"sentinel_tool.c"
DEBUGLINK = b"sentinel_tool.debug"
GO_START = bytes.fromhex("3077af0c9274080241e1c107e6d618e6")
GO_END = bytes.fromhex("f932433186182072008242104116d8f2")
READ_BUILD_INFO = b"runtime/debug.ReadBuildInfo"
PROGRAM_TEXT = b"hello.c\0hello: %d args, digest %016lx\n\0"
CODE = bytes(range(0x40, 0xC0))                 # stands in for machine code

ET_REL, ET_EXEC, ET_DYN, ET_CORE = 1, 2, 3, 4
SHT_PROGBITS, SHT_SYMTAB, SHT_STRTAB, SHT_NOTE = 1, 2, 3, 7
SHF_WRITE, SHF_ALLOC, SHF_EXEC = 1, 2, 4


def home(user: str) -> bytes:
    return f"/home/{user}/proj".encode()


def module(user: str, seed: int = 0) -> bytes:
    hosts = ("github.com/{}/tool", "gitlab.example.org/{}/x", "codeberg.org/{}/hello-cli")
    return hosts[seed % len(hosts)].format(user).encode()


def code(seed: int = 0) -> bytes:
    """Filler machine code; a seed makes it differ, so a diverse set of inputs
    shares no code page (what the fingerprint guard needs to see past)."""
    return CODE if not seed else bytes((b * (2 * seed + 1) + seed) & 0xFF for b in CODE)


def program_text(seed: int = 0) -> bytes:
    """A seed changes the text and the size, so diverse inputs share neither."""
    return PROGRAM_TEXT if not seed else (PROGRAM_TEXT + f"build {seed}\0".encode()
                                          + b"~" * (97 * seed))


def revision(user: str) -> bytes:
    return hashlib.sha1(b"commit " + user.encode()).hexdigest().encode()


def commit_time(user: str) -> bytes:
    return f"2026-{1 + len(user) % 9:02d}-06T09:15:51Z".encode()


def gnu_build_id(user: str) -> bytes:
    return hashlib.sha1(b"build " + user.encode()).digest()


def go_build_id(user: str) -> bytes:
    parts = [base64.urlsafe_b64encode(hashlib.sha256(f"{user}{i}".encode()).digest())
             [:20] for i in range(4)]
    return b"/".join(parts)                     # 83 characters, as Go writes them


def modinfo(user: str, seed: int = 0) -> bytes:
    pseudo = b"v0.0.0-20261006091551-" + revision(user)[:12]
    settings = (b"build\t-buildmode=exe\n" if not seed else
                f"build\t-buildmode={('exe', 'pie', 'exe')[seed % 3]}\n"
                f"build\tCGO_ENABLED={seed % 2}\n"
                f"build\tGOARCH={('amd64', 'arm64', '386')[seed % 3]}\n".encode())
    text = (b"path\t" + module(user, seed) + b"\nmod\t" + module(user, seed) + b"\t"
            + pseudo + b"\t\n" + settings + b"build\tvcs=git\nbuild\tvcs.revision="
            + revision(user) + b"\nbuild\tvcs.time=" + commit_time(user)
            + b"\nbuild\tvcs.modified=false\n")
    return GO_START + text + GO_END


def _uvarint(n: int) -> bytes:
    out = bytearray()
    while True:
        b, n = n & 0x7F, n >> 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _note(order: str, name: bytes, ntype: int, desc: bytes) -> bytes:
    pad = lambda b: b + bytes(-len(b) % 4)                       # noqa: E731
    return (struct.pack(order + "III", len(name) + 1, len(desc), ntype)
            + pad(name + b"\0") + pad(desc))


def build(user: str = "alice", *, bits: int = 64, order: str = "<",
          etype: int = ET_DYN, go: bool = False, read_build_info: bool = False,
          text_path: bool = False, shared_tail: bool = False,
          unloaded_inside_load: bool = False, section_headers: bool = True,
          seed: int = 0) -> bytes:
    """One executable whose every build-dependent value is derived from `user`.

    `text_path` puts the absolute build path in `.rodata`, as an `assert` does
    when the compiler was given an absolute source path: program text, which F1
    keeps and reports. `shared_tail` gives a function a name that is the tail of
    the source-file symbol's name, as linkers produce by merging string tails.
    """
    rodata = program_text(seed) + (home(user) + b"/hello.c\0" if text_path else b"")
    if go:
        rodata += modinfo(user, seed) + (READ_BUILD_INFO + b"\0"
                                         if read_build_info else b"")
    loaded = []
    if bits == 64:
        # What x86-64 gcc writes by default (CET): an 8-aligned property note, whose
        # descriptor starts at offset 16 -- the layout that a size-padding note
        # walker gets wrong (CI's first x86-64 run, p5 plan §8).
        prop = struct.pack(order + "III", 0xC0000002, 4, 3) + bytes(4)
        loaded.append((".note.gnu.property", SHT_NOTE, SHF_ALLOC,
                       _note(order, b"GNU", 5, prop), 8))
    loaded.append((".note.gnu.build-id", SHT_NOTE, SHF_ALLOC,
                   _note(order, b"GNU", 3, gnu_build_id(user)), 4))
    if go:
        loaded.append((".note.go.buildid", SHT_NOTE, SHF_ALLOC,
                       _note(order, b"Go", 4, go_build_id(user)), 4))
    loaded += [(".text", SHT_PROGBITS, SHF_ALLOC | SHF_EXEC, code(seed), 16),
               (".rodata", SHT_PROGBITS, SHF_ALLOC, rodata, 8)]
    if go:
        info = modinfo(user, seed)
        version = b"go1.22.2"
        header = b"\xff Go buildinf:" + bytes([bits // 8, 2]) + bytes(16)
        loaded.append((".go.buildinfo", SHT_PROGBITS, SHF_ALLOC | SHF_WRITE,
                       header + _uvarint(len(version)) + version
                       + _uvarint(len(info)) + info, 16))

    debug_info = (b"\x00" * 11 + PRODUCER + b"\0" + home(user) + b"\0"
                  + b"hello.c\0" + b"\x00" * 9)
    unloaded = [(".comment", SHT_PROGBITS, 0, COMPILER + b"\0", 1),
                (".debug_info", SHT_PROGBITS, 0, debug_info, 1),
                (".debug_line_str", SHT_PROGBITS, 0, home(user) + b"\0hello.c\0", 1),
                (".gnu_debuglink", SHT_PROGBITS, 0,
                 DEBUGLINK + b"\0" + bytes(-(len(DEBUGLINK) + 1) % 4)
                 + struct.pack(order + "I", 0x5E17E1), 4)]
    return _assemble(bits, order, etype, loaded, unloaded, shared_tail,
                     unloaded_inside_load, section_headers)


def _assemble(bits, order, etype, loaded, unloaded, shared_tail,
              unloaded_inside_load, section_headers) -> bytes:
    eh, ph = (52, 32) if bits == 32 else (64, 56)
    sh = 40 if bits == 32 else 64
    sections = []                               # name, type, flags, off, data, align
    out = bytearray(eh + 2 * ph)

    def place(name, typ, flags, data, align, link=0, info=0, entsize=0):
        out.extend(bytes(-len(out) % align))
        sections.append((name, typ, flags, len(out), data, align, link, info, entsize))
        out.extend(data)

    for s in loaded:
        place(*s)
    loaded_end = len(out)
    note_ranges = [(o, len(d)) for n, t, f, o, d, *_ in sections if t == SHT_NOTE]
    for s in unloaded:
        place(*s)

    # The symbol table: a source-file symbol, `main`, and (optionally) a function
    # whose name shares the source-file name's tail.
    strtab = bytearray(b"\0")
    file_at = len(strtab)
    strtab += SOURCE_FILE + b"\0"
    main_at = len(strtab)
    strtab += b"main\0"
    syms = [(0, 0, 0), (file_at, 0x04, 0xFFF1), (main_at, 0x12, 2)]
    if shared_tail:
        syms.append((file_at + len(SOURCE_FILE) - len(b"tool.c"), 0x12, 2))
    sym_fmt = order + ("IIIBBH" if bits == 32 else "IBBHQQ")
    symtab = b"".join(
        struct.pack(sym_fmt, name, 0, 0, info, 0, shndx) if bits == 32
        else struct.pack(sym_fmt, name, info, 0, shndx, 0, 0)
        for name, info, shndx in syms)
    n_sections = 1 + len(sections) + 3
    place(".symtab", SHT_SYMTAB, 0, symtab, 8, link=n_sections - 2, info=2,
          entsize=struct.calcsize(sym_fmt))
    place(".strtab", SHT_STRTAB, 0, bytes(strtab), 1)
    names = bytearray(b"\0")
    name_at = {}
    for s in [x[0] for x in sections] + [".shstrtab"]:
        name_at[s] = len(names)
        names += s.encode() + b"\0"
    place(".shstrtab", SHT_STRTAB, 0, bytes(names), 1)

    out.extend(bytes(-len(out) % 8))
    shoff = len(out) if section_headers else 0
    sh_fmt = order + ("IIIIIIIIII" if bits == 32 else "IIQQQQIIQQ")
    if section_headers:
        out += bytes(sh)
        for name, typ, flags, off, data, align, link, info, entsize in sections:
            addr = off if flags & SHF_ALLOC else 0
            out += struct.pack(sh_fmt, name_at[name], typ, flags, addr, off, len(data),
                               link, info, align, entsize)

    load_end = len(out) if unloaded_inside_load else loaded_end
    ph_fmt = order + ("IIIIIIII" if bits == 32 else "IIQQQQQQ")
    note_off = note_ranges[0][0]
    note_len = note_ranges[-1][0] + note_ranges[-1][1] - note_off
    phdrs = [(1, 5, 0, load_end, 0x1000), (4, 4, note_off, note_len, 4)]
    at = eh
    for ptype, pflags, off, size, align in phdrs:
        row = ((ptype, off, off, off, size, size, pflags, align) if bits == 32
               else (ptype, pflags, off, off, off, size, size, align))
        struct.pack_into(ph_fmt, out, at, *row)
        at += ph
    machine = 20 if bits == 32 else 183                  # PowerPC / AArch64
    struct.pack_into("4sBBBB8s", out, 0, b"\x7fELF", 1 if bits == 32 else 2,
                     1 if order == "<" else 2, 1, 0, bytes(8))
    hdr = order + ("HHIIIIIHHHHHH" if bits == 32 else "HHIQQQIHHHHHH")
    struct.pack_into(hdr, out, 16, etype, machine, 1, 0, eh, shoff, 0, eh, ph, 2,
                     sh, n_sections if section_headers else 0,
                     n_sections - 1 if section_headers else 0)
    return bytes(out)


# --------------------------------------------------------------------------- #
# Real builds: the "runs the same" check needs a program that runs.
# --------------------------------------------------------------------------- #
CAN_RUN = sys.platform.startswith("linux")
HELLO_C = r"""
#include <stdio.h>
static unsigned long long mix(const char *s) {
    unsigned long long h = 1469598103934665603ULL;
    for (; *s; s++) h = (h ^ (unsigned char)*s) * 1099511628211ULL;
    return h;
}
int main(int argc, char **argv) {
    unsigned long long acc = 0;
    for (int i = 1; i < argc; i++) acc ^= mix(argv[i]);
    printf("hello: %d args, digest %016llx\n", argc - 1, acc);
    return (int)(acc % 7);
}
"""


def have(tool: str) -> bool:
    return CAN_RUN and shutil.which(tool) is not None


def compile_c(workdir: str, compiler: str = "gcc", flags: tuple[str, ...] = ("-g",),
              name: str = "hello") -> str:
    """Build `HELLO_C` inside `workdir` (its path lands in the debug info)."""
    os.makedirs(workdir, exist_ok=True)
    src = os.path.join(workdir, "hello.c")
    with open(src, "w") as f:
        f.write(HELLO_C)
    out = os.path.join(workdir, name)
    subprocess.run([compiler, *flags, "hello.c", "-o", name], cwd=workdir, check=True,
                   capture_output=True)
    return out


def run(path: str, *args: str) -> tuple[int, bytes, bytes]:
    r = subprocess.run([path, *args], capture_output=True, timeout=30)
    return r.returncode, r.stdout, r.stderr
