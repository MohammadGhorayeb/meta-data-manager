"""MP4 corpus for Phase 4.

Two sources, and the split is the one HEIC's M5 argued for the hard way:

- **Hand-built**, here, byte by byte. It imports nothing from `src/`, so a shared
  misunderstanding of the box layout cannot cancel itself out between the thing
  being tested and the thing testing it — the same rule `heic_corpus.handbuilt()`
  follows, and a test asserts the no-import property rather than trusting this
  docstring. It is also the only way to produce the files the refusal list exists
  for: no encoder will emit a fragmented-and-also-progressive file, an encrypted
  sample entry, or a chunk offset pointing outside `mdat` on request.
- **ffmpeg**, for files a real muxer wrote. ffmpeg is installed on the CI runner
  (see `.github/workflows/ci.yml`), so this runs there too; it is still guarded,
  because a developer machine without it should skip rather than fail.

**What the hand-built file is not.** It is structurally complete — ffprobe reads
both tracks and their codec tags, and ExifTool reads its GPS and its tag list — but
its sample entries carry no codec configuration (`avcC`, `esds`), so ffprobe also
says `missing mandatory atoms, broken header` and nothing will decode it. That is
the right trade for M2, which tests container structure and the refusal list; it is
the wrong trade for M3, whose acceptance test must **decode** rather than parse. M3
uses `ffmpeg_corpus()` for that, and this note exists so nobody later mistakes a
green M2 for evidence the media survives.

Deliberately absent: real phone video. Phase 4 M1 put it out of scope, so an
iPhone `.MOV`'s `mebx` timed-metadata track is untested scope rather than measured
absent — and `walker.walk()` refuses such a file by name instead of scrubbing
around it (`docs/p4_media_plan.md` §8).
"""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
import tempfile

HAVE_FFMPEG = shutil.which("ffmpeg") is not None

# A recognisable byte pattern for "media", so a test can assert the samples came
# through untouched without needing a decoder.
SENTINEL = b"MP4-SENTINEL-MEDIA-"


# --------------------------------------------------------------------------- #
# Hand-built: the box writer
# --------------------------------------------------------------------------- #
def box(kind: bytes, payload: bytes) -> bytes:
    """A plain box: size(4) + type(4) + payload."""
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def full_box(kind: bytes, version: int, flags: int, payload: bytes) -> bytes:
    """A full box: the version/flags word precedes the payload."""
    return box(kind, struct.pack(">B", version) + struct.pack(">I", flags)[1:]
               + payload)


def ftyp(major: bytes = b"isom", compatible: tuple[bytes, ...] = (b"isom", b"mp42")
         ) -> bytes:
    return box(b"ftyp", major + struct.pack(">I", 512) + b"".join(compatible))


def mvhd(creation: int = 0, modification: int = 0, next_track: int = 2,
         version: int = 0) -> bytes:
    """The movie header. Version decides the WIDTH of the time fields, which is
    the trap `walker._header_times` reads rather than assumes: a version-1 box read
    as version 0 does not fail, it silently returns half a timestamp."""
    if version == 0:
        times = struct.pack(">IIII", creation, modification, 1000, 2000)
    else:
        times = struct.pack(">QQIQ", creation, modification, 1000, 2000)
    rest = (struct.pack(">i", 0x00010000)          # rate
            + struct.pack(">h", 0x0100)            # volume
            + b"\x00" * 10                         # reserved
            + struct.pack(">9i", 0x00010000, 0, 0, 0, 0x00010000, 0, 0, 0,
                          0x40000000)              # matrix
            + b"\x00" * 24                         # predefined
            + struct.pack(">I", next_track))
    return full_box(b"mvhd", version, 0, times + rest)


def tkhd(track_id: int = 1, creation: int = 0, modification: int = 0,
         version: int = 0) -> bytes:
    if version == 0:
        head = struct.pack(">IIIII", creation, modification, track_id, 0, 2000)
    else:
        head = struct.pack(">QQIIQ", creation, modification, track_id, 0, 2000)
    rest = (b"\x00" * 8                            # reserved
            + struct.pack(">hhhh", 0, 0, 0, 0)     # layer, group, volume, reserved
            + struct.pack(">9i", 0x00010000, 0, 0, 0, 0x00010000, 0, 0, 0,
                          0x40000000)              # matrix
            + struct.pack(">II", 160 << 16, 120 << 16))
    return full_box(b"tkhd", version, 3, head + rest)


def mdhd() -> bytes:
    return full_box(b"mdhd", 0, 0,
                    struct.pack(">IIII", 0, 0, 1000, 2000)
                    + struct.pack(">HH", 0x55C4, 0))   # language `und`


def hdlr(handler: bytes = b"vide", name: str = "VideoHandler",
         manufacturer: bytes = b"\x00\x00\x00\x00") -> bytes:
    """A handler box. `name` is the field that, measured in M0, is where
    AVFoundation writes `Core Media Video` — a producer fingerprint in a field no
    player needs.

    `manufacturer` matters more than it looks. A metadata handler must carry
    `mdir` + `appl` for a reader to treat the `ilst` beside it as iTunes-style
    tags: with zeroes there, ExifTool walks straight past the tags and reports
    nothing. That would have made every "the tag is gone" assertion pass on a file
    whose tags were never readable in the first place — the DOCX `w:rsid`-in-
    compressed-bytes mistake, in a different container.
    """
    return full_box(b"hdlr", 0, 0,
                    b"\x00" * 4 + handler + manufacturer + b"\x00" * 8
                    + name.encode("utf-8") + b"\x00")


def stsd(fmt: bytes = b"avc1") -> bytes:
    entry = box(fmt, b"\x00" * 6 + struct.pack(">H", 1) + b"\x00" * 70)
    return full_box(b"stsd", 0, 0, struct.pack(">I", 1) + entry)


def stco(offsets: tuple[int, ...]) -> bytes:
    return full_box(b"stco", 0, 0,
                    struct.pack(">I", len(offsets))
                    + b"".join(struct.pack(">I", o) for o in offsets))


def co64(offsets: tuple[int, ...]) -> bytes:
    """The 64-bit chunk table. Legal, handled by `isobmff.shift_chunk_offsets()`,
    and until now never exercised by an actual file in this suite — the tests only
    asserted that *some* table was patched. Built here so it is tested rather than
    assumed."""
    return full_box(b"co64", 0, 0,
                    struct.pack(">I", len(offsets))
                    + b"".join(struct.pack(">Q", o) for o in offsets))


def stbl(offsets: tuple[int, ...], fmt: bytes = b"avc1",
         table: str = "stco") -> bytes:
    offset_box = co64(offsets) if table == "co64" else stco(offsets)
    return box(b"stbl", stsd(fmt)
               + full_box(b"stts", 0, 0, struct.pack(">I", 0))
               + full_box(b"stsc", 0, 0, struct.pack(">I", 0))
               + full_box(b"stsz", 0, 0, struct.pack(">II", 0, 0))
               + offset_box)


def media_header(handler: bytes) -> bytes:
    """The per-kind media information header. `minf` is required to carry one, and
    without it ffprobe reports "missing mandatory atoms, broken header" -- an
    independent reader that will not validate the fixture is not validating it."""
    if handler == b"vide":
        return full_box(b"vmhd", 0, 1, struct.pack(">HHHH", 0, 0, 0, 0))
    if handler == b"soun":
        return full_box(b"smhd", 0, 0, struct.pack(">hH", 0, 0))
    return b""


def trak(track_id: int = 1, handler: bytes = b"vide",
         name: str = "VideoHandler", offsets: tuple[int, ...] = (),
         creation: int = 0, modification: int = 0, fmt: bytes = b"avc1",
         table: str = "stco", tkhd_version: int = 0,
         extra_minf: bytes = b"") -> bytes:
    header = extra_minf if extra_minf else media_header(handler)
    media_info = box(b"minf", header + box(b"dinf", b"") + stbl(offsets, fmt, table))
    return box(b"trak",
               tkhd(track_id, creation, modification, tkhd_version)
               + box(b"mdia", mdhd() + hdlr(handler, name) + media_info))


def udta(gps: bool = True, tags: bool = True) -> bytes:
    """User data: the two metadata containers M0 found the same coordinate in.

    `loci` is the QuickTime location box — binary, and where ffmpeg puts GPS
    whatever the muxer flags say. `meta`/`ilst` is the iTunes-style tag list.
    """
    parts = b""
    if tags:
        ilst = box(b"ilst",
                   box(b"\xa9too", box(b"data", struct.pack(">II", 1, 0)
                                       + b"Lavf62.12.101"))
                   + box(b"\xa9nam", box(b"data", struct.pack(">II", 1, 0)
                                         + b"Ground truth")))
        parts += full_box(b"meta", 0, 0,
                          hdlr(b"mdir", "", manufacturer=b"appl") + ilst)
    if gps:
        # language(2), name(\0-terminated), role(1), then LONGITUDE, LATITUDE and
        # altitude as 16.16 fixed point, then the astronomical body and a notes
        # string. The coordinate order is longitude-first, which is the reverse of
        # how it is spoken, and the trailing notes terminator is not optional --
        # getting either wrong yields a box ExifTool declines to read, so the GPS
        # would be invisible to the very check meant to prove it was removed.
        parts += full_box(b"loci", 0, 0,
                          struct.pack(">H", 0x15C7)      # language: und
                          + b"\x00"                      # name: empty
                          + b"\x00"                      # role: shooting location
                          + struct.pack(">iii", -74 << 16, 40 << 16, 0)
                          + b"earth\x00"                 # astronomical body
                          + b"\x00")                     # additional notes
    return box(b"udta", parts)


def large_mdat(payload: bytes) -> bytes:
    """`mdat` written in the 64-bit largesize form: size word 1, then a 64-bit
    length, for a 16-byte header instead of 8.

    Legal for any size, and **AVFoundation writes it for a 17 KB box**. It is here
    because reconstructing that header instead of reading it is a bug that hides
    perfectly: the media bytes survive, the container parses, the duration is
    right, and every chunk offset is 8 bytes off, so the file decodes to noise.
    M4A F1 shipped with it from Phase 2 until an Apple-muxed file was decoded.
    """
    return (struct.pack(">I", 1) + b"mdat"
            + struct.pack(">Q", 16 + len(payload)) + payload)


def keys_meta(entries: tuple[tuple[str, str], ...] = (
        ("location", "+40.7128-074.0060/"), ("make", "TestCorp"))) -> bytes:
    """A `meta` box in the **Keys** (`mdta`) namespace.

    The shape that makes this format's tag list different from every other one in
    the project: an `ilst` child's four "type" bytes are not a fourcc, they are a
    big-endian **1-based index into the `keys` box**, which holds the names in a
    parallel list. A reader matching fourcc names finds nothing here at all — and
    dropping one `keys` entry renumbers every entry after it, silently relabelling
    the surviving values. So the two are dropped or kept as a pair.
    """
    key_list = b"".join(
        struct.pack(">I", 8 + len(name)) + b"mdta" + name.encode()
        for name, _ in entries)
    keys = full_box(b"keys", 0, 0,
                    struct.pack(">I", len(entries)) + key_list)
    ilst = box(b"ilst", b"".join(
        box(struct.pack(">I", i + 1),
            box(b"data", struct.pack(">II", 1, 0) + value.encode()))
        for i, (_, value) in enumerate(entries)))
    return full_box(b"meta", 0, 0,
                    hdlr(b"mdta", "", manufacturer=b"\x00" * 4) + keys + ilst)


def handbuilt(*, media: bytes = None, moov_first: bool = False,
              free_before_mdat: bool = True, gps: bool = True, tags: bool = True,
              tracks: tuple[dict, ...] = None, mvhd_creation: int = 0,
              mvhd_version: int = 0, brands: tuple[bytes, ...] = (b"isom", b"mp42"),
              major: bytes = b"isom", extra_top: bytes = b"",
              table: str = "stco", chunk_offset_delta: int = 0,
              largesize_mdat: bool = False) -> bytes:
    """A complete, structurally valid MP4, assembled from the pieces above.

    `moov_first` selects the `+faststart` layout, which is the case where removing
    anything shrinks the region before `mdat` and every chunk offset has to move.
    """
    media = media or (SENTINEL + b"\x00" * 200)
    tracks = tracks if tracks is not None else (
        {"track_id": 1, "handler": b"vide", "name": "VideoHandler"},
        {"track_id": 2, "handler": b"soun", "name": "SoundHandler",
         "fmt": b"mp4a"},
    )

    head = ftyp(major, brands)
    pad = box(b"free", b"") if free_before_mdat else b""

    # Chunk offsets are absolute, so the layout has to be laid out before the
    # tables that point into it can be written. Build moov twice: once to learn
    # its size, once with the real offsets.
    header_len = 16 if largesize_mdat else 8

    def build(mdat_at: int) -> tuple[bytes, bytes]:
        chunk_at = mdat_at + header_len + chunk_offset_delta
        traks = b"".join(
            trak(offsets=(chunk_at,), table=table, **t) for t in tracks)
        movie = box(b"moov", mvhd(mvhd_creation, mvhd_creation,
                                  len(tracks) + 1, mvhd_version)
                    + traks + udta(gps, tags))
        media_box = (large_mdat(media) if largesize_mdat
                     else box(b"mdat", media))
        return movie, media_box

    if moov_first:
        movie, _ = build(0)                       # size probe
        mdat_at = len(head) + len(movie) + len(pad) + len(extra_top)
        movie, media_box = build(mdat_at)
        return head + movie + pad + extra_top + media_box
    mdat_at = len(head) + len(pad) + len(extra_top)
    movie, media_box = build(mdat_at)
    return head + pad + extra_top + media_box + movie


# --------------------------------------------------------------------------- #
# ffmpeg: files a real muxer wrote
# --------------------------------------------------------------------------- #
def ffmpeg_sample(path: str, *, faststart: bool = False, gps: bool = False,
                  duration: float = 1.0, audio: bool = True) -> str:
    """Encode a tiny deterministic clip. Returns `path`, or raises if ffmpeg fails."""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-f", "lavfi", "-i", f"testsrc2=size=160x120:rate=15:duration={duration}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac", "-shortest"]
    if gps:
        cmd += ["-metadata", "location=+40.7128-074.0060/",
                "-metadata", "title=Ground truth"]
    if faststart:
        cmd += ["-movflags", "+faststart"]
    cmd.append(path)
    subprocess.run(cmd, check=True, capture_output=True)
    return path


def ffmpeg_corpus(tmpdir: str) -> dict[str, str]:
    """The producer set M0 measured, minus the macOS-only AVFoundation re-mux."""
    out = {}
    for name, kw in (("plain", {}),
                     ("faststart", {"faststart": True}),
                     ("tagged", {"gps": True}),
                     ("video_only", {"audio": False})):
        out[name] = ffmpeg_sample(os.path.join(tmpdir, f"{name}.mp4"), **kw)
    return out


def temp_mp4(data: bytes) -> str:
    fd, path = tempfile.mkstemp(suffix=".mp4")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return path
