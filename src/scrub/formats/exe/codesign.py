"""Apple code signatures: read them, recompute an ad-hoc one, verify it.

On Apple Silicon every program is signed, and the signature is a list of hashes of
the file's own pages: change one byte and the kernel kills the program at launch
(survey §2, measured: exit 137). So every Mach-O edit ends here. An **ad-hoc**
signature -- the kind the linker writes, or `codesign -s -` -- carries no identity:
it is a CodeDirectory of page hashes, nothing more, and recomputing those hashes is
all re-signing takes. Apple's `codesign -v` accepts the result (measured), so no
Apple tool is needed and Linux can verify what macOS will.

An **identity** signature (a Developer ID or App Store certificate, or any CMS
signature) is refused rather than replaced: editing the file invalidates it, the
recipient's Mac would then block the program, and the certificate is the publisher's
identity by design (`docs/p5_executables_plan.md` §6 D3).

The identifier inside the CodeDirectory is the program's name when it was linked
(`hello-arm64.out` for a universal build, named after the SOURCE file; a
`codesign -s -` signature appends the old UUID), so it is rewritten to `a.out` --
what `ld64` and Go's linker write for an unnamed output, a crowd to join rather than
a mark. Nothing checks it for an ad-hoc signature: the designated requirement is the
code-directory hash (`codesign -d -r-`, measured).
"""
from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass

from ...errors import ParseError

SUPERBLOB = 0xFADE0CC0
CODEDIRECTORY = 0xFADE0C02
BLOBWRAPPER = 0xFADE0B01                  # the CMS signature
CS_ADHOC, CS_LINKER_SIGNED = 0x2, 0x20000
CANONICAL_IDENTIFIER = b"a.out"
_HASHES = {1: (hashlib.sha1, 20), 2: (hashlib.sha256, 32),
           3: (hashlib.sha256, 20), 4: (hashlib.sha384, 48)}


@dataclass(frozen=True)
class CodeDirectory:
    at: int                     # offset of the blob in the slice
    length: int
    version: int
    flags: int
    hash_offset: int
    ident_offset: int
    n_special: int
    n_code: int
    code_limit: int
    hash_size: int
    hash_type: int
    page_size: int              # bytes; 0 = one hash for everything
    team_offset: int
    scatter_offset: int

    def slot(self, i: int) -> int:
        return self.at + self.hash_offset + i * self.hash_size


@dataclass(frozen=True)
class Signature:
    at: int
    size: int
    directories: list[CodeDirectory]
    cms_length: int | None

    @property
    def adhoc(self) -> bool:
        return all(cd.flags & CS_ADHOC for cd in self.directories)

    @property
    def linker_signed(self) -> bool:
        return all(cd.flags & CS_LINKER_SIGNED for cd in self.directories)


def parse(data, at: int, size: int) -> Signature:
    if at + size > len(data) or size < 12:
        raise ParseError("Mach-O: the code signature runs past the end of the file")
    magic, length, count = struct.unpack_from(">III", data, at)
    if magic != SUPERBLOB or length > size or 12 + 8 * count > length:
        raise ParseError("Mach-O: the code signature is not a signature blob")
    dirs, cms = [], None
    for i in range(count):
        _btype, off = struct.unpack_from(">II", data, at + 12 + 8 * i)
        if off + 8 > length:
            raise ParseError("Mach-O: a signature blob lies outside the signature")
        bmagic, blen = struct.unpack_from(">II", data, at + off)
        if off + blen > length:
            raise ParseError("Mach-O: a signature blob runs past the signature")
        if bmagic == BLOBWRAPPER:
            cms = blen
        elif bmagic == CODEDIRECTORY:
            dirs.append(_code_directory(data, at + off, blen))
    if not dirs:
        raise ParseError("Mach-O: the signature has no code directory")
    return Signature(at, size, dirs, cms)


def _code_directory(data, at: int, length: int) -> CodeDirectory:
    if length < 44:
        raise ParseError("Mach-O: code directory truncated")
    (_magic, _len, version, flags, hash_offset, ident_offset, n_special, n_code,
     code_limit, hash_size, hash_type, _platform, page_log2) = struct.unpack_from(
        ">IIIIIIIIIBBBB", data, at)
    scatter = struct.unpack_from(">I", data, at + 44)[0] if version >= 0x20100 else 0
    team = struct.unpack_from(">I", data, at + 48)[0] if version >= 0x20200 else 0
    if version >= 0x20300 and length >= 64:
        limit64 = struct.unpack_from(">Q", data, at + 56)[0]
        code_limit = limit64 or code_limit
    if hash_type not in _HASHES or _HASHES[hash_type][1] != hash_size:
        raise ParseError(f"Mach-O: unknown code-signature hash type {hash_type}")
    if hash_offset + n_code * hash_size > length or hash_offset < n_special * hash_size \
            or not 0 < ident_offset < length:
        raise ParseError("Mach-O: code directory tables do not fit")
    return CodeDirectory(at, length, version, flags, hash_offset, ident_offset,
                         n_special, n_code, code_limit, hash_size, hash_type,
                         (1 << page_log2) if page_log2 else 0, team, scatter)


def _page_hashes(data, cd: CodeDirectory) -> list[bytes]:
    fn, size = _HASHES[cd.hash_type]
    if cd.code_limit > len(data):
        raise ParseError("Mach-O: the signature covers bytes past the end of the file")
    page = cd.page_size or cd.code_limit
    view = memoryview(data)
    out = [fn(view[i * page:min((i + 1) * page, cd.code_limit)]).digest()[:size]
           for i in range(cd.n_code)]
    if page and cd.n_code != -(-cd.code_limit // page):
        raise ParseError("Mach-O: the signature's page count does not match its limit")
    return out


def identifier(data, cd: CodeDirectory) -> bytes:
    start = cd.at + cd.ident_offset
    return bytes(data[start:data.index(b"\0", start, cd.at + cd.length)])


def team(data, cd: CodeDirectory) -> bytes:
    if not cd.team_offset:
        return b""
    start = cd.at + cd.team_offset
    return bytes(data[start:data.index(b"\0", start, cd.at + cd.length)])


def _identifier_room(cd: CodeDirectory) -> int:
    """Bytes from the identifier to whatever the directory stores next."""
    special = cd.hash_offset - cd.n_special * cd.hash_size
    nxt = [o for o in (special, cd.team_offset, cd.scatter_offset, cd.length)
           if o > cd.ident_offset]
    return min(nxt) - cd.ident_offset


def rename(buf: bytearray, sig: Signature) -> None:
    """Every code directory's identifier to `a.out`, when it fits (it fits whenever
    the original was at least as long); a shorter name is left as it is."""
    for cd in sig.directories:
        room = _identifier_room(cd)
        if identifier(buf, cd) != CANONICAL_IDENTIFIER and room > len(
                CANONICAL_IDENTIFIER):
            start = cd.at + cd.ident_offset
            buf[start:start + room] = CANONICAL_IDENTIFIER.ljust(room, b"\0")


def resign(buf: bytearray, sig: Signature) -> None:
    """Recompute every code slot of every code directory over the edited bytes.
    Special slots hash the other blobs (requirements, entitlements, Info.plist),
    none of which an F1 scrub touches, so they stay valid as they are."""
    for cd in sig.directories:
        for i, digest in enumerate(_page_hashes(buf, cd)):
            buf[cd.slot(i):cd.slot(i) + cd.hash_size] = digest


def verify(data, sig: Signature) -> list[str]:
    """Our own check of what the kernel checks at launch: every code slot."""
    out = []
    for cd in sig.directories:
        bad = [i for i, d in enumerate(_page_hashes(data, cd))
               if data[cd.slot(i):cd.slot(i) + cd.hash_size] != d]
        if bad:
            out.append(f"the code signature does not match page {bad[0]} "
                       f"({len(bad)} of {cd.n_code} pages): the program would be "
                       "killed at launch")
    return out
