"""MP4 / MOV F1 — bit-preserving metadata strip. Every kept sample is the same bytes.

What goes (p4 plan §5.3 has the measurements):
  * `moov/meta`, `trak/meta` — QuickTime `mdta` keys: GPS, make, model, OS version,
    creation date with its UTC offset, lens model, the microphone's name.
  * `udta`, `uuid`, `free`, `skip`, `wide` anywhere inside `moov`.
  * Timed-metadata tracks (handler `meta`): per-frame face detection, live-photo
    info, scene illuminance, and a per-recording UUID. The track, its samples, and
    every `tref` entry naming it.
  * Every top-level box except `ftyp`, `moov`, `mdat`. This is a KEEP-list on
    purpose: an iPhone clip under ten seconds ends with a `free` box holding a stale
    copy of the metadata, exact GPS included, and a drop-list only catches the boxes
    somebody already knew about.
  * `mvhd`/`tkhd`/`mdhd` creation and modification times (the M4A rule).
  * Handler names (`Core Media Video` and so on) and the `hdlr` reserved words, where
    QuickTime writes the component manufacturer `appl`. Both are spec'd as reserved
    or free text, and no decoder reads them.

THE REASON THIS TIER REBUILDS `mdat` INSTEAD OF COPYING IT: the camera writes a stale
copy of its metadata into `mdat` once every ten seconds of recording, in bytes no
sample table points at. Copying `mdat` whole -- which is what M4A F1 does, and what
ExifTool does -- keeps the GPS. So the output `mdat` is assembled chunk by chunk from
the tables of the tracks we keep, in the original file order (the interleave is
untouched), and every chunk offset is rewritten individually. Unreferenced bytes and
dropped tracks' samples are not removed by a deletion pass. They are never written.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from itertools import pairwise

from ...errors import ParseError, ScrubError
from ...standards import isobmff as iso

KEEP_TOP = {b"ftyp", b"moov", b"mdat"}
# Movie fragments put sample tables outside `moov`; nothing here models them.
FRAGMENT_TOP = {b"moof", b"mfra", b"sidx", b"styp", b"ssix", b"emsg"}
DROP_IN_MOOV = {b"udta", b"meta", b"uuid", b"free", b"skip", b"wide"}
# Encryption. A protected track cannot be decoded to prove the picture survived,
# and the boxes that say so (`sinf`, `pssh`, a key ID in `tenc`) are refused rather
# than kept or dropped: dropping them leaves samples nothing can decrypt. The sample
# entries matter separately, because a scan for boxes walks straight past an
# `encv` that is only visible as the first four bytes of an `stsd` entry.
PROTECTION_BOXES = {b"sinf", b"pssh", b"schm", b"frma", b"senc", b"tenc"}
PROTECTED_SAMPLE_ENTRIES = {b"encv", b"enca", b"encs", b"drms", b"drmi"}

KEEP_HANDLERS = {b"vide", b"soun"}
# Measured: Apple's timed metadata (`mebx`). A handler in neither set is refused
# rather than guessed at -- a subtitle track is content, a timecode track may be a
# time of day, and neither has been measured.
DROP_HANDLERS = {b"meta"}


@dataclass
class _Track:
    box: iso.Box
    track_id: int
    handler: bytes
    stbl: iso.Box | None = None
    chunks: list[iso.Chunk] | None = None


def _u32(b: bytes, at: int) -> int:
    return int.from_bytes(b[at:at + 4], "big")


def _track_id(tkhd: iso.Box) -> int:
    p = tkhd.payload
    if not p or p[0] not in (0, 1):
        # Read as version 0, a later version yields a plausible wrong track ID.
        raise ParseError(f"MP4: tkhd version {p[0] if p else '?'} not modelled")
    at = 4 + (16 if p[0] == 1 else 8)          # after version/flags + two times
    return _u32(p, at)


def _tracks(moov: iso.Box) -> list[_Track]:
    out = []
    for trak in (c for c in moov.children if c.type == b"trak"):
        tkhd, hdlr = trak.find(b"tkhd"), trak.find(b"mdia/hdlr")
        if tkhd is None or hdlr is None or len(hdlr.payload) < 12:
            raise ParseError("MP4: a track without tkhd or a media handler")
        out.append(_Track(box=trak, track_id=_track_id(tkhd),
                          handler=hdlr.payload[8:12]))
    return out


def _check_self_contained(trak: iso.Box) -> None:
    """Every data reference must say "the samples are in this file" (flag 1). An
    external reference means content lives elsewhere, and a scrub of this file alone
    could neither preserve nor vouch for it."""
    dref = trak.find(b"mdia/minf/dinf/dref")
    if dref is None:
        raise ParseError("MP4: a track with no data reference")
    p = dref.payload
    at, n = 8, _u32(p, 4)
    for _ in range(n):
        size = _u32(p, at)
        if size < 12 or at + size > len(p):
            raise ParseError("MP4: malformed data reference")
        if not p[at + 11] & 1:
            raise ParseError("MP4: a track's samples live in another file "
                             "(external data reference) -- refusing")
        at += size


def _check_unprotected(moov: iso.Box) -> None:
    for box in moov.walk():
        if box.type in PROTECTION_BOXES:
            raise ParseError(f"MP4: {box.type.decode('latin-1')} box -- the file "
                             "is encrypted, refusing")
        if box.type == b"stsd":
            p, at = box.payload, 8
            for _ in range(_u32(p, 4) if len(p) >= 8 else 0):
                size, entry = _u32(p, at), p[at + 4:at + 8]
                if entry in PROTECTED_SAMPLE_ENTRIES:
                    raise ParseError(f"MP4: {entry.decode('latin-1')} sample "
                                     "entry -- the track is encrypted, refusing")
                if size < 8 or at + size > len(p):
                    raise ParseError("MP4: malformed sample description")
                at += size


def _prune_tref(trak: iso.Box, dropped: set[int]) -> None:
    tref = trak.find(b"tref")
    if tref is None:
        return
    kept = []
    for ref in iso.parse(tref.payload) if tref.payload else tref.children:
        ids = [_u32(ref.payload, i) for i in range(0, len(ref.payload) - 3, 4)]
        ids = [i for i in ids if i not in dropped]
        if ids:
            kept.append(iso.Box(type=ref.type, offset=0, size=0, header_len=8,
                                payload=b"".join(i.to_bytes(4, "big") for i in ids)))
    if kept:
        tref.payload, tref.children = iso.serialize(kept), []
    else:
        trak.children = [c for c in trak.children if c is not tref]


def _layout(data: bytes) -> tuple[list[iso.Box], iso.Box, iso.Box]:
    tops = iso.scan(data)
    types = [b.type for b in tops]
    if types.count(b"ftyp") != 1:
        raise ParseError("MP4: expected exactly one ftyp")
    frag = FRAGMENT_TOP.intersection(types)
    if frag:
        raise ParseError(f"MP4: fragmented file ({sorted(frag)}) -- not modelled")
    if types.count(b"moov") != 1 or types.count(b"mdat") != 1:
        raise ParseError("MP4: expected exactly one moov and one mdat")
    moov = next(b for b in tops if b.type == b"moov")
    mdat = next(b for b in tops if b.type == b"mdat")
    return tops, moov, mdat


def scrub(data: bytes) -> bytes:
    tops, moov_hdr, mdat_hdr = _layout(data)
    ftyp = iso.parse(data[tops[0].offset:tops[0].end])[0] \
        if tops[0].type == b"ftyp" else None
    if ftyp is None:
        raise ParseError("MP4: ftyp is not the first box")
    moov = iso.parse(data[moov_hdr.offset:moov_hdr.end])[0]
    if moov.find(b"mvex") is not None:
        raise ParseError("MP4: fragmented file (mvex) -- not modelled")
    _check_unprotected(moov)

    tracks = _tracks(moov)
    unknown = {t.handler for t in tracks} - KEEP_HANDLERS - DROP_HANDLERS
    if unknown:
        raise ParseError(f"MP4: track handler(s) {sorted(unknown)} not modelled -- "
                         "refusing rather than guessing whether they are content")
    kept = [t for t in tracks if t.handler in KEEP_HANDLERS]
    if not kept:
        raise ParseError("MP4: no audio or video track to preserve")
    dropped_ids = {t.track_id for t in tracks if t.handler in DROP_HANDLERS}

    lo, hi = mdat_hdr.offset + mdat_hdr.header_len, mdat_hdr.end
    for t in kept:
        _check_self_contained(t.box)
        t.stbl = t.box.find(b"mdia/minf/stbl")
        if t.stbl is None:
            raise ParseError("MP4: a kept track has no sample table")
        t.chunks = iso.chunks(t.stbl, limit=len(data))
        for c in t.chunks:
            if c.sample_sizes and not (lo <= c.offset and c.end <= hi):
                raise ParseError("MP4: a sample lies outside mdat -- refusing")

    # File order across every kept track, so the interleave is preserved.
    order = sorted(((c.offset, ti, c) for ti, t in enumerate(kept) for c in t.chunks
                    if c.sample_sizes), key=lambda x: (x[0], x[1]))
    for (_, _, a), (_, _, b) in pairwise(order):
        if b.offset < a.end:
            raise ParseError("MP4: two chunks overlap -- refusing")
    before = _track_digests(data, [t.chunks for t in kept])

    # Rebuild moov: drop timed-metadata tracks, then everything else.
    moov.children = [c for c in moov.children
                     if not (c.type == b"trak"
                             and any(c is t.box for t in tracks
                                     if t.handler in DROP_HANDLERS))]
    for t in kept:
        _prune_tref(t.box, dropped_ids)
    moov.children = iso.strip_tree(moov.children, DROP_IN_MOOV,
                                   blank_handler_names=True)

    # Sizes first, offsets second: every table has a fixed width, so moov's size
    # does not depend on the values written into it, and the two never chase.
    payload_len = sum(c.size for _, _, c in order)
    mdat_header = 8 if payload_len + 8 <= 0xFFFFFFFF else 16
    ftyp_bytes = iso.serialize([ftyp])
    moov_len = len(iso.serialize([moov]))
    out_order = [b.type for b in tops if b.type in KEEP_TOP]
    cursor = 0
    for btype in out_order:
        if btype == b"mdat":
            break
        cursor += len(ftyp_bytes) if btype == b"ftyp" else moov_len
    data_start = cursor + mdat_header

    new_offsets, cursor = {}, data_start
    for _, _, c in order:
        new_offsets[id(c)] = cursor
        cursor += c.size
    for t in kept:
        # A chunk with no samples points at nothing; give it the start of mdat.
        iso.set_chunk_offsets(t.stbl, [new_offsets.get(id(c), data_start)
                                       for c in t.chunks])
    moov_bytes = iso.serialize([moov])
    if len(moov_bytes) != moov_len:
        raise ScrubError("MP4: moov changed size when offsets were filled in")

    # Assembled as a list of views and joined ONCE: a bytearray grown chunk by
    # chunk and then copied into `bytes` holds the whole output twice at its peak,
    # which was a third full copy of the video beside the input (limit #38).
    view = memoryview(data)
    parts: list = []
    for btype in out_order:
        if btype == b"ftyp":
            parts.append(ftyp_bytes)
        elif btype == b"moov":
            parts.append(moov_bytes)
        else:
            total = mdat_header + payload_len
            parts.append(total.to_bytes(4, "big") + b"mdat" if mdat_header == 8 else
                         (1).to_bytes(4, "big") + b"mdat" + total.to_bytes(8, "big"))
            parts.extend(view[c.offset:c.end] for _, _, c in order)
    result = b"".join(parts)
    del parts, view

    # The promise of this tier, checked on the output as a reader would find it:
    # every kept track's samples are the same bytes, in the same order.
    _, out_moov_hdr, _ = _layout(result)
    out_moov = iso.parse(result[out_moov_hdr.offset:out_moov_hdr.end])[0]
    after = _track_digests(result, [iso.chunks(t.box.find(b"mdia/minf/stbl"),
                                               limit=len(result))
                                    for t in _tracks(out_moov)])
    if after != before:
        raise ScrubError("MP4 F1 altered the samples of a kept track")
    return result


def _track_digests(data: bytes, per_track: list[list[iso.Chunk]]) -> list[str]:
    view, out = memoryview(data), []
    for chunks in per_track:
        h = hashlib.sha256()
        for c in chunks:
            h.update(view[c.offset:c.end])
        out.append(h.hexdigest())
    return out


# Strings that must not appear outside `mdat` in a scrubbed file. Inside `mdat` they
# may occur by chance in coded video, so the scan is restricted to the container.
_MARKERS = ((b"com.apple.quicktime", "QuickTime metadata key"),
            (b"\xa9xyz", "location atom"), (b"loci", "3GPP location box"),
            (b"\xa9too", "encoder atom"),
            (b"Core Media", "Apple muxer handler name"))
_ISO6709 = re.compile(rb"[+-]\d{2}\.\d{3,}[+-]\d{3}\.\d{3,}")


def residuals(data: bytes) -> list[str]:
    """Re-walk the output. Anything left of what F1 removes is a leak."""
    out: list[str] = []
    tops, moov_hdr, mdat_hdr = _layout(data)
    for b in tops:
        if b.type not in KEEP_TOP:
            out.append(f"top-level {b.type.decode('latin-1')} box survived")
    moov = iso.parse(data[moov_hdr.offset:moov_hdr.end])[0]
    for box in moov.walk():
        if box.type in DROP_IN_MOOV:
            out.append(f"{box.type.decode('latin-1')} box survived in moov")
        elif box.type in iso.TIMESTAMP_BOXES:
            width = 8 if box.payload[0] == 1 else 4
            if box.payload[4:4 + 2 * width].strip(b"\x00"):
                out.append(f"{box.type.decode()} timestamps survived")
        elif box.type == b"hdlr" and box.payload[12:].strip(b"\x00"):
            out.append("a handler name or manufacturer survived")
    tracks = _tracks(moov)
    for t in tracks:
        if t.handler not in KEEP_HANDLERS:
            out.append(f"a {t.handler.decode('latin-1')} track survived")

    # The locus this tier exists for: bytes in mdat that no kept table reaches.
    lo, hi = mdat_hdr.offset + mdat_hdr.header_len, mdat_hdr.end
    spans = sorted((c.offset, c.end) for t in tracks
                   for c in iso.chunks(t.box.find(b"mdia/minf/stbl"),
                                       limit=len(data))
                   if c.sample_sizes)
    cursor, gap = lo, 0
    for a, b in spans:
        gap += max(0, a - cursor)
        cursor = max(cursor, b)
    gap += max(0, hi - cursor)
    if gap:
        out.append(f"{gap} bytes of mdat are referenced by no sample table")

    container = data[:mdat_hdr.offset] + data[mdat_hdr.end:]
    for needle, label in _MARKERS:
        if needle in container:
            out.append(f"{label} present outside mdat")
    if _ISO6709.search(container):
        out.append("an ISO 6709 coordinate is present outside mdat")
    return out
