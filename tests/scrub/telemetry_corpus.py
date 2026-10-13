"""Video telemetry corpus: where cameras that are not iPhones put their position.

The iPhone stores GPS in metadata boxes (Phase 4, M7). Action cameras, drones and
360 cameras store it as **media** -- samples of a track, timed like video frames --
and Android phones in boxes of their own:

- `gopro`: a GPMF telemetry track (handler `meta`, sample entry `gpmd`, named
  `GoPro MET`) whose samples hold `GPS5` coordinates; a timecode track (`tmcd`)
  whose one sample is the time of day the recording started; `udta/GPMF` with the
  camera's serial (`CASN`); the `GoPro SOS` track (`fdsc`);
- `drone`: a subtitle track (handler `sbtl`, `tx3g`) carrying the drone's home and
  current position on every frame, in the text format Autel's drones write (the
  format ExifTool's source documents and parses; Yuneec's is similar);
- `android`: `udta/©xyz` (an ISO 6709 location string), Android's `mdta` keys
  (`com.android.version`, model) and a Samsung `sefd` box after `mdat`;
- `camm`: Google's camera-motion track (handler `camm`) with GPS records, as 360
  cameras write for Street View.

Built from `mp4_iso_corpus`'s boxes, one payload per track, following each format's
public description; ExifTool's embedded-data mode (`-ee`) reads every shape's
position back, which is the check that the models are real enough to test against.
Planted values derive from `variant`.
"""
from __future__ import annotations

import os
import struct

from tests.scrub import mp4_iso_corpus as c

SHAPES = ("gopro", "drone", "android", "camm")
ENCODER_NAME = b"GoPro AVC encoder"     # measured in all ten of GoPro's samples
GOPRO_DIR = os.path.expanduser("~/metadata-research/step2/video/gopro")


def gopro_samples() -> list[str]:
    """GoPro's own published sample clips (github.com/gopro/gpmf-parser, downloaded
    to the research folder, never into the repo), or []."""
    if os.environ.get("SCRUB_IGNORE_REAL_SAMPLES") or not os.path.isdir(GOPRO_DIR):
        return []
    return sorted(os.path.join(GOPRO_DIR, f) for f in os.listdir(GOPRO_DIR)
                  if f.endswith(".mp4"))


def coords(variant: int = 0) -> tuple[float, float]:
    return 40.7128 + variant * 0.001, -74.0060 - variant * 0.001


def secret(name: str, variant: int = 0) -> str:
    return f"SENTINEL-{name}-V{variant:02d}"


def _klv(key: bytes, kind: bytes, size: int, count: int, data: bytes) -> bytes:
    pad = (-len(data)) % 4
    return key + kind + bytes([size]) + struct.pack(">H", count) + data + b"\x00" * pad


def gpmf_gps(variant: int = 0, n: int = 4) -> bytes:
    """A GPMF device stream with GPS5 samples (lat, lon, alt, 2D and 3D speed as
    int32, divided by SCAL)."""
    lat, lon = coords(variant)
    scal = struct.pack(">iiiii", 10_000_000, 10_000_000, 1000, 1000, 100)
    rows = b"".join(struct.pack(">iiiii", round(lat * 1e7), round(lon * 1e7),
                                12_000, 1500, 150) for _ in range(n))
    strm = (_klv(b"STNM", b"c", 1, 4, b"GPS5")
            + _klv(b"GPSF", b"L", 4, 1, struct.pack(">I", 3))
            + _klv(b"SCAL", b"l", 4, 5, scal)
            + _klv(b"GPS5", b"l", 20, n, rows))
    devc = (_klv(b"DVID", b"L", 4, 1, struct.pack(">I", 1))
            + _klv(b"DVNM", b"c", 1, 6, b"Camera")
            + _klv(b"STRM", b"\x00", 1, len(strm), strm))
    return _klv(b"DEVC", b"\x00", 1, len(devc), devc)


def gopro_udta(variant: int = 0) -> bytes:
    serial = secret("SERIAL", variant).encode()
    gpmf = (_klv(b"CASN", b"c", 1, len(serial), serial)
            + _klv(b"MINF", b"c", 1, 9, b"HERO8 Blk"))
    return c.box(b"udta", c.box(b"FIRM", b"HD8.01.01.60.00")
                 + c.box(b"GPMF", gpmf))


def tmcd_sample(variant: int = 0) -> bytes:
    """The frame number of the first frame: at 30 fps, the time of day the camera
    started recording (14:0v:05:12 here)."""
    return struct.pack(">I", ((14 * 60 + variant) * 60 + 5) * 30 + 12)


def drone_subtitle(variant: int = 0) -> bytes:
    """One tx3g sample: a 16-bit length, then the text."""
    lat, lon = coords(variant)
    text = (f"HOME(W: {-lon:.6f}, N: {lat:.6f}) 2026-10-0{1 + variant % 9} 14:05:12\n"
            f"GPS(W: {-lon:.6f}, N: {lat:.6f}, 21.00m)\n"
            "F.PRY (0.0\u00b0, 0.0\u00b0, 0.0\u00b0)").encode()
    return struct.pack(">H", len(text)) + text


def camm_gps(variant: int = 0) -> bytes:
    """A camm type-5 record: reserved, type, then latitude, longitude, altitude as
    little-endian doubles (the spec's byte order)."""
    lat, lon = coords(variant)
    return struct.pack("<HH", 0, 5) + struct.pack("<ddd", lat, lon, 21.0)


def xyz_box(variant: int = 0) -> bytes:
    lat, lon = coords(variant)
    iso6709 = f"{lat:+08.4f}{lon:+09.4f}/".encode()
    return c.box(b"\xa9xyz", struct.pack(">HH", len(iso6709), 0x15C7) + iso6709)


def android_meta(variant: int = 0) -> bytes:
    return c.keys_meta((("com.android.version", "14"),
                        ("com.android.model", secret("MODEL", variant))))


def build(variant: int = 0, *, shape: str = "gopro", tmcd: bool = True) -> bytes:
    """One video sample (and one audio sample) plus the shape's telemetry, laid out
    mdat first so each chunk's offset is known before moov is written."""
    video = b"\x00\x00\x00\x10" + b"\x65" + bytes(range(15))   # a stand-in NAL
    audio = b"\x21\x10\x05" + b"\x00" * 13
    tracks: list[tuple[dict, bytes]] = [
        ({"handler": b"vide", "name": "VideoHandler"}, video),
        ({"handler": b"soun", "name": "SoundHandler", "fmt": b"mp4a"}, audio)]
    user = b""
    trailer = b""
    if shape == "gopro":
        if tmcd:
            tracks.append(({"handler": b"tmcd", "name": "GoPro TCD", "fmt": b"tmcd"},
                           tmcd_sample(variant)))
        tracks.append(({"handler": b"meta", "name": "GoPro MET", "fmt": b"gpmd"},
                       gpmf_gps(variant)))
        tracks.append(({"handler": b"meta", "name": "GoPro SOS", "fmt": b"fdsc"},
                       b"\x00" * 16))
        user = gopro_udta(variant)
    elif shape == "drone":
        tracks.append(({"handler": b"sbtl", "name": "SubtitleHandler", "fmt": b"tx3g"},
                       drone_subtitle(variant)))
    elif shape == "android":
        user = c.box(b"udta", xyz_box(variant))
        trailer = c.box(b"sefd", b"\x00\x00" + secret("SAMSUNG", variant).encode())
    elif shape == "camm":
        tracks.append(({"handler": b"camm", "name": "CameraMetadataMotionHandler",
                        "fmt": b"camm"}, camm_gps(variant)))
    else:
        raise ValueError(shape)

    head = c.ftyp(b"mp42" if shape != "android" else b"isom")
    mdat_payload = b"".join(p for _, p in tracks)
    mdat_at = len(head)
    offset = mdat_at + 8
    traks = b""
    for i, (t, payload) in enumerate(tracks, start=1):
        traks += c.trak(track_id=i, offsets=(offset,), sizes=(len(payload),), **t)
        offset += len(payload)
    extra = android_meta(variant) if shape == "android" else b""
    moov = c.box(b"moov", c.mvhd(0, 0, len(tracks) + 1) + traks + user + extra)
    out = head + c.box(b"mdat", mdat_payload) + moov + trailer
    if shape == "gopro":
        # The video sample entry's `compressorname`: a Pascal string at 42 bytes
        # into the `avc1` entry's payload, as GoPro writes it.
        at = out.index(b"avc1", len(head) + 8 + len(mdat_payload)) + 4 + 42
        name = bytes([len(ENCODER_NAME)]) + ENCODER_NAME
        out = out[:at] + name + out[at + len(name):]
    return out


def planted(variant: int = 0, shape: str = "gopro") -> list[bytes]:
    """Every planted value as the bytes it is stored as."""
    lat, lon = coords(variant)
    out = []
    if shape == "gopro":
        out += [struct.pack(">i", round(lat * 1e7)), secret("SERIAL", variant).encode(),
                tmcd_sample(variant), ENCODER_NAME]
    elif shape == "drone":
        out += [f"{lat:.6f}".encode()]
    elif shape == "android":
        out += [f"{lat:+08.4f}".encode(), secret("MODEL", variant).encode(),
                secret("SAMSUNG", variant).encode()]
    elif shape == "camm":
        out += [struct.pack("<d", lat)]
    return out
