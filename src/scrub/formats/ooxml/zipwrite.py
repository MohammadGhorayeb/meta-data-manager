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
    out = bytearray()
    central = bytearray()
    count = 0

    for name in order_parts(parts):
        body = parts[name]
        try:
            raw_name = name.encode("ascii")
        except UnicodeEncodeError as exc:
            # A non-ASCII part name needs GP bit 11, which would make our flag word
            # non-constant. OPC part names are ASCII by spec, so this is a refusal
            # rather than a feature.
            raise ParseError(f"non-ASCII part name {name!r}") from exc
        if name.endswith("/"):
            raise ParseError(f"directory entry {name!r}: we do not write them")

        data = _deflate(body)
        crc = zlib.crc32(body)
        offset = len(out)

        # The two copies of the header are built from ONE tuple of values, because
        # the classic subtly-corrupt rewrite is the local header and the central
        # directory drifting apart. Note the DOS pair is time-then-date; writing it
        # the other way round is silent and produces a file dated 1980-01-00.
        shared = struct.pack("<HHHH", FLAGS, METHOD_DEFLATE, DOS_TIME, DOS_DATE)
        sizes = struct.pack("<III", crc, len(data), len(body))

        out += (_LOC_SIG + struct.pack("<H", VERSION) + shared + sizes
                + struct.pack("<HH", len(raw_name), 0) + raw_name + data)

        central += (_CEN_SIG
                    + struct.pack("<HH", (CREATE_SYSTEM << 8) | VERSION, VERSION)
                    + shared + sizes
                    + struct.pack("<HHHHH", len(raw_name), 0, 0, 0, INTERNAL_ATTR)
                    + struct.pack("<II", EXTERNAL_ATTR, offset) + raw_name)
        count += 1

    cd_offset = len(out)
    out += central
    out += _EOCD_SIG + struct.pack("<HHHHIIH", 0, 0, count, count, len(central),
                                   cd_offset, 0)
    return bytes(out)
