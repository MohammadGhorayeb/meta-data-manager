"""What a Go build writes into every program, whatever the container.

Go keeps its build information twice -- once in `.go.buildinfo` / `__go_buildinfo`
for `go version -m`, once in read-only data for `runtime/debug.ReadBuildInfo` -- and
inside a git checkout that information carries the commit hash and time (Go >= 1.24
also writes them into the main module's pseudo-version), plus the module path,
`github.com/<name>/...` more often than not (survey §1, §4). The same bytes appear in
ELF, Mach-O and PE, so the rule lives here once: blank them in every copy, but only
when the program does not link `ReadBuildInfo`, the one way it can read them. The
linker drops that function when nothing calls it, so its name in the binary is the
test.

The Go build ID (`actionID/contentID`, 83 characters) is recomputed from the cleaned
file by the container's handler; `build_id()` keeps its shape.
"""
from __future__ import annotations

import hashlib
import re
import string

READ_BUILD_INFO = b"runtime/debug.ReadBuildInfo"
_VCS_LINE = re.compile(rb"build\tvcs\.(?:revision|time)=([^\n]*)\n")
# The main module's version, when Go derived it from the checkout: a pseudo-version
# `vX.Y.Z-[pre.]yyyymmddhhmmss-<12 hex>` carries the commit time and hash too.
_MOD_LINE = re.compile(rb"\nmod\t[^\t\n]*\t(v[^\t\n]*)")
_PSEUDO = re.compile(rb"-(?:[0-9A-Za-z.]*\.)?(\d{14})-([0-9a-f]{12})")
# `path` opens the build info, straight after Go's 16-byte start marker (which
# ends `\xd6\x18\xe6`); `mod` follows it on the next line.
_MODULE_PATH = re.compile(rb"(?:\n|\xd6\x18\xe6)(?:path|mod)\t([^\t\n]+)")
# Outside ELF (which has a note for it) Go writes its build ID at the start of the
# text: `\xff Go build ID: "<id>"\n \xff`.
TEXT_BUILD_ID = re.compile(rb'\xff Go build ID: "([A-Za-z0-9_\-/]{20,200})"\n \xff')
_ID_ALPHABET = (string.ascii_letters + string.digits + "-_").encode()


def can_read_build_info(data) -> bool:
    return READ_BUILD_INFO in data


def stamps(data) -> list[tuple[int, int]]:
    """vcs.revision / vcs.time values and a pseudo-version's time and hash."""
    out = [m.span(1) for m in _VCS_LINE.finditer(data)]
    for m in _MOD_LINE.finditer(data):
        p = _PSEUDO.search(data, m.start(1), m.end(1))
        if p:
            out += [p.span(1), p.span(2)]
    return out


def module_paths(data) -> list[tuple[int, int]]:
    """The main module's path in the build info's `path` and `mod` lines."""
    return [m.span(1) for m in _MODULE_PATH.finditer(data)]


def text_build_ids(data) -> list[tuple[int, int]]:
    return [m.span(1) for m in TEXT_BUILD_ID.finditer(data)]


def blank(buf: bytearray, data) -> list[tuple[int, int]]:
    """Blank the stamp and the module path in place; returns the ranges edited
    (empty when the program can read them, and so they stay)."""
    if can_read_build_info(data):
        return []
    edited = []
    for start, end in stamps(data):
        # Hex and digits to `0`, separators kept: `2026-10-06T09:15:51Z` reads
        # `0000-00-00T00:00:00Z`, still the shape a parser expects.
        buf[start:end] = bytes(0x30 if chr(c) in string.hexdigits else c
                               for c in buf[start:end])
        edited.append((start, end))
    for start, end in module_paths(data):
        # Letters and digits to `0`; `.`, `/`, `-` and `_` kept, so the line still
        # parses as a module path.
        buf[start:end] = bytes(0x30 if chr(c).isalnum() else c for c in buf[start:end])
        edited.append((start, end))
    return edited


def build_id(old: bytes, seed: bytes) -> bytes:
    """A Go build ID of the same shape: the `/` separators where they were, the
    other characters drawn from Go's alphabet by `seed`."""
    stream, counter = b"", 0
    while len(stream) < len(old):
        stream += hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
        counter += 1
    return bytes(c if c == 0x2F else _ID_ALPHABET[stream[i] % 64]
                 for i, c in enumerate(old))


def residuals(data) -> list[str]:
    if can_read_build_info(data):
        return []
    out = []
    if any(any(c not in b"0-:TZ" for c in data[a:b]) for a, b in stamps(data)):
        out.append("Go's commit stamp is still in the build info")
    if any(chr(c).isalnum() and c != 0x30 for a, b in module_paths(data)
           for c in data[a:b]):
        out.append("the Go module path is still in the build info")
    return out


def advise(data) -> list[str]:
    out = []
    if can_read_build_info(data) and (stamps(data) or module_paths(data)):
        out.append("this Go program can read its own build information "
                   "(runtime/debug.ReadBuildInfo), so its module path and any commit "
                   "hash and time in it are kept; -buildvcs=false leaves the commit "
                   "out at build time")
    paths = {bytes(data[a:b]) for a, b in module_paths(data)}
    elsewhere = [p for p in paths if p != b"command-line-arguments"
                 and data.count(p) > sum(1 for a, b in module_paths(data)
                                         if data[a:b] == p)]
    if elsewhere:
        out.append(f"the Go module path {elsewhere[0].decode('latin-1')!r} is also "
                   "in the program's function names, which stack traces print, so "
                   "those copies are kept (limit #50)")
    return out


def describe(data) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in _VCS_LINE.finditer(data):
        key = m.group(0).split(b"=")[0].split(b"\t")[1].decode()
        out.setdefault(f"Go {key}", m.group(1).decode("latin-1"))
    for a, b in module_paths(data)[:1]:
        out["Go module path"] = bytes(data[a:b]).decode("latin-1")
    return out
