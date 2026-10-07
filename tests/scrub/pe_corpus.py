"""PE corpus for CI: Windows programs written byte by byte, plus real builds where
a cross compiler and Wine exist.

Two shapes, because two toolchain families write different things
(`docs/p5_executables_plan.md` §1, §10):

- **msvc**: a Rich header (with a real key), a CodeView entry naming the PDB under
  the builder's `C:\\Users\\<name>`, a REPRO hash, plus a POGO entry and the CET
  flag entry that must survive (the loader reads the latter);
- **mingw**: a COFF symbol table with `.file` records, DWARF sections behind long
  `/N` names, and a checksum.

Both carry a link time and a version resource whose company, copyright and original
file name derive from who built it. Options add Go's build ID and build info, a
DLL's export directory, an import of `version.dll`, and the refusal cases. The
checksum and Rich key are computed here, independently of the scrubber.

The code bytes are filler, so these never run; whether a cleaned program still runs
is the real builds' job (`compile_c`, under Wine).

Imports nothing from `src` (a test walks the imports).
"""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys

from . import elf_corpus as ec

TEXT, RDATA, DATA, RSRC, RELOC, DBG_INFO, DBG_LINE = (0x1000 * i for i in range(1, 8))
FILE_ALIGN = 0x200
CODE = bytes(range(0x30, 0xB0))
PROGRAM_TEXT = b"hello: %d args\n\0"
SOURCE_FILE = b"sentinel_tool.c"
PRODUCT, DESCRIPTION, FILE_VERSION = "hello", "hello survey", "1.2.3.4"
RICH_ENTRIES = [((0x0105 << 16) | 30034, 35), ((0x0104 << 16) | 30034, 17),
                ((0x0001 << 16) | 0, 108)]


def link_time(user: str) -> int:
    return 1_791_279_045 + sum(user.encode())


def pdb_path(user: str) -> bytes:
    return f"C:\\Users\\{user}\\proj\\x64\\Release\\sentinel.pdb".encode()


def pdb_guid(user: str) -> bytes:
    import hashlib
    return hashlib.md5(b"guid " + user.encode()).digest()


def repro_hash(user: str) -> bytes:
    import hashlib
    return hashlib.sha256(b"repro " + user.encode()).digest()


def company(user: str) -> str:
    return f"SENTINEL-COMPANY-{user}"


def copyright_(user: str) -> str:
    return f"(c) SENTINEL {user}"


def original_name(user: str) -> str:
    return f"{user}_tool.exe"


def home_unix(user: str) -> bytes:
    return f"/home/{user}/proj".encode()


def utf16(s: str) -> bytes:
    return s.encode("utf-16-le")


def _align(n: int, a: int) -> int:
    return (n + a - 1) & ~(a - 1)


def _rol(v: int, n: int) -> int:
    n &= 31
    return ((v << n) | (v >> (32 - n))) & 0xFFFFFFFF


def rich(dos: bytes, entries=RICH_ENTRIES) -> bytes:
    """The Rich header for this DOS header and stub, key and all, as link.exe
    computes it: start offset + every DOS byte (e_lfanew skipped) rotated by its
    position + every entry rotated by its count."""
    key = 0x80
    for i, b in enumerate(dos[:0x80]):
        if not 0x3C <= i < 0x40:
            key = (key + _rol(b, i)) & 0xFFFFFFFF
    for comp, count in entries:
        key = (key + _rol(comp, count)) & 0xFFFFFFFF
    words = [0x536E6144 ^ key, key, key, key]
    for comp, count in entries:
        words += [comp ^ key, count ^ key]
    return struct.pack(f"<{len(words)}I", *words) + b"Rich" + struct.pack("<I", key)


def _vs_node(key: str, value: bytes = b"", vtype: int = 0, children: bytes = b"",
             vlen: int | None = None) -> bytes:
    body = utf16(key) + b"\0\0"
    body += bytes((-(6 + len(body))) % 4) + value
    if children:
        body += bytes((-(6 + len(body))) % 4) + children
    node = struct.pack("<HHH", 6 + len(body), len(value) if vlen is None else vlen,
                       vtype) + body
    return node + bytes(-len(node) % 4)


def version_info(user: str) -> bytes:
    strings = [("CompanyName", company(user)), ("FileDescription", DESCRIPTION),
               ("FileVersion", FILE_VERSION), ("LegalCopyright", copyright_(user)),
               ("OriginalFilename", original_name(user)), ("ProductName", PRODUCT)]
    table = b"".join(_vs_node(k, utf16(v) + b"\0\0", 1, vlen=len(v) + 1)
                     for k, v in strings)
    sfi = _vs_node("StringFileInfo", vtype=1,
                   children=_vs_node("040904b0", vtype=1, children=table))
    var = _vs_node("VarFileInfo", vtype=1,
                   children=_vs_node("Translation", struct.pack("<HH", 0x409, 1200)))
    fixed = struct.pack("<13I", 0xFEEF04BD, 0x10000, 0x10002, 0x30004, 0x10002,
                        0x30004, 0x3F, 0, 0x40004, 1, 0, 0x01D7, link_time(user))
    return _vs_node("VS_VERSION_INFO", fixed, 0, sfi + var)


class _Section:
    def __init__(self, name: bytes, va: int, flags: int):
        self.name, self.va, self.flags, self.data = name, va, flags, bytearray()

    def put(self, blob: bytes, align: int = 4) -> int:
        self.data += bytes(-len(self.data) % align)
        rva = self.va + len(self.data)
        self.data += blob
        return rva


def build(user: str = "alice", *, shape: str = "msvc", plus: bool = True,
          go: bool = False, dll: bool = False, reads_version: bool = False,
          signed: bool = False, dotnet: bool = False, reloc_into_debug: bool = False,
          text_path: bool = False, seed: int = 0) -> bytes:
    msvc = shape == "msvc"
    text = _Section(b".text", TEXT, 0x60000020)
    rdata = _Section(b".rdata", RDATA, 0x40000040)
    data = _Section(b".data", DATA, 0xC0000040)
    rsrc = _Section(b".rsrc", RSRC, 0x40000040)
    reloc = _Section(b".reloc", RELOC, 0x42000040)
    sections = [text, rdata, data, rsrc, reloc]

    if go:
        text.put(b'\xff Go build ID: "' + ec.go_build_id(user) + b'"\n \xff')
    text.put(CODE if not seed else ec.code(seed))
    rdata.put((PROGRAM_TEXT if not seed else ec.program_text(seed))
              + (home_unix(user) + b"/hello.c\0" if text_path else b""))
    if go:
        info = ec.modinfo(user, seed)
        rdata.put(info)
        data.put(b"\xff Go buildinf:" + bytes([8, 2]) + bytes(16)
                 + ec._uvarint(8) + b"go1.22.2" + ec._uvarint(len(info)) + info, 16)
    else:
        data.put(b"\x01\x02\x03\x04")

    # Debug directory: what MSVC writes, plus the CET entry the loader reads.
    debug_blobs = []
    if msvc:
        rsds = b"RSDS" + pdb_guid(user) + struct.pack("<I", 1) + pdb_path(user) + b"\0"
        debug_blobs = [(2, rdata.put(rsds), len(rsds)),
                       (13, rdata.put(b"PGU\0" + b".text$mn\0\0\0\0" + bytes(4)), 20),
                       (16, rdata.put(struct.pack("<I", 32) + repro_hash(user)), 36),
                       (20, rdata.put(struct.pack("<I", 1)), 4)]
    debug_dir_rva = rdata.put(bytes(28 * len(debug_blobs))) if debug_blobs else 0

    imports_rva = imports_size = 0
    if reads_version:
        name = rdata.put(b"VERSION.dll\0")
        hint = rdata.put(b"\0\0VerQueryValueW\0", 2)
        thunks = struct.pack("<QQ" if plus else "<II", hint, 0)
        ilt, iat = rdata.put(thunks, 8), rdata.put(thunks, 8)
        imports_rva = rdata.put(struct.pack("<IIIII", ilt, 0, 0, name, iat) + bytes(20))
        imports_size = 40
    export_rva = 0
    if dll:
        name = rdata.put(b"sentinel.dll\0")
        export_rva = rdata.put(struct.pack("<IIHHIIIIIII", 0, link_time(user), 0, 0,
                                           name, 1, 0, 0, 0, 0, 0))
    clr_rva = rdata.put(struct.pack("<II", 72, 0x50002) + bytes(64)) if dotnet else 0

    # Resources: one version-info resource, type 16 / id 1 / English.
    vinfo = version_info(user)
    root = struct.pack("<IIHHHH", 0, link_time(user), 0, 0, 0, 1)
    tree = bytearray(root + struct.pack("<II", 16, 0x80000000 | 0x18))
    tree += struct.pack("<IIHHHH", 0, link_time(user), 0, 0, 0, 1) \
        + struct.pack("<II", 1, 0x80000000 | 0x30)
    tree += struct.pack("<IIHHHH", 0, link_time(user), 0, 0, 0, 1) \
        + struct.pack("<II", 0x409, 0x48)
    leaf_at = len(tree)
    tree += bytes(16)
    data_rva = RSRC + _align(len(tree), 8)
    struct.pack_into("<IIII", tree, leaf_at, data_rva, len(vinfo), 0, 0)
    rsrc.put(bytes(tree))
    rsrc.put(vinfo, 8)

    page = DBG_INFO if reloc_into_debug else TEXT
    reloc.put(struct.pack("<IIHH", page, 12, (10 if plus else 3) << 12, 0))

    debug_info = (b"\0" * 11 + b"GNU C17 13-win32 -g -fSENTINEL-FLAG\0"
                  + home_unix(user) + b"\0hello.c\0")
    dbg = [_Section(b"/4", DBG_INFO, 0x42000040), _Section(b"/16", DBG_LINE, 0x42000040)]
    dbg[0].put(debug_info)
    dbg[1].put(home_unix(user) + b"\0hello.c\0")
    if not msvc:
        sections += dbg

    # Headers.
    dos = bytearray(b"MZ" + bytes(0x3A) + bytes(4))
    dos += b"\x0e\x1f\xba\x0e\x00\xb4\x09\xcd\x21\xb8\x01\x4c\xcd\x21" \
        + b"This program cannot be run in DOS mode.\r\r\n$" + bytes(5)
    dos = dos.ljust(0x80, b"\0")
    stub = bytes(dos) + (rich(bytes(dos)) if msvc else b"")
    lfanew = _align(len(stub), 8)
    struct.pack_into("<I", dos, 0x3C, lfanew)
    stub = bytes(dos) + (rich(bytes(dos)) if msvc else b"")
    opt_size = 240 if plus else 224
    headers_len = lfanew + 4 + 20 + opt_size + 40 * len(sections)
    headers_size = _align(headers_len, FILE_ALIGN)

    raw = headers_size
    for s in sections:
        s.raw, s.rawsize = raw, _align(len(s.data), FILE_ALIGN)
        raw += s.rawsize
    image_end = raw

    # Debug entries need file offsets, known only now.
    if debug_blobs:
        table = b"".join(struct.pack("<IIHHIIII", 0, link_time(user), 0, 0, typ, size,
                                     rva, rdata.raw + rva - RDATA)
                         for typ, rva, size in debug_blobs)
        off = debug_dir_rva - RDATA
        rdata.data[off:off + len(table)] = table

    symbols, strings = b"", b""
    if not msvc:
        strtab = bytearray(b"\0\0\0\0")
        def name_at(text: bytes) -> int:
            at = len(strtab)
            strtab.extend(text + b"\0")
            return at
        long_names = {b"/4": name_at(b".debug_info"), b"/16": name_at(b".debug_line_str")}
        dbg[0].name, dbg[1].name = (f"/{long_names[b'/4']}".encode(),
                                    f"/{long_names[b'/16']}".encode())
        func = name_at(b"sentinel_long_function_name")
        syms = [struct.pack("<8sIhHBB", b".file", 0, -2, 0, 103, 1),
                SOURCE_FILE.ljust(18, b"\0"),
                struct.pack("<8sIhHBB", b"main", 0x10, 1, 0x20, 2, 0),
                struct.pack("<IIIhHBB", 0, func, 0x20, 1, 0x20, 2, 0)]
        symbols = b"".join(syms)
        struct.pack_into("<I", strtab, 0, len(strtab))
        strings = bytes(strtab)
    symptr = image_end if symbols else 0
    cert_at = image_end + len(symbols) + len(strings)
    cert = b""
    if signed:
        body = b"SENTINEL-PUBLISHER-CERTIFICATE"
        cert = struct.pack("<IHH", 8 + len(body), 0x200, 2) + body
        cert += bytes(-len(cert) % 8)

    coff = struct.pack("<HHIIIHH", 0x8664 if plus else 0x14C, len(sections),
                       link_time(user), symptr, len(symbols) // 18, opt_size,
                       0x2022 if dll else (0x22 if plus else 0x102))
    dirs = [(0, 0)] * 16
    dirs[0] = (export_rva, 40) if dll else (0, 0)
    dirs[1] = (imports_rva, imports_size)
    dirs[2] = (RSRC, len(rsrc.data))
    dirs[4] = (cert_at, len(cert)) if signed else (0, 0)
    dirs[5] = (RELOC, 12)
    dirs[6] = (debug_dir_rva, 28 * len(debug_blobs)) if debug_blobs else (0, 0)
    dirs[14] = (clr_rva, 72) if dotnet else (0, 0)
    size_of_image = _align(max(s.va + len(s.data) for s in sections), 0x1000)
    if plus:
        opt = struct.pack("<HBBIIIIIQIIHHHHHHIIIIHHQQQQII", 0x20B,
                          14 if msvc else 2, 36 if msvc else 41, 0x200, 0x400, 0,
                          TEXT, TEXT, 0x140000000, 0x1000, FILE_ALIGN, 6, 0, 0, 0, 6,
                          0, 0, size_of_image, headers_size, 0, 3, 0x8160,
                          0x100000, 0x1000, 0x100000, 0x1000, 0, 16)
    else:
        opt = struct.pack("<HBBIIIIIIIIIHHHHHHIIIIHHIIIIII", 0x10B,
                          14 if msvc else 2, 36 if msvc else 41, 0x200, 0x400, 0,
                          TEXT, TEXT, RDATA, 0x400000, 0x1000, FILE_ALIGN, 6, 0, 0,
                          0, 6, 0, 0, size_of_image, headers_size, 0, 3, 0x8140,
                          0x100000, 0x1000, 0x100000, 0x1000, 0, 16)
    opt += b"".join(struct.pack("<II", *d) for d in dirs)
    table = b"".join(struct.pack("<8sIIIIIIHHI", s.name.ljust(8, b"\0"), len(s.data),
                                 s.va, s.rawsize, s.raw, 0, 0, 0, 0, s.flags)
                     for s in sections)
    head = stub.ljust(lfanew, b"\0") + b"PE\0\0" + coff + opt + table
    out = bytearray(head.ljust(headers_size, b"\0"))
    for s in sections:
        out += bytes(s.data).ljust(s.rawsize, b"\0")
    out += symbols + strings + cert
    if not msvc:
        at = lfanew + 4 + 20 + 64
        struct.pack_into("<I", out, at, checksum(bytes(out), at))
    return bytes(out)


def checksum(data: bytes, at: int) -> int:
    """This module's own PE checksum, word by word."""
    total = 0
    padded = data + (b"\0" if len(data) % 2 else b"")
    for i in range(0, len(padded), 2):
        if at <= i < at + 4:
            continue
        total += padded[i] | (padded[i + 1] << 8)
        total = (total & 0xFFFF) + (total >> 16)
    total = (total & 0xFFFF) + (total >> 16)
    return (total + len(data)) & 0xFFFFFFFF


# --------------------------------------------------------------------------- #
# Real builds: the "runs the same" check needs a program that runs.
# --------------------------------------------------------------------------- #
MINGW = "x86_64-w64-mingw32-gcc"


def wine() -> str | None:
    if not sys.platform.startswith("linux"):
        return None
    for w in ("/usr/lib/wine/wine64", "wine64", "wine"):
        found = shutil.which(w) or (w if os.path.exists(w) else None)
        if found:
            return found
    return None


def have_mingw() -> bool:
    return shutil.which(MINGW) is not None


def compile_c(workdir: str, flags: tuple[str, ...] = ("-g",),
              rc: bool = False) -> str:
    os.makedirs(workdir, exist_ok=True)
    with open(os.path.join(workdir, "hello.c"), "w") as f:
        f.write(ec.HELLO_C)
    extra = []
    if rc:
        with open(os.path.join(workdir, "v.rc"), "w") as f:
            f.write('1 VERSIONINFO\nFILEVERSION 1,2,3,4\nBEGIN\n BLOCK "StringFileInfo"\n'
                    ' BEGIN\n  BLOCK "040904b0"\n  BEGIN\n'
                    '   VALUE "CompanyName", "SENTINEL-COMPANY"\n'
                    '   VALUE "LegalCopyright", "SENTINEL-AUTHOR"\n'
                    '   VALUE "ProductName", "hello"\n  END\n END\nEND\n')
        subprocess.run(["x86_64-w64-mingw32-windres", "v.rc", "-O", "coff", "-o", "v.o"],
                       cwd=workdir, check=True, capture_output=True)
        extra = ["v.o"]
    subprocess.run([MINGW, *flags, "hello.c", *extra, "-o", "hello.exe"], cwd=workdir,
                   check=True, capture_output=True)
    return os.path.join(workdir, "hello.exe")


def run(path: str, *args: str) -> tuple[int, bytes]:
    env = dict(os.environ, WINEDEBUG="-all",
               WINEPREFIX=os.environ.get("WINEPREFIX", "/tmp/scrub-wineprefix"))
    r = subprocess.run([wine(), path, *args], capture_output=True, timeout=180,
                       env=env)
    return r.returncode, r.stdout.replace(b"\r\n", b"\n")
