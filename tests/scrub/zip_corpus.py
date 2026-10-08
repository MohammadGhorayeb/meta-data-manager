"""ZIP corpus for CI: archives shaped like what real producers write, every locus
planted (`docs/p6_tail_plan.md` §1, §10).

Written byte by byte (not through `zipfile`, which cannot write half of these
fields), in four producer shapes measured in the survey:

- `infozip`: version 3.0, `UT` extended times and `ux` uid/gid on every entry, a
  directory entry per folder, the text flag on text, an archive comment, an entry
  comment, and the `.DS_Store` a `zip -r` of a Finder folder picks up;
- `ditto` (Finder's *Compress*): version 2.1, data descriptors, the old `UX` times,
  Apple's attribute bits, and an `__MACOSX/._<name>` AppleDouble sidecar per file
  and folder holding the download URL and the quarantine record;
- `python`: no extras, no directory entries, the operator's umask in every mode;
- `windows` (Explorer): FAT create system, the archive bit, NTFS times, a
  `Thumbs.db`.

Every shape holds the same content: a photo with EXIF (GPS), a PNG with a text
chunk, an SVG with a comment, plain text, an executable script, an empty file, an
empty folder, and a nested archive with its own comment and a PNG of its own.
Planted values derive from `variant` at a fixed length; `seed` changes the content
so diverse inputs share none (what the fingerprint guard needs). Imports nothing
from `src`.
"""
from __future__ import annotations

import io
import plistlib
import struct
import zlib
from dataclasses import dataclass, field

from PIL import Image

CFB_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
SHAPES = ("infozip", "ditto", "python", "windows")


def secret(name: str, variant: int = 0) -> str:
    return f"SENTINEL-{name}-V{variant:02d}"


# --- member content -----------------------------------------------------------

def photo(variant: int = 0, seed: int = 0) -> bytes:
    img = Image.new("RGB", (32 + seed * 2, 24), (30 + seed * 41 % 200, 140, 90))
    img.paste((200, 30, 60), (0, 0, 10, 24))
    exif = Image.Exif()
    exif[0x013B] = secret("PHOTO-ARTIST", variant)
    exif[0x0131] = secret("PHOTO-SOFT", variant)
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2] = "N", (40.0, 42.0, 46.0 + variant)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88, exif=exif.tobytes())
    return buf.getvalue()


def png(variant: int = 0, key: str = "PNG-AUTHOR", seed: int = 0) -> bytes:
    img = Image.new("RGB", (10 + seed, 10), (seed * 23 % 250, 60, 200))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    raw = buf.getvalue()
    text = b"Author\0" + secret(key, variant).encode()
    chunk = struct.pack(">I", len(text)) + b"tEXt" + text + struct.pack(
        ">I", zlib.crc32(b"tEXt" + text) & 0xFFFFFFFF)
    return raw[:33] + chunk + raw[33:]


def svg(variant: int = 0, seed: int = 0) -> bytes:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8">'
            f'<!-- {secret("SVG-COMMENT", variant)} -->'
            f'<rect width="8" height="{4 + seed % 4}" fill="#3060a0"/>'
            + '<g/>' * seed + '</svg>').encode()


def text(seed: int = 0) -> bytes:
    return (f"Meeting notes {seed}\nWhat the archive is for: its content.\n"
            + "." * (seed * 7)).encode()


def script(seed: int = 0) -> bytes:
    return f"#!/bin/sh\necho run {seed}{'!' * seed * 5}\n".encode()


# --- sidecars -----------------------------------------------------------------

def appledouble(attrs: dict[str, bytes], fork: bytes = b"") -> bytes:
    """An AppleDouble file as `ditto` writes it: Finder info with an `ATTR` block,
    and the resource fork (empty unless given)."""
    names = [n.encode() + b"\x00" for n in attrs]
    table_len = sum((11 + len(n) + 3) & ~3 for n in names)
    data_start = 50 + 34 + 36 + table_len          # Finder info, pad, ATTR header
    entries, values, off = b"", b"", data_start
    for n, v in zip(names, attrs.values(), strict=True):
        e = struct.pack(">IIHB", off, len(v), 0, len(n)) + n
        entries += e + b"\x00" * (((len(e) + 3) & ~3) - len(e))
        values += v
        off += len(v)
    attr = (b"ATTR" + struct.pack(">IIII", 0, data_start + len(values), data_start,
                                  len(values))
            + b"\x00" * 12 + struct.pack(">HH", 0, len(attrs)) + entries + values)
    finder = b"\x00" * 32 + b"\x00\x00" + attr
    return (b"\x00\x05\x16\x07\x00\x02\x00\x00" + b"Mac OS X        "
            + struct.pack(">H", 2) + struct.pack(">III", 9, 50, len(finder))
            + struct.pack(">III", 2, 50 + len(finder), len(fork)) + finder + fork)


def where_froms(variant: int = 0) -> bytes:
    return plistlib.dumps([f"https://{secret('HOST', variant)}.example/photo.jpg",
                           f"https://{secret('REFERRER', variant)}.example/"],
                          fmt=plistlib.FMT_BINARY)


def quarantine(variant: int = 0) -> bytes:
    return f"q/0083;6703f1a2;Safari;{secret('QUARANTINE', variant)}".encode()


def ds_store(variant: int = 0) -> bytes:
    return (b"\x00\x00\x00\x01Bud1" + b"\x00" * 24
            + secret("DSSTORE", variant).encode("utf-16-be") + b"\x00" * 16)


def thumbs_db(variant: int = 0) -> bytes:
    return CFB_MAGIC + b"\x00" * 24 + secret("THUMBS", variant).encode("utf-16-le")


# --- the raw writer -----------------------------------------------------------

@dataclass
class Entry:
    name: bytes
    body: bytes = b""
    method: int = 8
    flags: int = 0
    when: tuple = (2001, 2, 3, 4, 5, 6)
    create_system: int = 3
    made_by: int = 20
    needed: int = 20
    internal: int = 0
    external: int = 0o100644 << 16
    extra_loc: bytes = b""
    extra_cen: bytes = b""
    comment: bytes = b""
    extra_kw: dict = field(default_factory=dict)


def _dos(when: tuple) -> tuple[int, int]:
    y, mo, d, h, mi, s = when
    return ((y - 1980) << 9) | (mo << 5) | d, (h << 11) | (mi << 5) | (s // 2)


def _deflate(body: bytes, level: int = 9) -> bytes:
    co = zlib.compressobj(level, zlib.DEFLATED, -15)
    return co.compress(body) + co.flush()


def write(entries: list[Entry], comment: bytes = b"") -> bytes:
    out, cen = bytearray(), bytearray()
    for e in entries:
        data = _deflate(e.body) if e.method == 8 else e.body
        crc = zlib.crc32(e.body)
        date, time = _dos(e.when)
        loc = (0, 0, 0) if e.flags & 8 else (crc, len(data), len(e.body))
        off = len(out)
        out += (b"PK\x03\x04" + struct.pack("<HHHHHIIIHH", e.needed, e.flags,
                                            e.method, time, date, *loc, len(e.name),
                                            len(e.extra_loc))
                + e.name + e.extra_loc + data)
        if e.flags & 8:
            out += b"PK\x07\x08" + struct.pack("<III", crc, len(data), len(e.body))
        cen += (b"PK\x01\x02" + struct.pack(
            "<HHHHHHIIIHHHHHII", (e.create_system << 8) | e.made_by, e.needed,
            e.flags, e.method, time, date, crc, len(data), len(e.body), len(e.name),
            len(e.extra_cen), len(e.comment), 0, e.internal, e.external, off)
            + e.name + e.extra_cen + e.comment)
    n = len(entries)
    cd = len(out)
    out += cen
    out += b"PK\x05\x06" + struct.pack("<HHHHIIH", 0, 0, n, n, len(cen), cd,
                                       len(comment)) + comment
    return bytes(out)


def _x(hid: int, body: bytes) -> bytes:
    return struct.pack("<HH", hid, len(body)) + body


# --- the archive --------------------------------------------------------------

def nested(variant: int = 0, seed: int = 0) -> bytes:
    """An archive inside the archive, with metadata of its own."""
    return write([Entry(b"inner/picture.png", png(variant, "NESTED-PNG", seed),
                        when=(2002, 3, 4, 5 + variant, 6, 8)),
                  Entry(b"inner/readme.txt", text(seed + 100),
                        comment=secret("NESTED-ENTRY", variant).encode())],
                 comment=secret("NESTED-COMMENT", variant).encode())


def content(seed: int = 0) -> dict[str, bytes]:
    """{name: body} of the files every shape carries, metadata-free parts only
    (the members with metadata are built per variant)."""
    return {"docs/notes.txt": text(seed), "tools/run.sh": script(seed),
            "docs/empty.txt": b""}


def _files(variant: int, seed: int) -> list[tuple[str, bytes, str]]:
    """(name, body, kind) for every member: kind is file, exec, dir."""
    return [("docs/", b"", "dir"), ("docs/empty.txt", b"", "file"),
            ("docs/notes.txt", text(seed), "file"),
            ("docs/drawing.svg", svg(variant, seed), "file"),
            ("empty/", b"", "dir"),
            ("photos/", b"", "dir"), ("photos/photo.jpg", photo(variant, seed), "file"),
            ("photos/icon.png", png(variant, seed=seed), "file"),
            ("photos/nested.zip", nested(variant, seed), "file"),
            ("tools/", b"", "dir"), ("tools/run.sh", script(seed), "exec")]


def build(variant: int = 0, *, shape: str = "infozip", seed: int = 0) -> bytes:
    """A seed also puts everything under a folder of its own, its name a different
    length per seed, so diverse archives share no member name, name length or
    offset."""
    v = variant
    when = (2001, 2, 3, 4 + v, 5, 6)
    unix_t = 1_000_000_000 + v * 3600
    entries: list[Entry] = []
    top = f"{chr(ord('a') + seed) * (seed + 2)}/" if seed else ""
    files = [(top + n, b, k) for n, b, k in _files(v, seed)]
    if shape == "infozip":
        ut = _x(0x5455, b"\x03" + struct.pack("<II", unix_t, unix_t))
        ux = _x(0x7875, b"\x01\x04" + struct.pack("<I", 501 + v) + b"\x04"
                + struct.pack("<I", 20))
        files.insert(6, (top + "photos/.DS_Store", ds_store(v), "file"))
        for name, body, kind in files:
            mode = {"dir": 0o40755, "exec": 0o100755, "file": 0o100644}[kind]
            entries.append(Entry(
                name.encode(), body, method=0 if not body else 8, when=when,
                made_by=30, needed=10 if not body else 20,
                internal=1 if name.endswith((".txt", ".sh")) else 0,
                external=(mode << 16) | (0x10 if kind == "dir" else 0),
                extra_loc=ut + ux, extra_cen=_x(0x5455, b"\x03"
                                                + struct.pack("<I", unix_t)) + ux,
                comment=secret("ENTRY-COMMENT", v).encode()
                if name == "docs/notes.txt" else b""))
        return write(entries, comment=secret("ARCHIVE-COMMENT", v).encode())
    if shape == "ditto":
        ux_c = _x(0x5855, struct.pack("<II", unix_t, unix_t))
        ux_l = _x(0x5855, struct.pack("<IIHH", unix_t, unix_t, 501 + v, 20))
        sidecars = []
        for name, body, kind in files:
            mode = {"dir": 0o40755, "exec": 0o100755, "file": 0o100644}[kind]
            entries.append(Entry(
                name.encode(), body, method=8 if body else 0,
                flags=8 if body else 0, when=when, made_by=21,
                needed=20 if body else 10, external=(mode << 16) | 0x4000,
                extra_loc=ux_l, extra_cen=ux_c))
            base = name.rstrip("/")
            parent, _, leaf = base.rpartition("/")
            attrs = {"com.apple.provenance": b"\x01\x02\x00" + bytes(8)}
            if name.endswith(".jpg"):
                attrs["com.apple.metadata:kMDItemWhereFroms"] = where_froms(v)
                attrs["com.apple.quarantine"] = quarantine(v)
            side = f"__MACOSX/{parent + '/' if parent else ''}._{leaf}"
            sidecars.append(Entry(side.encode(), appledouble(attrs), flags=8,
                                  when=when, made_by=21,
                                  external=(0o100644 << 16) | 0x4000,
                                  extra_loc=ux_l, extra_cen=ux_c))
        dirs = [Entry(b"__MACOSX/", b"", method=0, when=when, made_by=21, needed=10,
                      external=(0o40775 << 16) | 0x4000, extra_loc=ux_l,
                      extra_cen=ux_c)]
        return write(entries + dirs + sidecars)
    if shape == "python":
        umask_mode = (0o100600, 0o100664)[v % 2]
        for name, body, kind in files:
            if kind == "dir":
                if name.endswith("empty/"):
                    entries.append(Entry(name.encode(), b"", method=0, when=when,
                                         external=(0o40700 << 16) | 0x10))
                continue
            mode = 0o100700 if kind == "exec" else umask_mode
            entries.append(Entry(name.encode(), body, when=when,
                                 external=mode << 16))
        return write(list(reversed(entries)))
    if shape == "windows":
        ft = 116_444_736_000_000_000 + unix_t * 10_000_000
        ntfs = _x(0x000A, bytes(4) + struct.pack("<HH", 1, 24)
                  + struct.pack("<QQQ", ft, ft, ft))
        files.insert(6, (top + "photos/Thumbs.db", thumbs_db(v), "file"))
        for name, body, kind in files:
            if kind == "dir" and not name.endswith("empty/"):
                continue
            entries.append(Entry(
                name.encode(), body, method=8 if body else 0, when=when,
                create_system=0, made_by=20, external=0x10 if kind == "dir" else 0x20,
                extra_loc=ntfs, extra_cen=ntfs))
        return write(entries)
    raise ValueError(shape)


def planted(variant: int = 0, shape: str = "infozip") -> list[bytes]:
    """Every planted value, as bytes: present in the input's raw bytes or in some
    member once decompressed (the A1 search looks in both)."""
    names = ["PHOTO-ARTIST", "PHOTO-SOFT", "PNG-AUTHOR", "SVG-COMMENT", "NESTED-PNG",
             "NESTED-ENTRY", "NESTED-COMMENT"]
    names += {"infozip": ["ARCHIVE-COMMENT", "ENTRY-COMMENT"],
              "ditto": ["HOST", "REFERRER", "QUARANTINE"], "python": [],
              "windows": []}[shape]
    out = [secret(n, variant).encode() for n in names]
    if shape == "infozip":
        out.append(secret("DSSTORE", variant).encode("utf-16-be"))
    if shape == "windows":
        out.append(secret("THUMBS", variant).encode("utf-16-le"))
    return out


def expanded(data: bytes) -> list[bytes]:
    """The archive's raw bytes and every member's decompressed bytes, recursively --
    where a planted value can hide (zipfile here: a test's view, not the reader
    under test)."""
    import zipfile
    out = [data]
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for info in z.infolist():
                body = z.read(info)
                out.append(body)
                if body[:4] == b"PK\x03\x04":
                    out += expanded(body)
    except zipfile.BadZipFile:
        pass
    return out
