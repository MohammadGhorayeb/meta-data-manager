"""Deterministic ZIP writer for OOXML packages.

Every constant here was **measured, not chosen** (docs/p3_documents_plan.md §2.7).
The rule the project keeps arriving at is that a mandatory field should join the
largest crowd that actually exists, because a value nobody else writes is a
signature — the same reasoning that put PDF's deflate at level 6 after level 9
labelled our output, and that took FLAC's padding to zero rather than normalising it.

We write the container rather than driving `zipfile` for one reason, and it is not
style: CPython does

    if not zinfo.external_attr:
        zinfo.external_attr = 0o600 << 16

so `0` — the value Word, LibreOffice and macOS `textutil` all write — is the one
value the stdlib refuses to keep, and a package written through it carries **the
operator's umask in the central directory of every entry**.

What is pinned, and to whom:

| field | value | who else writes it |
|---|---|---|
| `create system` | 0 (FAT) | Word, LibreOffice, textutil |
| `version made by` / `needed` | 20 (2.0) | everyone except Word (45) |
| GP flags | 0 | Word, textutil, MAT2 |
| MS-DOS date/time | 1980-01-01 00:00 | **Word itself**, and MAT2 |
| method / level | deflate, zlib level 6 | LibreOffice, textutil, MAT2 |
| `external attr` | 0 | Word, LibreOffice, textutil |
| extra fields, comments, directory entries | none | — |

**Entry order** is `[Content_Types].xml` first, then every other part sorted. First
because ECMA-376 Part 2 asks for it (a streaming consumer needs the types before the
parts), which is also Word's convention; sorted after that because the alternative is
an order derived from a dict or a set, and that is precisely the class of
non-determinism the harness floor cannot see.

**Portability caveat, stated rather than discovered later:** the compressed bytes are
whatever this machine's zlib produces at level 6. That is deterministic per build and
across processes, which is what the determinism tests check, but two different zlib
builds can differ. Verdicts are compared, never bytes across machines — the same
caveat `pdftoppm` carries for PDF F3.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

from ...errors import ParseError

CONTENT_TYPES = "[Content_Types].xml"

CREATE_SYSTEM = 0        # FAT, as every non-Python producer writes
VERSION = 20             # 2.0
FLAGS = 0                # no data descriptor, no UTF-8 bit (part names are ASCII)
METHOD_DEFLATE = 8
LEVEL = 6                # measured as the crowd, twice, by two techniques
EXTERNAL_ATTR = 0
INTERNAL_ATTR = 0

# 1980-01-01 00:00:00 in MS-DOS form: year offset 0, month 1, day 1.
DOS_DATE = (0 << 9) | (1 << 5) | 1
DOS_TIME = 0

_LOC_SIG = b"PK\x03\x04"
_CEN_SIG = b"PK\x01\x02"
_EOCD_SIG = b"PK\x05\x06"


def order_parts(names) -> list[str]:
    """The canonical entry order: content types first, everything else sorted."""
    rest = sorted(n for n in names if n != CONTENT_TYPES)
    return ([CONTENT_TYPES] if CONTENT_TYPES in set(names) else []) + rest


def _deflate(body: bytes) -> bytes:
    co = zlib.compressobj(LEVEL, zlib.DEFLATED, -15)
    return co.compress(body) + co.flush()


def write(parts: dict[str, bytes]) -> bytes:
    """Serialise parts into a package. Deterministic for a given input and zlib.

    `parts` maps part name to its bytes. Names must be ASCII with no directory
    entries — the two things that would force a flag bit or an entry we do not write.
    """
    entries = []
    for name in order_parts(parts):
        try:
            raw_name = name.encode("ascii")
        except UnicodeEncodeError as exc:
            # A non-ASCII part name needs GP bit 11, which would make our flag word
            # non-constant. OPC part names are ASCII by spec, so this is a refusal
            # rather than a feature.
            raise ParseError(f"non-ASCII part name {name!r}") from exc
        if name.endswith("/"):
            raise ParseError(f"directory entry {name!r}: we do not write them")
        body = parts[name]
        entries.append((raw_name, FLAGS, METHOD_DEFLATE, body, _deflate(body),
                        CREATE_SYSTEM, EXTERNAL_ATTR))
    return _serialise(entries)


# --- plain archives (Phase 6: ZIP, EPUB) ---------------------------------------
#
# The same writer with the three things a package never needs and an archive does:
# directory entries (an empty folder is content), a non-ASCII name (bit 11, set only
# for such a name, as the spec and Python's writer do -- Info-ZIP, `ditto` and
# libarchive write UTF-8 without it, which a reader without a UTF-8 guess shows as
# mojibake), and the executable bit (a script that stops running is a content
# change). An archive is written as Unix (`create system` 3, the crowd on every
# producer measured: Info-ZIP, `ditto`, libarchive, Python, MAT2), so modes mean
# something, and every mode is one of four canonical values -- what was the
# operator's umask (0600, 0664...) is gone. Stored when deflate does not shrink a
# member (an empty file, a JPEG), deflated otherwise: decided by the content, as
# Info-ZIP does, never by the producer.

UNIX = 3
FLAG_UTF8 = 0x0800
METHOD_STORED = 0
MODE_FILE = 0o100644
MODE_EXEC = 0o100755
MODE_DIR = 0o040755
MODE_LINK = 0o120777
DOS_DIRECTORY = 0x10


@dataclass(frozen=True)
class Member:
    """One archive member as written. `name` is the stored bytes; `utf8` sets bit
    11 (only for a non-ASCII name known to be UTF-8 -- a legacy code-page name is
    written back as it was, without it). `mode` is one of the MODE_ constants."""
    name: bytes
    body: bytes
    mode: int = MODE_FILE
    utf8: bool = False


def write_archive(members: list[Member]) -> bytes:
    """Serialise members in the order given (the caller decides it: sorted for a
    plain archive, `mimetype` first for EPUB). Deterministic for a given input and
    zlib."""
    entries = []
    for m in members:
        if m.mode not in (MODE_FILE, MODE_EXEC, MODE_DIR, MODE_LINK):
            raise ParseError(f"{m.name!r}: mode {m.mode:o} is not a canonical mode")
        if (m.mode == MODE_DIR) != m.name.endswith(b"/"):
            raise ParseError(f"{m.name!r}: a directory entry is a name ending '/'")
        if m.mode == MODE_DIR and m.body:
            raise ParseError(f"{m.name!r}: a directory entry has no content")
        attr = (m.mode << 16) | (DOS_DIRECTORY if m.mode == MODE_DIR else 0)
        packed = _deflate(m.body)
        method, data = ((METHOD_DEFLATE, packed) if len(packed) < len(m.body)
                        else (METHOD_STORED, m.body))
        entries.append((m.name, FLAG_UTF8 if m.utf8 else 0, method, m.body, data,
                        UNIX, attr))
    return _serialise(entries)


def _serialise(entries) -> bytes:
    """(name bytes, flags, method, body, stored data, create system, external attr)
    per entry, in order, into an archive."""
    out = bytearray()
    central = bytearray()
    count = 0

    for raw_name, flags, method, body, data, create_system, external_attr in entries:
        crc = zlib.crc32(body)
        offset = len(out)

        # The two copies of the header are built from ONE tuple of values, because
        # the classic subtly-corrupt rewrite is the local header and the central
        # directory drifting apart. Note the DOS pair is time-then-date; writing it
        # the other way round is silent and produces a file dated 1980-01-00.
        shared = struct.pack("<HHHH", flags, method, DOS_TIME, DOS_DATE)
        sizes = struct.pack("<III", crc, len(data), len(body))

        out += (_LOC_SIG + struct.pack("<H", VERSION) + shared + sizes
                + struct.pack("<HH", len(raw_name), 0) + raw_name + data)

        central += (_CEN_SIG
                    + struct.pack("<HH", (create_system << 8) | VERSION, VERSION)
                    + shared + sizes
                    + struct.pack("<HHHHH", len(raw_name), 0, 0, 0, INTERNAL_ATTR)
                    + struct.pack("<II", external_attr, offset) + raw_name)
        count += 1

    cd_offset = len(out)
    out += central
    out += _EOCD_SIG + struct.pack("<HHHHIIH", 0, 0, count, count, len(central),
                                   cd_offset, 0)
    return bytes(out)
