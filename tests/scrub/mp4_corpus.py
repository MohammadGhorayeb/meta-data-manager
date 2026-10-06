"""P4 M11 — a hand-built QuickTime movie carrying every locus M7 measured.

Real iPhone videos are personal files that never enter the repository, so CI needs a
file that can express what they do. This one is written **byte by byte** and imports
nothing from `src.scrub` (a test walks the imports to hold that), so a shared
misreading of `stsc` or `stco` cannot cancel itself out between corpus and scrubber.

ffmpeg is used only as an ENCODER: it makes a real, decodable video and audio stream,
and this module then lifts the coded samples out with its own reader and writes a new
container around them, which is what lets the acceptance test decode rather than parse.

What the container carries, and the path each covers:

| structure                                      | the path it covers              |
|------------------------------------------------|---------------------------------|
| brand `qt  `, `wide` after `ftyp`              | QuickTime identification        |
| QuickTime-dialect `moov/meta` (`mdta` keys)    | M8 walker; GPS, make, model, date |
| `trak/meta` on the video track                 | the lens model                  |
| a timed-metadata track (`meta`, `mebx`)        | per-frame faces; samples IN mdat |
| `tref` on the audio track naming it            | pruning a kept track's reference |
| a stale-metadata blob between chunks of `mdat` | THE locus: bytes no table reaches |
| zero padding at the start of `mdat`            | Apple's opening gap             |
| a trailing top-level `free` with GPS           | the short-clip locus            |
| `udta/©xyz`, a `uuid` box                      | the ISO/iTunes-style stores     |
| stamped `mvhd`/`tkhd`/`mdhd` times             | the structural timestamps       |
| Apple handler names + manufacturer `appl`      | the muxer's own name            |
| several samples per chunk, uneven final chunk  | a multi-run `stsc`              |
| `moov_first`, `co64` variants                  | offsets that depend on moov size; 64-bit tables |
"""
from __future__ import annotations

import os
import shutil
import struct
import subprocess

HAVE_FFMPEG = shutil.which("ffmpeg") is not None

GPS = b"+12.3456+065.4321+007.000/"          # ISO 6709, planted; never a real place
MAKE, MODEL = b"MAKE-SENTINEL", b"MODEL-SENTINEL"
LENS = b"LENS-SENTINEL back camera"
FACE_KEY = b"com.apple.quicktime.detected-face.bounds"
FACE_SAMPLE = b"FACE-SENTINEL"
STALE = b"STALE-SENTINEL"
FREE_TAIL = b"FREE-TAIL-SENTINEL"
UDTA = b"UDTA-SENTINEL"
XMP = b"XMP-SENTINEL"
STAMP = 0xE0000000                          # a nonzero QuickTime-epoch time
SECRETS = (GPS, MAKE, MODEL, LENS, FACE_KEY, FACE_SAMPLE, STALE, FREE_TAIL, UDTA,
           XMP, b"Core Media", b"appl")

REAL_DIR = os.environ.get(
    "MP4_REAL_SAMPLES",
    os.path.join(os.path.dirname(__file__), "..", "..", "metadata-research", "step2",
                 "video"))


def real_samples() -> list[str]:
    """Real videos on this machine (symlinks, git-ignored), or []. Never bundled."""
    if os.environ.get("SCRUB_IGNORE_REAL_SAMPLES"):
        return []                       # the published test count (test_readme_claims)
    if not os.path.isdir(REAL_DIR):
        return []
    return sorted(os.path.join(REAL_DIR, f) for f in os.listdir(REAL_DIR)
                  if f.lower().endswith((".mov", ".mp4")))


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #
def box(btype: bytes, body: bytes = b"") -> bytes:
    return struct.pack(">I", 8 + len(body)) + btype + body


def _qt_hdlr(component: bytes, handler: bytes, name: bytes) -> bytes:
    """QuickTime form: component type, subtype, manufacturer `appl`, Pascal name."""
    return box(b"hdlr", b"\x00" * 4 + component + handler + b"appl" + b"\x00" * 8
               + bytes([len(name)]) + name)


def _mdta_meta(pairs: list[tuple[bytes, bytes]]) -> bytes:
    """A QuickTime-dialect `meta`: NO version/flags, children at once."""
    keys = b"\x00" * 4 + struct.pack(">I", len(pairs))
    ilst = b""
    for i, (name, value) in enumerate(pairs, start=1):
        keys += struct.pack(">I", 8 + len(name)) + b"mdta" + name
        ilst += box(struct.pack(">I", i), box(b"data", struct.pack(">II", 1, 0) + value))
    return box(b"meta", box(b"hdlr", b"\x00" * 8 + b"mdta" + b"\x00" * 12 + b"\x00")
               + box(b"keys", keys) + box(b"ilst", ilst))


def _stamp(payload: bytes) -> bytes:
    width = 8 if payload[0] == 1 else 4
    return payload[:4] + STAMP.to_bytes(width, "big") * 2 + payload[4 + 2 * width:]


def _stsc(per_chunk: list[int]) -> bytes:
    runs, prev = [], None
    for i, n in enumerate(per_chunk, start=1):
        if n != prev:
            runs.append((i, n))
            prev = n
    return box(b"stsc", b"\x00" * 4 + struct.pack(">I", len(runs))
               + b"".join(struct.pack(">III", f, n, 1) for f, n in runs))


def _offsets(values: list[int], co64: bool) -> bytes:
    if co64:
        return box(b"co64", b"\x00" * 4 + struct.pack(">I", len(values))
                   + b"".join(struct.pack(">Q", v) for v in values))
    return box(b"stco", b"\x00" * 4 + struct.pack(">I", len(values))
               + b"".join(struct.pack(">I", v) for v in values))


def _group(samples: list[bytes], size: int) -> list[list[bytes]]:
    return [samples[i:i + size] for i in range(0, len(samples), size)]


def build(path: str, *, moov_first: bool = False, co64: bool = False,
          src: str | None = None) -> str:
    """Write the torture movie to `path` and return it."""
    src = src or _encode(os.path.join(os.path.dirname(path), "_src.mp4"))
    tracks = read_tracks(open(src, "rb").read())
    video = next(t for t in tracks.values() if t["handler"] == b"vide")
    audio = next(t for t in tracks.values() if t["handler"] == b"soun")

    # Chunking: 3 video samples per chunk, 7 audio, both leaving an uneven last
    # chunk, so `stsc` needs more than one run.
    vchunks, achunks = _group(video["samples"], 3), _group(audio["samples"], 7)
    face = [FACE_SAMPLE + b"-%02d" % i + b"\x00" * 20 for i in range(2)]
    mchunks = [[face[0]], [face[1]]]

    # mdat layout: Apple-style zero lead, then interleave v/a/m, with the stale
    # copy wedged between the first video and first audio chunk.
    stale = (b"\x00\x00\x00\x40traf" + STALE + b"keys" + b"mdta" + GPS
             + b"com.apple.quicktime.model" + MODEL + b"\x00" * 40)
    pieces: list[tuple[str, int, bytes]] = [("pad", -1, b"\x00" * 64)]
    for i in range(max(len(vchunks), len(achunks))):
        if i < len(vchunks):
            pieces.append(("v", i, b"".join(vchunks[i])))
        if i == 0:
            pieces.append(("stale", -1, stale))
        if i < len(achunks):
            pieces.append(("a", i, b"".join(achunks[i])))
        if i < len(mchunks):
            pieces.append(("m", i, b"".join(mchunks[i])))
    mdat_payload = b"".join(p[2] for p in pieces)

    ftyp = box(b"ftyp", b"qt  " + b"\x00" * 4 + b"qt  ")
    wide = box(b"wide")

    def moov_bytes(data_start: int) -> bytes:
        offs = {"v": [], "a": [], "m": []}
        at = data_start
        for kind, _, blob in pieces:
            if kind in offs:
                offs[kind].append(at)
            at += len(blob)

        def stbl(t, chunk_list, kind):
            return box(b"stbl", t["stsd"] + t["stts"] + t.get("stss", b"")
                       + t.get("ctts", b"") + _stsc([len(c) for c in chunk_list])
                       + t["stsz"] + _offsets(offs[kind], co64))

        dinf = box(b"dinf", box(b"dref", b"\x00" * 4 + struct.pack(">I", 1)
                                + box(b"alis", b"\x00\x00\x00\x01")))
        data_hdlr = _qt_hdlr(b"dhlr", b"alis", b"Core Media Data Handler")

        vtrak = box(b"trak", box(b"tkhd", _stamp(video["tkhd"]))
                    + _mdta_meta([(b"com.apple.quicktime.camera.lens_model", LENS)])
                    + box(b"mdia", box(b"mdhd", _stamp(video["mdhd"]))
                          + _qt_hdlr(b"mhlr", b"vide", b"Core Media Video")
                          + box(b"minf", video["xmhd"] + data_hdlr + dinf
                                + stbl(video, vchunks, "v"))))
        atrak = box(b"trak", box(b"tkhd", _stamp(audio["tkhd"]))
                    # One reference to a KEPT track, one to the dropped one.
                    + box(b"tref", box(b"sync", struct.pack(">I", video["id"]))
                          + box(b"cdep", struct.pack(">I", 3)))
                    + box(b"mdia", box(b"mdhd", _stamp(audio["mdhd"]))
                          + _qt_hdlr(b"mhlr", b"soun", b"Core Media Audio")
                          + box(b"minf", audio["xmhd"] + data_hdlr + dinf
                                + stbl(audio, achunks, "a"))))

        key = box(struct.pack(">I", 1), box(b"keyd", b"mdta" + FACE_KEY)
                  + box(b"dtyp", struct.pack(">II", 0, 78)))
        mebx = box(b"mebx", b"\x00" * 6 + struct.pack(">H", 1) + box(b"keys", key))
        mtrak_tables = {
            "stsd": box(b"stsd", b"\x00" * 4 + struct.pack(">I", 1) + mebx),
            "stts": box(b"stts", b"\x00" * 4 + struct.pack(">III", 1, 2, 1000)),
            "stsz": box(b"stsz", b"\x00" * 4 + struct.pack(">II", 0, 2)
                        + b"".join(struct.pack(">I", len(f)) for f in face)),
        }
        tkhd = bytearray(_stamp(video["tkhd"]))
        width = 8 if tkhd[0] == 1 else 4
        struct.pack_into(">I", tkhd, 4 + 2 * width, 3)          # track_ID 3
        mtrak = box(b"trak", box(b"tkhd", bytes(tkhd))
                    + box(b"tref", box(b"cdsc", struct.pack(">I", video["id"])))
                    + box(b"mdia", box(b"mdhd", _stamp(
                        b"\x00" * 4 + b"\x00" * 8 + struct.pack(">II", 1000, 2000)
                        + b"\x55\xc4\x00\x00"))
                        + _qt_hdlr(b"mhlr", b"meta", b"Core Media Metadata")
                        + box(b"minf", box(b"gmhd", box(b"gmin", b"\x00" * 16))
                              + data_hdlr + dinf + stbl(mtrak_tables, mchunks, "m"))))

        mvhd = bytearray(_stamp(movie_header(open(src, "rb").read())))
        struct.pack_into(">I", mvhd, len(mvhd) - 4, 4)          # next_track_ID
        return box(b"moov", box(b"mvhd", bytes(mvhd)) + vtrak + atrak + mtrak
                   + _mdta_meta([
                       (b"com.apple.quicktime.location.ISO6709", GPS),
                       (b"com.apple.quicktime.make", MAKE),
                       (b"com.apple.quicktime.model", MODEL),
                       (b"com.apple.quicktime.creationdate", b"2026-01-01T00:00:00+0300"),
                   ])
                   + box(b"udta", box(b"\xa9xyz", struct.pack(">HH", len(UDTA), 0)
                                      + UDTA))
                   + box(b"uuid", b"\xbe\x7a\xcf\xcb\x97\xa9\x42\xe8\x9c\x71\x99\x94"
                         b"\x91\xe3\xaf\xac" + XMP))

    mdat_header = 8
    tail = box(b"free", b"keys" + GPS + FREE_TAIL + MODEL)
    if moov_first:
        probe = moov_bytes(0)
        start = len(ftyp) + len(wide) + len(probe) + mdat_header
        moov = moov_bytes(start)
        assert len(moov) == len(probe)
        out = ftyp + wide + moov + box(b"mdat", mdat_payload) + tail
    else:
        start = len(ftyp) + len(wide) + mdat_header
        out = ftyp + wide + box(b"mdat", mdat_payload) + moov_bytes(start) + tail
    with open(path, "wb") as f:
        f.write(out)
    return path


def have_encoder(name: str) -> bool:
    if not HAVE_FFMPEG:
        return False
    r = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True,
                       text=True)
    return f" {name} " in r.stdout


def _encode(path: str, video: str = "mpeg4") -> str:
    """Two seconds of real video and audio. The native `mpeg4` encoder writes no
    settings string of its own into the stream. `libx264` does -- a user-data SEI
    naming its version and every option, which is what WhatsApp's transcoder leaves
    in every file it sends (p4 plan §5.3), and which F1 keeps by definition."""
    if not os.path.exists(path):
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-f", "lavfi", "-i", "testsrc=size=96x64:rate=10:duration=2",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=2:sample_rate=44100",
             "-c:v", video, "-g", "5", "-c:a", "aac", "-b:a", "64k",
             "-fflags", "+bitexact", "-map_metadata", "-1", path],
            check=True, capture_output=True)
    return path


# --------------------------------------------------------------------------- #
# Reading — our own, deliberately not the scrubber's
# --------------------------------------------------------------------------- #
_CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts", b"dinf"}


def _boxes(data: bytes, start: int = 0, end: int | None = None):
    end = len(data) if end is None else end
    pos = start
    while pos + 8 <= end:
        size, btype = struct.unpack(">I4s", data[pos:pos + 8])
        header = 8
        if size == 1:
            size, header = struct.unpack(">Q", data[pos + 8:pos + 16])[0], 16
        elif size == 0:
            size = end - pos
        yield btype, pos, pos + header, pos + size
        pos += size


def _child(data: bytes, start: int, end: int, *path: bytes):
    for p in path:
        hit = next(((s, e) for t, _, s, e in _boxes(data, start, end) if t == p), None)
        if hit is None:
            return None
        start, end = hit
    return start, end


def read_tracks(data: bytes) -> dict:
    """Every track's sample bytes, resolved from ITS tables with THIS reader, plus
    the raw table boxes `build()` copies from an ffmpeg-made source."""
    moov = next((s, e) for t, _, s, e in _boxes(data) if t == b"moov")
    out: dict = {}
    for t, _, s, e in _boxes(data, *moov):
        if t != b"trak":
            continue
        tk = _child(data, s, e, b"tkhd")
        tkhd = data[tk[0]:tk[1]]
        track_id = struct.unpack(">I", tkhd[4 + (16 if tkhd[0] == 1 else 8):][:4])[0]
        hd = _child(data, s, e, b"mdia", b"hdlr")
        md = _child(data, s, e, b"mdia", b"mdhd")
        minf = _child(data, s, e, b"mdia", b"minf")
        stbl = _child(data, *minf, b"stbl")
        raw = {bt: data[p:q] for bt, p, _, q in _boxes(data, *stbl)}
        xmhd = next((data[p:q] for bt, p, _, q in _boxes(data, *minf)
                     if bt in (b"vmhd", b"smhd", b"gmhd")), b"")
        rec = {"id": track_id, "handler": data[hd[0] + 8:hd[0] + 12], "tkhd": tkhd,
               "mdhd": data[md[0]:md[1]], "xmhd": xmhd, **{
                   k.decode(): v for k, v in raw.items()}}
        rec["samples"] = _samples(data, raw)
        out[track_id] = rec
    return out


def movie_header(data: bytes) -> bytes:
    moov = next((s, e) for t, _, s, e in _boxes(data) if t == b"moov")
    hit = _child(data, *moov, b"mvhd")
    return data[hit[0]:hit[1]]


def _samples(data: bytes, raw: dict) -> list[bytes]:
    stsz = raw[b"stsz"][8:]
    fixed, n = struct.unpack(">II", stsz[4:12])
    sizes = [fixed] * n if fixed else list(struct.unpack(f">{n}I", stsz[12:12 + 4 * n]))
    if b"stco" in raw:
        p = raw[b"stco"][8:]
        k = struct.unpack(">I", p[4:8])[0]
        offs = list(struct.unpack(f">{k}I", p[8:8 + 4 * k]))
    else:
        p = raw[b"co64"][8:]
        k = struct.unpack(">I", p[4:8])[0]
        offs = list(struct.unpack(f">{k}Q", p[8:8 + 8 * k]))
    p = raw[b"stsc"][8:]
    runs = [struct.unpack(">III", p[8 + 12 * i:20 + 12 * i])
            for i in range(struct.unpack(">I", p[4:8])[0])]
    out, s = [], 0
    for ci, off in enumerate(offs, start=1):
        per = [r[1] for r in runs if r[0] <= ci][-1]
        for _ in range(per):
            out.append(data[off:off + sizes[s]])
            off += sizes[s]
            s += 1
    assert s == n, f"resolved {s} of {n} samples"
    return out


def unreferenced(data: bytes) -> int:
    """Bytes of mdat no track points at, computed with this module's reader."""
    mdat = next((hs, e) for t, _, hs, e in _boxes(data) if t == b"mdat")
    spans = []
    for rec in read_tracks(data).values():
        raw = {k.encode(): v for k, v in rec.items() if isinstance(v, bytes)}
        pos_list = _sample_positions(data, raw)
        spans += pos_list
    cursor, gap = mdat[0], 0
    for a, b in sorted(spans):
        gap += max(0, a - cursor)
        cursor = max(cursor, b)
    return gap + max(0, mdat[1] - cursor)


def _sample_positions(data: bytes, raw: dict) -> list[tuple[int, int]]:
    stsz = raw[b"stsz"][8:]
    fixed, n = struct.unpack(">II", stsz[4:12])
    sizes = [fixed] * n if fixed else list(struct.unpack(f">{n}I", stsz[12:12 + 4 * n]))
    key = b"stco" if b"stco" in raw else b"co64"
    p = raw[key][8:]
    k = struct.unpack(">I", p[4:8])[0]
    offs = list(struct.unpack(f">{k}{'I' if key == b'stco' else 'Q'}",
                              p[8:8 + (4 if key == b"stco" else 8) * k]))
    p = raw[b"stsc"][8:]
    runs = [struct.unpack(">III", p[8 + 12 * i:20 + 12 * i])
            for i in range(struct.unpack(">I", p[4:8])[0])]
    out, s = [], 0
    for ci, off in enumerate(offs, start=1):
        per = [r[1] for r in runs if r[0] <= ci][-1]
        for _ in range(per):
            out.append((off, off + sizes[s]))
            off += sizes[s]
            s += 1
    return out


def decoded(path: str, seconds: float | None = None) -> str:
    """Frame-by-frame digests of the video and first audio stream, through ffmpeg:
    an implementation that shares no code with ours. `seconds` bounds the decode for
    the local real-video tests; a 10,000-frame screen recording took five minutes
    twice over, and the scrubber's own per-sample check covers the whole file."""
    limit = ["-t", str(seconds)] if seconds else []
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", path, *limit, "-map", "0:v:0",
                        "-map", "0:a:0?", "-f", "framemd5", "-"],
                       capture_output=True, text=True, check=True)
    lines = [ln for ln in r.stdout.splitlines() if not ln.startswith("#")]
    assert lines, "ffmpeg decoded nothing"
    return "\n".join(lines)
