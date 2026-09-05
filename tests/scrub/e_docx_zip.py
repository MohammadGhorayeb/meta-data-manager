"""E-DOCX-ZIP (W8/M6) — what real producers write into a ZIP, at byte level.

The W0 lesson applied to a second container: measure the peers before choosing a
writer. For PDF that spike caught qpdf's constant header comment and its inherited
`/ID[0]`. The ZIP equivalents are harder to see, because Python's `zipfile` *looks*
neutral and is not.

**This module reads the bytes itself and never uses `zipfile` to inspect.** That is
the whole point: `zipfile` normalises away most of what is being measured — it hands
back a `ZipInfo` with tidy fields and no view of which of the two copies of a header
it read, whether they agreed, or what sat in the extra field.

Two techniques worth naming:

- **Compression level has to be recovered, not read.** A ZIP stores *raw* deflate with
  no zlib wrapper, so there is no `78 9C` header byte to read the level off the way
  PDF W5 could. The level is recovered by recompressing the entry's own plaintext at
  every level (and both plausible `memLevel`s) and looking for a byte-exact match. A
  producer whose bytes match no zlib setting is not using zlib, and *that* is a
  sharper fingerprint than any level would have been.
- **Every field is read from BOTH copies.** A ZIP writes each entry's header twice,
  in the local header and in the central directory, and a rewrite that lets them drift
  is the classic subtly-corrupt output. Disagreements are reported per field.

Run it directly for the table:

    python -m tests.scrub.e_docx_zip
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field

EOCD_SIG = b"PK\x05\x06"
EOCD64_LOC_SIG = b"PK\x06\x07"
CEN_SIG = b"PK\x01\x02"
LOC_SIG = b"PK\x03\x04"

# ZIP's `version made by` high byte. 0 and 3 are the two that occur in practice;
# they say which OS wrote the file, which is a platform fingerprint sitting in every
# entry of the archive.
CREATE_SYSTEM = {0: "FAT/Windows", 3: "Unix", 10: "NTFS", 19: "OS X (Darwin)"}

# GP flag bits 1-2, for method 8, declare which deflate effort the writer used.
# Nobody reads them, so they are a free producer tell that survives any amount of
# tag stripping.
_DEFLATE_OPTION = {0: "normal", 1: "maximum", 2: "fast", 3: "super-fast"}

# Extra-field header IDs worth naming. The timestamp ones matter most: they carry
# SECOND- or 100-NANOSECOND-resolution clocks next to a DOS date field that everyone
# assumes is the only timestamp in a ZIP.
EXTRA_IDS = {
    0x0001: "ZIP64",
    0x000A: "NTFS-times(100ns)",
    0x5455: "UT-extended-time",
    0x7875: "Ux-uid/gid",
    0x9901: "AES",
    0xA220: "MS-OPC-growth-hint",   # Microsoft Open Packaging padding hint
}


@dataclass
class Entry:
    name: str
    version_made_by: int
    create_system: int
    version_needed_cen: int
    version_needed_loc: int | None
    flags_cen: int
    flags_loc: int | None
    method_cen: int
    method_loc: int | None
    dos_date: int
    dos_time: int
    crc: int
    comp_size: int
    uncomp_size: int
    extra_cen: list[int]
    extra_loc: list[int]
    internal_attr: int
    external_attr: int
    comment_len: int
    local_offset: int
    data: bytes = b""          # raw stored bytes (still compressed)
    mismatches: list[str] = field(default_factory=list)

    @property
    def is_dir(self) -> bool:
        return self.name.endswith("/")

    def timestamp(self) -> str:
        """DOS date/time -> readable. (0, 0) is what an unset field looks like."""
        if self.dos_date == 0 and self.dos_time == 0:
            return "0000-00-00 00:00:00"
        y = ((self.dos_date >> 9) & 0x7F) + 1980
        mo = (self.dos_date >> 5) & 0x0F
        d = self.dos_date & 0x1F
        h = (self.dos_time >> 11) & 0x1F
        mi = (self.dos_time >> 5) & 0x3F
        s = (self.dos_time & 0x1F) * 2
        return f"{y:04d}-{mo:02d}-{d:02d} {h:02d}:{mi:02d}:{s:02d}"


@dataclass
class Archive:
    path: str
    entries: list[Entry]
    eocd_comment: bytes
    zip64: bool
    prepended_bytes: int
    cd_offset: int
    size: int


def _parse_extra(blob: bytes) -> list[int]:
    """Extra fields are (id, size, payload) triples. A malformed one truncates the
    list rather than raising -- this is a *census*, and a file that lies about its
    own extra fields is a finding, not a crash."""
    out, i = [], 0
    while i + 4 <= len(blob):
        hid, hsize = struct.unpack_from("<HH", blob, i)
        out.append(hid)
        i += 4 + hsize
    return out


def read_archive(path: str) -> Archive:
    data = open(path, "rb").read()

    # EOCD is at the end but may be followed by a comment, so scan backwards.
    idx = data.rfind(EOCD_SIG)
    if idx < 0:
        raise ValueError(f"{path}: no end-of-central-directory record")
    (_, _, _, n_entries, _, cd_offset, comment_len) = struct.unpack_from(
        "<HHHHIIH", data, idx + 4)
    comment = data[idx + 22:idx + 22 + comment_len]
    zip64 = data.rfind(EOCD64_LOC_SIG) >= 0

    entries: list[Entry] = []
    p = cd_offset
    for _ in range(n_entries):
        if data[p:p + 4] != CEN_SIG:
            raise ValueError(f"{path}: central directory record expected at {p}")
        (vmb, vneed, flags, method, dtime, ddate, crc, csize, usize,
         nlen, elen, clen, _disk, iattr, eattr, loff) = struct.unpack_from(
            "<HHHHHHIIIHHHHHII", data, p + 4)
        name = data[p + 46:p + 46 + nlen].decode("utf-8", "replace")
        extra_cen = _parse_extra(data[p + 46 + nlen:p + 46 + nlen + elen])
        p += 46 + nlen + elen + clen

        # The local header: the second copy of the same fields.
        vneed_l = flags_l = method_l = None
        extra_loc: list[int] = []
        raw = b""
        mism: list[str] = []
        if data[loff:loff + 4] == LOC_SIG:
            (vneed_l, flags_l, method_l, dtime_l, ddate_l, crc_l, csize_l, usize_l,
             nlen_l, elen_l) = struct.unpack_from("<HHHHHIIIHH", data, loff + 4)
            extra_loc = _parse_extra(
                data[loff + 30 + nlen_l:loff + 30 + nlen_l + elen_l])
            start = loff + 30 + nlen_l + elen_l
            raw = data[start:start + csize]
            for label, a, b in (("method", method, method_l), ("flags", flags, flags_l),
                                ("time", dtime, dtime_l), ("date", ddate, ddate_l)):
                if a != b:
                    mism.append(f"{label} cen={a} loc={b}")
            # A streaming writer sets GP bit 3 and leaves the local sizes/CRC at
            # zero, filling them in a data descriptor after the data. Only a real
            # disagreement counts as a mismatch.
            if not (flags_l & 0x08):
                for label, a, b in (("crc", crc, crc_l), ("csize", csize, csize_l),
                                    ("usize", usize, usize_l)):
                    if a != b:
                        mism.append(f"{label} cen={a} loc={b}")
        else:
            mism.append(f"no local header at {loff}")

        entries.append(Entry(
            name=name, version_made_by=vmb & 0xFF, create_system=vmb >> 8,
            version_needed_cen=vneed, version_needed_loc=vneed_l,
            flags_cen=flags, flags_loc=flags_l, method_cen=method,
            method_loc=method_l, dos_date=ddate, dos_time=dtime, crc=crc,
            comp_size=csize, uncomp_size=usize, extra_cen=extra_cen,
            extra_loc=extra_loc, internal_attr=iattr, external_attr=eattr,
            comment_len=clen, local_offset=loff, data=raw, mismatches=mism))

    first_loc = data.find(LOC_SIG)
    return Archive(path=path, entries=entries, eocd_comment=comment, zip64=zip64,
                   prepended_bytes=max(first_loc, 0), cd_offset=cd_offset,
                   size=len(data))


# --------------------------------------------------------------------------- #
# Compression-level recovery
# --------------------------------------------------------------------------- #
_LEVELS = range(1, 10)
_MEMLEVELS = (8, 9)


def matching_levels(stored: bytes, plain: bytes) -> set[int]:
    """EVERY zlib level whose output equals these exact bytes.

    Returning the first match would have been wrong, and the first run of this
    census proved it: on inputs this small, levels 4 through 9 frequently produce
    byte-identical deflate output, so a first-match search reports 4 for a file
    written at 9. The honest unit of measurement is therefore an **equivalence
    class**, and a producer's actual setting is only pinned to the *intersection*
    of the classes across all of its entries -- which is what `level_class` below
    computes, and which is sometimes still ambiguous.

    An empty set means no zlib setting reproduces the bytes: the producer is not
    using zlib at all, which identifies the *implementation* rather than a level.
    """
    out: set[int] = set()
    for lvl in _LEVELS:
        for mem in _MEMLEVELS:
            co = zlib.compressobj(lvl, zlib.DEFLATED, -15, mem)
            if co.compress(plain) + co.flush() == stored:
                out.add(lvl)
                break
    return out


def _fmt_levels(levels: set[int]) -> str:
    if not levels:
        return "not-zlib"
    lo, hi = min(levels), max(levels)
    if len(levels) == hi - lo + 1:
        return str(lo) if lo == hi else f"{lo}-{hi}"
    return ",".join(str(x) for x in sorted(levels))


def entry_levels(arc: Archive) -> dict[str, str]:
    """name -> the level class consistent with that entry's bytes."""
    out: dict[str, str] = {}
    for e in arc.entries:
        if e.method_cen != 8 or e.is_dir or not e.data:
            continue
        try:
            plain = zlib.decompress(e.data, -15)
        except zlib.error:
            out[e.name] = "undecodable"
            continue
        out[e.name] = _fmt_levels(matching_levels(e.data, plain))
    return out


def level_class(arc: Archive) -> str:
    """The zlib level(s) consistent with EVERY deflated entry in the archive.

    This is the statistic W11 needs. A single surviving level is a producer pinned;
    several means the corpus is too small to separate them; none means either a
    non-zlib deflate or a producer that varies its level per entry -- two different
    findings, distinguished by whether any entry matched zlib at all.
    """
    classes: list[set[int]] = []
    for e in arc.entries:
        if e.method_cen != 8 or e.is_dir or not e.data:
            continue
        try:
            plain = zlib.decompress(e.data, -15)
        except zlib.error:
            continue
        classes.append(matching_levels(e.data, plain))
    if not classes:
        return "-"
    if all(not c for c in classes):
        return "not-zlib"
    common = set.intersection(*classes)
    if common:
        return _fmt_levels(common)
    return "varies per entry"


# --------------------------------------------------------------------------- #
# Summary + report
# --------------------------------------------------------------------------- #
def _modal(values) -> str:
    vals = list(values)
    if not vals:
        return "-"
    uniq = sorted(set(vals), key=lambda v: (-vals.count(v), str(v)))
    if len(uniq) == 1:
        return str(uniq[0])
    return f"{uniq[0]} (+{len(uniq) - 1} more)"


def summarise(arc: Archive) -> dict:
    es = arc.entries
    stored = [e for e in es if e.method_cen == 0 and not e.is_dir]
    levels = entry_levels(arc)
    ts = {e.timestamp() for e in es}
    scheme = ("epoch-1980" if ts == {"1980-01-01 00:00:00"}
              else "unset-zero" if ts == {"0000-00-00 00:00:00"}
              else "wall-clock" if len(ts) >= 1 else "-")
    extras = sorted({EXTRA_IDS.get(i, f"0x{i:04X}")
                     for e in es for i in e.extra_cen + e.extra_loc})
    return {
        "entries": len(es),
        "dir_entries": sum(1 for e in es if e.is_dir),
        "stored_uncompressed": len(stored),
        "create_system": _modal(
            f"{CREATE_SYSTEM.get(e.create_system, e.create_system)}"
            f"({e.create_system})" for e in es),
        "version_made_by": _modal(e.version_made_by for e in es),
        "version_needed": _modal(e.version_needed_cen for e in es),
        "flags": _modal(f"0x{e.flags_cen:04X}" for e in es),
        "data_descriptors": sum(1 for e in es if e.flags_cen & 0x08),
        "utf8_flag": sum(1 for e in es if e.flags_cen & 0x800),
        "timestamps": scheme,
        "distinct_timestamps": len(ts),
        "extra_fields": ", ".join(extras) or "none",
        "external_attr": _modal(f"0x{e.external_attr:08X}" for e in es),
        "zip64": arc.zip64,
        "eocd_comment": len(arc.eocd_comment),
        "prepended": arc.prepended_bytes,
        "mismatches": sum(len(e.mismatches) for e in es),
        "deflate_level": level_class(arc),
        "deflate_flag_bits": _modal(
            _DEFLATE_OPTION[(e.flags_cen >> 1) & 0x03] for e in arc.entries
            if e.method_cen == 8),
        "levels_detail": levels,
        "size": arc.size,
        "macosx_entries": sum(1 for e in es
                              if e.name.startswith("__MACOSX/")
                              or e.name.split("/")[-1].startswith("._")),
        "names": [e.name for e in es],
    }


_COLS = [
    ("entries", "entries"), ("dir_entries", "dir"),
    ("create_system", "create system"), ("version_made_by", "v.made"),
    ("version_needed", "v.need"), ("flags", "flags"),
    ("timestamps", "timestamp scheme"), ("extra_fields", "extra fields"),
    ("external_attr", "ext.attr"), ("deflate_level", "zlib level"),
    ("deflate_flag_bits", "deflate flag"),
    ("stored_uncompressed", "stored"), ("macosx_entries", "__MACOSX"),
    ("eocd_comment", "comment"), ("mismatches", "loc/cen disagree"),
]


def markdown(rows: dict[str, dict]) -> str:
    head = "| field | " + " | ".join(rows) + " |"
    sep = "|---|" + "|".join("---" for _ in rows) + "|"
    out = [head, sep]
    for key, label in _COLS:
        out.append(f"| **{label}** | "
                   + " | ".join(str(rows[p][key]) for p in rows) + " |")
    return "\n".join(out)


def main() -> int:
    import tempfile

    from . import docx_corpus as C

    with tempfile.TemporaryDirectory() as td:
        paths = C.producers(td)
        missing = [k for k, v in C.available_producers().items() if not v]
        rows = {}
        for name, p in sorted(paths.items()):
            try:
                rows[name] = summarise(read_archive(p))
            except Exception as exc:                      # noqa: BLE001
                print(f"!! {name}: {exc}")
        print("# E-DOCX-ZIP — container conventions by producer\n")
        print(markdown(rows))
        print()
        if missing:
            print(f"NOT MEASURED (producer absent): {', '.join(missing)}")
        print("\n## Per-entry deflate settings\n")
        for name, r in rows.items():
            print(f"- **{name}** ({r['size']} B): "
                  + ", ".join(f"{k}={v}" for k, v in r["levels_detail"].items()))
        print("\n## Entry order\n")
        for name, r in rows.items():
            print(f"- **{name}**: " + " → ".join(r["names"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
