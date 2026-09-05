"""ZIP reader for OOXML packages (byte depth).

Read from the **central directory**, never by scanning local headers forward. A ZIP
records each entry's header twice and the two copies can disagree; which one a reader
trusts is itself an attack surface, and the central directory is the one the format
designates as authoritative. Where the copies differ we say so rather than pick
silently.

This is a reader for *scrubbing*, so its job is accounting, not convenience:

  * every byte of the archive belongs to a named region, or we fail closed;
  * structures we cannot reason about — ZIP64, encryption, an entry that escapes the
    package, a name appearing twice — are refused rather than guessed at;
  * the metadata-bearing fields the stdlib normalises away (`external_attr`,
    `create_system`, the extra fields, the general-purpose flag bits) survive to the
    caller, because W8 measured those to be producer fingerprints.

`zipfile` is deliberately not used. It hides exactly what is being measured: it hands
back a tidy `ZipInfo` with no view of which header copy it read or whether they
agreed, and it cannot even emit `external_attr = 0` (docs/p3_documents_plan.md §2.7).
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field

from ...errors import ParseError

EOCD_SIG = b"PK\x05\x06"
EOCD64_SIG = b"PK\x06\x06"
EOCD64_LOC_SIG = b"PK\x06\x07"
CEN_SIG = b"PK\x01\x02"
LOC_SIG = b"PK\x03\x04"

STORED, DEFLATED = 0, 8

# GP flag bits that change how an entry must be read, as opposed to the ones that
# merely describe it.
FLAG_ENCRYPTED = 0x0001
FLAG_DATA_DESCRIPTOR = 0x0008
FLAG_STRONG_ENCRYPTION = 0x0040
FLAG_UTF8 = 0x0800

# An EOCD is 22 bytes plus a comment of up to 0xFFFF.
_EOCD_MAX = 22 + 0xFFFF


@dataclass
class ZipEntry:
    """One archive member, with the fields a scrubber has to decide about.

    The producer-identifying fields (W8 §2.7) are kept rather than normalised on read:
    a walker that drops them cannot tell a plugin what leaked.
    """
    name: str
    method: int
    flags: int
    dos_date: int
    dos_time: int
    crc: int
    comp_size: int
    uncomp_size: int
    version_made_by: int
    create_system: int
    version_needed: int
    internal_attr: int
    external_attr: int
    extra_cen: bytes
    extra_loc: bytes
    comment: bytes
    local_offset: int
    data_offset: int
    raw: bytes                      # still compressed
    header_disagreements: list[str] = field(default_factory=list)

    @property
    def is_dir(self) -> bool:
        return self.name.endswith("/")

    def content(self) -> bytes:
        """The entry's bytes, decompressed. Raises rather than returning a partial
        read: a part we cannot fully decode is a part we cannot vouch for."""
        if self.method == STORED:
            body = self.raw
        elif self.method == DEFLATED:
            try:
                body = zlib.decompress(self.raw, -15)
            except zlib.error as exc:
                raise ParseError(f"{self.name}: undecodable deflate ({exc})") from exc
        else:
            raise ParseError(f"{self.name}: unsupported compression method "
                             f"{self.method}")
        if len(body) != self.uncomp_size:
            raise ParseError(f"{self.name}: declared {self.uncomp_size} bytes, "
                             f"decoded {len(body)}")
        if zlib.crc32(body) != self.crc:
            raise ParseError(f"{self.name}: CRC mismatch")
        return body


@dataclass
class ZipArchive:
    entries: list[ZipEntry]
    comment: bytes
    cd_offset: int
    cd_size: int
    size: int

    def by_name(self, name: str) -> ZipEntry | None:
        for e in self.entries:
            if e.name == name:
                return e
        return None

    @property
    def names(self) -> list[str]:
        return [e.name for e in self.entries]


def _unsafe_name(name: str) -> str | None:
    """Why this name may not be trusted, or None.

    An OPC part name is an ASCII, absolute-ish, forward-slash path with no `.` or `..`
    segments. Everything rejected here is a real file that exists in the wild, and a
    scrubber that resolves such a name against the filesystem is a directory-traversal
    bug — so they are refused at the reader, before anything can act on them.
    """
    if not name:
        return "empty entry name"
    if "\\" in name:
        return "backslash in entry name"
    if name.startswith("/"):
        return "absolute entry name"
    if any(seg in (".", "..") for seg in name.split("/")):
        return "relative segment in entry name"
    if "\x00" in name:
        return "NUL in entry name"
    return None


def read(data: bytes) -> ZipArchive:
    """Parse an archive from the central directory outward. Fail closed."""
    tail_start = max(0, len(data) - _EOCD_MAX)
    idx = data.rfind(EOCD_SIG, tail_start)
    if idx < 0:
        raise ParseError("no end-of-central-directory record")
    (disk, cd_disk, n_this, n_total, cd_size, cd_offset, clen) = struct.unpack_from(
        "<HHHHIIH", data, idx + 4)
    if idx + 22 + clen != len(data):
        raise ParseError("trailing bytes after the end-of-central-directory record")
    if disk or cd_disk or n_this != n_total:
        raise ParseError("multi-disk archive")

    # ZIP64 is refused rather than half-supported: its 64-bit sizes live in extra
    # fields and in a second EOCD, and a reader that ignores them silently truncates.
    if data.rfind(EOCD64_LOC_SIG, tail_start) >= 0 or data.rfind(
            EOCD64_SIG, tail_start) >= 0:
        raise ParseError("ZIP64 archive (unsupported; refused rather than truncated)")
    if n_total == 0xFFFF or cd_offset == 0xFFFFFFFF or cd_size == 0xFFFFFFFF:
        raise ParseError("ZIP64 sentinel values in the end-of-central-directory record")

    entries: list[ZipEntry] = []
    seen: set[str] = set()
    p = cd_offset
    for _ in range(n_total):
        if data[p:p + 4] != CEN_SIG:
            raise ParseError(f"central directory record expected at offset {p}")
        (vmb, vneed, flags, method, dtime, ddate, crc, csize, usize,
         nlen, elen, cmlen, _disk, iattr, eattr, loff) = struct.unpack_from(
            "<HHHHHHIIIHHHHHII", data, p + 4)
        name_raw = data[p + 46:p + 46 + nlen]
        extra_cen = data[p + 46 + nlen:p + 46 + nlen + elen]
        comment = data[p + 46 + nlen + elen:p + 46 + nlen + elen + cmlen]
        p += 46 + nlen + elen + cmlen
        if p > cd_offset + cd_size:
            raise ParseError("central directory record runs past its declared size")

        name = name_raw.decode("utf-8" if flags & FLAG_UTF8 else "cp437", "replace")
        bad = _unsafe_name(name)
        if bad:
            raise ParseError(f"{name!r}: {bad}")
        if name in seen:
            # Two parts, one name: readers disagree about which wins, so "the
            # document" is not even well defined. The PDF hybrid-xref refusal, again.
            raise ParseError(f"duplicate entry name {name!r}")
        seen.add(name)
        if flags & (FLAG_ENCRYPTED | FLAG_STRONG_ENCRYPTION):
            raise ParseError(f"{name}: encrypted entry")

        if data[loff:loff + 4] != LOC_SIG:
            raise ParseError(f"{name}: no local header at offset {loff}")
        (vneed_l, flags_l, method_l, dtime_l, ddate_l, crc_l, csize_l, usize_l,
         nlen_l, elen_l) = struct.unpack_from("<HHHHHIIIHH", data, loff + 4)
        extra_loc = data[loff + 30 + nlen_l:loff + 30 + nlen_l + elen_l]
        start = loff + 30 + nlen_l + elen_l
        raw = data[start:start + csize]
        if len(raw) != csize:
            raise ParseError(f"{name}: entry data truncated")

        # ZIP64 hides in the extra field as often as in the EOCD: a single entry
        # over 4 GiB (or written with force_zip64) carries a 0x0001 record whose
        # 64-bit sizes SUPERSEDE the 32-bit ones read above. Ignoring it does not
        # fail loudly -- it silently reads the wrong length. Refuse instead.
        for blob in (extra_cen, extra_loc):
            i = 0
            while i + 4 <= len(blob):
                hid, hsize = struct.unpack_from("<HH", blob, i)
                if hid == 0x0001:
                    raise ParseError(
                        f"{name}: ZIP64 extra field (unsupported; refused rather "
                        f"than read at the wrong length)")
                i += 4 + hsize

        disagree: list[str] = []
        for label, a, b in (("method", method, method_l), ("flags", flags, flags_l),
                            ("time", dtime, dtime_l), ("date", ddate, ddate_l)):
            if a != b:
                disagree.append(f"{label}: central={a} local={b}")
        # A streaming writer sets bit 3 and leaves the local sizes and CRC at zero,
        # filling them into a data descriptor after the data. LibreOffice does this,
        # so it is normal rather than suspicious -- but only when the flag says so.
        if not (flags_l & FLAG_DATA_DESCRIPTOR):
            for label, a, b in (("crc", crc, crc_l), ("csize", csize, csize_l),
                                ("usize", usize, usize_l)):
                if a != b:
                    disagree.append(f"{label}: central={a} local={b}")

        entries.append(ZipEntry(
            name=name, method=method, flags=flags, dos_date=ddate, dos_time=dtime,
            crc=crc, comp_size=csize, uncomp_size=usize, version_made_by=vmb & 0xFF,
            create_system=vmb >> 8, version_needed=vneed, internal_attr=iattr,
            external_attr=eattr, extra_cen=extra_cen, extra_loc=extra_loc,
            comment=comment, local_offset=loff, data_offset=start, raw=raw,
            header_disagreements=disagree))

    first = data.find(LOC_SIG)
    if entries and first not in (0, -1) and first < cd_offset:
        # Bytes before the first local header are a self-extracting stub or a
        # concatenation. Either way they are unaccounted-for content: refuse.
        raise ParseError(f"{first} bytes before the first local header")

    return ZipArchive(entries=entries, comment=data[idx + 22:idx + 22 + clen],
                      cd_offset=cd_offset, cd_size=cd_size, size=len(data))
