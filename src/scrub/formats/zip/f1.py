"""ZIP at F1: the archive rebuilt, every member scrubbed by its own handler.

Phase 6 M5 (`docs/p6_tail_plan.md` §1, §4 D4, §10). An archive carries two kinds of
metadata: the container's -- per entry a local-time DOS stamp, extended timestamps,
the creating account's Unix user and group ID (Info-ZIP's `ux`), permission modes
that show the operator's umask, comments -- and its members', each a file with its
own. Finder's *Compress* adds a third: an `__MACOSX/._<name>` sidecar per file
holding its extended attributes, which measured the URL a download came from, the
browser and the quarantine event.

F1 reads the archive (`ooxml/zipread`, fail closed) and writes a new one through the
shared writer (`ooxml/zipwrite.write_archive`): one canonical time, no extra fields,
no comments, one of four modes. Nothing of the input's container survives except
each member's name, its content and whether it is a directory, a link or an
executable -- what extracting it puts on a disk.

Per member:

- **sidecars are dropped** when their bytes say they are one: an AppleDouble file
  (`._<name>`, `__MACOSX/`), a Finder `.DS_Store`, a Windows `Thumbs.db`. They hold
  a file system's view of the files, never the files;
- **a file a handler claims is scrubbed by it** at its own F1 (`embedded.py`,
  identified by content), and an archive inside the archive by this module again,
  to a depth of four;
- **plain text is kept as written**: it has no metadata container, so what it says
  is content (reported by `kept`). An XMP packet saved as a file is the exception --
  a file that is nothing but metadata -- and counts as unknown;
- **anything else is refused**, every such member named in one message, unless the
  caller asked to keep unknown members byte for byte (`keep_unknown`, the CLI's
  `--keep-unknown-members`): the choice MAT2 offers, made explicit;
- a symbolic link stays a link to the same target, which is content.

Directory entries are written only for directories nothing else lies under (an
empty folder is content; an entry for a folder its files already imply is a
producer's habit). Members are sorted by name. Packages that happen to be ZIPs --
OOXML other than Word, OpenDocument, EPUB, Java, Android -- are refused by name: a
plain-archive scrub would keep their metadata parts as text.
"""
from __future__ import annotations

import datetime as _dt
import re
import struct
import zlib

from ... import embedded, resources
from ...errors import ParseError, ResourceError, ScrubError, UnsupportedFormatError
from ..ooxml import zipread, zipwrite
from . import appledouble

DEPTH_LIMIT = 4
UNIX = 3
CANONICAL_TIME = (zipwrite.DOS_DATE, zipwrite.DOS_TIME)
CANONICAL_ATTRS = {(m << 16) | (zipwrite.DOS_DIRECTORY if m == zipwrite.MODE_DIR else 0)
                   for m in (zipwrite.MODE_FILE, zipwrite.MODE_EXEC,
                             zipwrite.MODE_DIR, zipwrite.MODE_LINK)}

CFB_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
DS_STORE_MAGIC = b"\x00\x00\x00\x01Bud1"
UNICODE_PATH = 0x7075            # Info-ZIP: the UTF-8 name, when the header's isn't
EXT_TIME = 0x5455                # Info-ZIP `UT`: Unix times
UNIX_OWNER = 0x7875              # Info-ZIP `ux`: uid and gid
OLD_UNIX = 0x5855                # Info-ZIP `UX` (old): times, uid, gid

# Peak memory as a multiple of (archive + declared member sizes): measured 2.5 on
# macOS (every member inflated and kept, the compressed copies the reader slices,
# the output while it grows), with a margin. Held to the measurement by
# tests/scrub/test_zip.py::test_memory_need_holds.
MEMORY_FACTOR = 3.0

_CONTROL = re.compile(rb"[\x00-\x08\x0b\x0e-\x1f\x7f]")
_XMP_START = re.compile(rb"\A(?:\xef\xbb\xbf)?\s*(?:<\?xpacket|<x:xmpmeta)")


# --- what a member is -----------------------------------------------------------

def is_text(body: bytes) -> bool:
    """UTF-8 with no control characters but tab, newline, carriage return and form
    feed: text a person wrote or a program printed."""
    if _CONTROL.search(body):
        return False
    try:
        body.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return not _XMP_START.match(body)


def sidecar(name: str, body: bytes) -> str | None:
    """What a sidecar member holds, or None for a member that is a file. Decided by
    the bytes; the name only says where to look."""
    segs = name.rstrip("/").split("/")
    base = segs[-1]
    if segs[0] == "__MACOSX" and name.endswith("/"):
        return "a folder of Mac file attributes (__MACOSX)"
    if (base.startswith("._") or segs[0] == "__MACOSX") and \
            appledouble.is_appledouble(body):
        return "Mac file attributes (AppleDouble)"
    if base == ".DS_Store" and body[:8] == DS_STORE_MAGIC:
        return "a Finder folder view (.DS_Store)"
    if base.lower() == "thumbs.db" and body[:8] == CFB_MAGIC:
        return "a Windows thumbnail cache (Thumbs.db)"
    return None


def package_kind(archive: zipread.ZipArchive) -> str | None:
    """The kind of package an archive is, when it is one: refused by name."""
    names = set(archive.names)
    if "[Content_Types].xml" in names:
        return "an Office Open XML package other than Word (Excel, PowerPoint...)"
    first = archive.entries[0] if archive.entries else None
    if first is not None and first.name == "mimetype":
        try:
            kind = first.content().decode("ascii", "replace").strip()
        except ParseError:
            kind = "?"
        return f"a {kind!r} package (OpenDocument or EPUB)"
    if "META-INF/MANIFEST.MF" in names:
        return "a Java archive"
    if "AndroidManifest.xml" in names:
        return "an Android package"
    return None


def _unicode_path(e: zipread.ZipEntry) -> str | None:
    """Info-ZIP's UTF-8 name field, when its CRC matches the header name (a stale
    one -- the header renamed after it was written -- must not win)."""
    for blob in (e.extra_cen, e.extra_loc):
        for hid, body in _extras(blob):
            if hid == UNICODE_PATH and len(body) > 5 and body[0] == 1:
                crc = struct.unpack_from("<I", body, 1)[0]
                if crc == zlib.crc32(e.name_bytes):
                    try:
                        return body[5:].decode("utf-8")
                    except UnicodeDecodeError:
                        return None
    return None


def member_name(e: zipread.ZipEntry) -> tuple[bytes, bool, str]:
    """(name bytes to write, bit 11, name as text). A name is content and is kept;
    only its encoding is decided. UTF-8 -- flagged, in Info-ZIP's field, or simply
    valid, which is how Info-ZIP, `ditto` and libarchive all write it -- is written
    as UTF-8 with bit 11. A name in some legacy code page is written back as it
    was, unflagged, for readers to decode as they did."""
    raw = e.name_bytes or e.name.encode("cp437")
    text = _unicode_path(e)
    if text is None:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            if e.flags & zipread.FLAG_UTF8:
                raise ParseError(f"{e.name!r}: flagged UTF-8 but is not") from None
            return raw, False, e.name
    bad = zipread._unsafe_name(text)
    if bad:
        raise ParseError(f"{text!r}: {bad}")
    return text.encode("utf-8"), not text.isascii(), text


def _extras(blob: bytes):
    i = 0
    while i + 4 <= len(blob):
        hid, size = struct.unpack_from("<HH", blob, i)
        yield hid, blob[i + 4:i + 4 + size]
        i += 4 + size


def _unix_mode(e: zipread.ZipEntry) -> int | None:
    return e.external_attr >> 16 if e.create_system == UNIX else None


def _kind(e: zipread.ZipEntry) -> str:
    """'dir', 'link', 'exec' or 'file'. Anything else a Unix mode can name (a
    device, a pipe, a socket) is refused: extracting one is not extracting a file."""
    if e.name.endswith("/"):
        return "dir"
    mode = _unix_mode(e)
    if mode is None:
        return "file"
    ftype = mode & 0o170000
    if ftype == 0o120000:
        return "link"
    if ftype not in (0, 0o100000):
        raise ParseError(f"{e.name}: a special file (mode {mode:o}), not a file")
    return "exec" if mode & 0o111 else "file"


_MODE = {"file": zipwrite.MODE_FILE, "exec": zipwrite.MODE_EXEC,
         "link": zipwrite.MODE_LINK, "dir": zipwrite.MODE_DIR}


# --- the scrub ------------------------------------------------------------------

def _check_memory(archive: zipread.ZipArchive, where: str) -> None:
    declared = sum(e.uncomp_size for e in archive.entries)
    need = MEMORY_FACTOR * (archive.size + declared)
    free = resources.available_memory()
    if free is not None and need > free:
        raise ResourceError(
            f"{where or 'this archive'}: its members inflate to "
            f"{resources._human(declared)} and scrubbing needs about "
            f"{resources._human(need)}; this machine has {resources._human(free)} "
            "available. Nothing was written. Pass --skip-memory-check to try anyway.")


def _rewrap(exc: ScrubError, where: str) -> ScrubError:
    new = type(exc)(f"{where}: {exc}")
    new.__cause__ = exc
    return new


def scrub(data: bytes, *, keep_unknown: bool = False, check_memory: bool = True,
          dispatcher=None, where: str = "", depth: int = 0) -> bytes:
    """The archive rebuilt. `where` prefixes member paths in messages (the path of
    this archive inside an outer one)."""
    from ...dispatch import default_dispatcher
    if depth > DEPTH_LIMIT:
        raise ParseError(f"{where}: archives nested more than {DEPTH_LIMIT} deep "
                         "(a recursive archive is a decompression bomb)")
    archive = zipread.read(data)
    kind = package_kind(archive)
    if kind:
        raise UnsupportedFormatError(
            f"{where or 'this archive'} is {kind}: its metadata lives in named "
            "parts a plain-archive scrub would keep as text; not supported")
    if check_memory:
        _check_memory(archive, where)
    d = dispatcher or default_dispatcher()

    files: dict[bytes, zipwrite.Member] = {}
    dirs: dict[bytes, bool] = {}
    unknown: list[str] = []
    for e in archive.entries:
        name, utf8, text = member_name(e)
        path = f"{where}{text}"
        mk = _kind(e)
        if mk == "dir":
            if not sidecar(text, b""):
                dirs[name] = utf8
            continue
        body = e.content()
        if sidecar(text, body):
            continue
        if mk == "link":
            if not body or b"\x00" in body:
                raise ParseError(f"{path}: a symbolic link with no usable target")
        elif body:
            body = _scrub_member(body, path, d, keep_unknown, check_memory, depth,
                                 unknown)
        if name in files:
            raise ParseError(f"{path}: two members with one name")
        files[name] = zipwrite.Member(name, body, _MODE[mk], utf8)
    if unknown:
        shown = ", ".join(unknown[:10]) + (f" and {len(unknown) - 10} more"
                                           if len(unknown) > 10 else "")
        raise ParseError(
            f"{len(unknown)} member(s) of a type this project has no handler for, "
            f"refused rather than passed through unscrubbed: {shown}. "
            "--keep-unknown-members keeps such members byte for byte, unscrubbed")

    parents = {n[:i + 1] for n in list(files) + list(dirs)
               for i in range(len(n) - 1) if n[i:i + 1] == b"/"}
    for name, utf8 in dirs.items():
        if name not in parents and name not in files:
            files[name] = zipwrite.Member(name, b"", zipwrite.MODE_DIR, utf8)
    return zipwrite.write_archive([files[n] for n in sorted(files)])


def _scrub_member(body: bytes, path: str, d, keep_unknown: bool,
                  check_memory: bool, depth: int, unknown: list[str]) -> bytes:
    try:
        handler = d.resolve(body)
    except UnsupportedFormatError:
        handler = None
    if handler is None:
        if not is_text(body) and not keep_unknown:
            unknown.append(path)
        return body
    if handler.format_id == "zip":
        return scrub(body, keep_unknown=keep_unknown, check_memory=check_memory,
                     dispatcher=d, where=f"{path}/", depth=depth + 1)
    try:
        return embedded.scrub_bytes(body, path, d)
    except ScrubError as exc:
        if str(exc).startswith(f"{path}:"):
            raise
        raise _rewrap(exc, path) from exc


# --- reading it back: residuals, description, what is kept ---------------------

def _members(data: bytes, where: str = ""):
    """(path, name, entry, body or None, handler or None) for every entry; the body
    is None for directories."""
    from ...dispatch import default_dispatcher
    d = default_dispatcher()
    for e in zipread.read(data).entries:
        _, _, text = member_name(e)
        if e.name.endswith("/"):
            yield f"{where}{text}", text, e, None, None
            continue
        body = e.content()
        handler = None
        if body and _kind(e) != "link":
            try:
                handler = d.resolve(body)
            except UnsupportedFormatError:
                handler = None
        yield f"{where}{text}", text, e, body, handler


def residuals(data: bytes) -> list[str]:
    """What a scrubbed archive must not hold: any container field off its canonical
    value, a sidecar, or a member its own handler finds metadata in."""
    try:
        archive = zipread.read(data)
    except ParseError as exc:
        return [f"unreadable archive ({exc})"]
    out = []
    if archive.comment:
        out.append("archive comment")
    try:
        for path, name, e, body, handler in _members(data):
            if e.extra_cen or e.extra_loc:
                out.append(f"{path}: extra fields")
            if e.comment:
                out.append(f"{path}: entry comment")
            if (e.dos_date, e.dos_time) != CANONICAL_TIME:
                out.append(f"{path}: entry time")
            if (e.create_system, e.version_made_by, e.version_needed) != (
                    UNIX, zipwrite.VERSION, zipwrite.VERSION):
                out.append(f"{path}: producer version fields")
            if e.external_attr not in CANONICAL_ATTRS or e.internal_attr:
                out.append(f"{path}: file attributes")
            if e.flags & ~zipread.FLAG_UTF8:
                out.append(f"{path}: flags {e.flags:#x}")
            if sidecar(name, body or b""):
                out.append(f"{path}: sidecar")
            if handler is not None:
                out += embedded.residuals(body, path)
    except ScrubError as exc:
        out.append(f"unreadable member ({exc})")
    return out


def _dos(date: int, time: int) -> str:
    return (f"{1980 + (date >> 9):04d}-{(date >> 5) & 15:02d}-{date & 31:02d} "
            f"{time >> 11:02d}:{(time >> 5) & 63:02d}")


def _container(path: str, e: zipread.ZipEntry) -> dict[str, str]:
    out = {}
    for hid, body in _extras(e.extra_cen + e.extra_loc):
        if hid == UNIX_OWNER and len(body) >= 3:
            n = body[1]
            uid = int.from_bytes(body[2:2 + n], "little")
            m = body[2 + n] if len(body) > 2 + n else 0
            gid = int.from_bytes(body[3 + n:3 + n + m], "little")
            out["Unix owner (uid, gid)"] = f"{uid}, {gid}"
        elif hid in (EXT_TIME, OLD_UNIX) and len(body) >= 5:
            t = struct.unpack_from("<I", body, 1 if hid == EXT_TIME else 4)[0]
            out["file times (UTC)"] = _dt.datetime.fromtimestamp(
                t, _dt.UTC).strftime("%Y-%m-%d %H:%M:%S")
    if e.comment:
        out[f"{path}: entry comment"] = e.comment.decode("utf-8", "replace")
    return out


def describe(data: bytes, where: str = "") -> dict[str, str]:
    """The archive's metadata, then each member's as its own handler describes it,
    keyed by member path. Container fields held at our canonical values (the 1980
    time, the four modes) are not metadata and are not listed."""
    archive = zipread.read(data)
    out: dict[str, str] = {}
    if archive.comment:
        out[f"{where}archive comment"] = archive.comment.decode("utf-8", "replace")
    times, modes = set(), set()
    container: dict[str, str] = {}
    for path, name, e, body, handler in _members(data, where):
        if (e.dos_date, e.dos_time) != CANONICAL_TIME:
            times.add(_dos(e.dos_date, e.dos_time))
        mode = _unix_mode(e)
        if mode is not None and e.external_attr not in CANONICAL_ATTRS:
            modes.add(f"{mode & 0o7777:04o}")
        for k, v in _container(path, e).items():
            container.setdefault(f"{where}{k}" if ":" not in k else k, v)
        what = sidecar(name, body or b"")
        if what and body is None:
            continue                       # a folder; its sidecars are listed
        if what:
            out[path] = what
            for attr, value in appledouble.attributes(body or b"").items():
                out[f"{path}: {attr}"] = value
            continue
        if handler is None:
            continue
        if handler.format_id == "zip":
            try:
                out.update(describe(body, f"{path}/"))
            except ScrubError:
                pass
            continue
        fn = getattr(handler, "describe", None)
        try:
            for k, v in (fn(body) if fn else {}).items():
                out[f"{path}: {k}"] = v
        except Exception:                                 # noqa: BLE001
            pass
    if times:
        t = sorted(times)
        out[f"{where}entry times (local)"] = t[0] if len(t) == 1 else \
            f"{t[0]} to {t[-1]}"
    if modes:
        out[f"{where}permission modes"] = ", ".join(sorted(modes))
    out.update(container)
    return out


def kept(data: bytes) -> list[str]:
    """What the output knowingly holds that was not cleaned: text kept as written,
    unknown members kept on request, links."""
    text, unknown, links = [], [], []
    notes: dict[str, list[str]] = {}
    for path, _name, e, body, handler in _members(data):
        if not body:
            continue
        if _kind(e) == "link":
            links.append(f"{path} -> {body.decode('utf-8', 'replace')}")
        elif handler is None:
            (text if is_text(body) else unknown).append(path)
        elif handler.format_id != "zip":
            fn = getattr(handler, "kept", None)
            try:
                for note in (fn(body, "F1") if fn else []):
                    notes.setdefault(note, []).append(path)
            except Exception:                             # noqa: BLE001
                pass

    def _list(items):
        return ", ".join(items[:8]) + (f" and {len(items) - 8} more"
                                       if len(items) > 8 else "")
    out = []
    if text:
        out.append(f"kept as written (text is content, not metadata): {_list(text)}")
    if unknown:
        out.append(f"NOT scrubbed, kept byte for byte as asked: {_list(unknown)}")
    if links:
        out.append(f"links kept, pointing where they did: {_list(links)}")
    for note, paths in notes.items():
        out.append(f"{_list(paths)}: {note}")
    return out


_HOME = re.compile(r"(?:^|/)(?:home|Users)/[^/]+/|^[A-Za-z]:\\Users\\")


def advise(data: bytes) -> list[str]:
    """Risks in the input that F1 does not edit: names are content, and so is
    where a link points."""
    out = []
    homes = set()
    for path, _name, e, body, handler in _members(data):
        m = _HOME.search(path)
        if m:
            homes.add(m.group().strip("/"))
        if body and _kind(e) == "link":
            target = body.decode("utf-8", "replace")
            if target.startswith("/") or _HOME.search(target):
                out.append(f"{path} is a link to {target}: an absolute target names "
                           "a place on the machine the archive was made on, and is "
                           "kept (it is what the link means)")
            elif ".." in target.split("/"):
                out.append(f"{path} is a link to {target}, outside the archive's own "
                           "folders: kept, but extracting it points at whatever is "
                           "there on the recipient's machine")
        if handler is not None and handler.format_id != "zip":
            fn = getattr(handler, "advise", None)
            try:
                out += [f"{path}: {a}" for a in (fn(body) if fn else [])]
            except Exception:                             # noqa: BLE001
                pass
    if homes:
        out.append("member names include a home folder ("
                   + ", ".join(sorted(homes)) + "): names are content and are kept "
                   "-- rename the folder before archiving to remove it")
    return out
