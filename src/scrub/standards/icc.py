"""ICC colour-profile handling.

An ICC profile is a colour-management blob, but its 128-byte header carries
identifying metadata — preferred CMM, creation date/time, primary platform,
device manufacturer/model, profile creator, and a 16-byte MD5 profile ID — and
the *byte pattern of the whole profile* is itself an A2 vector (a given
camera/OS emits a recognizable canned profile). So ICC is in scope for scrubbing,
not just colour.

Where it hides:
  JPEG   ICC spans multiple APP2 segments, each payload =
         b"ICC_PROFILE\\0" | seq:u8 | total:u8 | chunk, reassembled by seq.
  PNG    a single iCCP chunk (name | 0 | deflate(profile)).
  (TIFF tag 0x8773, PDF stream, HEIC — same profile bytes.)

Phase-1 **default policy is strip** (drop APP2 / iCCP): simplest, and safe for
the sRGB-ish images that dominate. The exceptions are wide-gamut images where the
pixels are only correct *with* the profile — for those, F1/F2 keep a **sanitized**
profile (identifying header fields zeroed, profile ID recomputed) and F3 converts
to sRGB. `sanitize()` implements the keep path; the strip path needs nothing from
here. Which default wins is experiment E7.
"""
from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from typing import Optional

# JPEG APP2 ICC marker.
JPEG_ICC_SIG = b"ICC_PROFILE\x00"
_JPEG_ICC_HDR = len(JPEG_ICC_SIG) + 2   # sig + seq + total

HEADER_LEN = 128
_ACSP = b"acsp"  # profile signature at byte 36

# Header field spans (offset, length) — ICC.1:2010 §7.2.
_F_CMM = (4, 4)
_F_DATETIME = (24, 12)
_F_PLATFORM = (40, 4)
_F_MANUFACTURER = (48, 4)
_F_MODEL = (52, 4)
_F_CREATOR = (80, 4)
_F_PROFILE_ID = (84, 16)

# Fields zeroed *only for the ID computation* (ICC.1:2010 §7.2.18): profile
# flags, rendering intent, and the profile-ID field itself.
_ID_ZERO_SPANS = [(44, 4), (64, 4), (84, 16)]

# Identifying fields we zero when sanitizing (kept out of the ID-zero set so
# they contribute to the fresh, content-derived ID).
_SANITIZE_SPANS = [_F_CMM, _F_DATETIME, _F_PLATFORM,
                   _F_MANUFACTURER, _F_MODEL, _F_CREATOR]


@dataclass
class IccHeader:
    size: int
    cmm: bytes
    version: bytes
    device_class: bytes
    color_space: bytes
    pcs: bytes
    datetime_raw: bytes
    platform: bytes
    manufacturer: bytes
    model: bytes
    creator: bytes
    profile_id: bytes
    valid_signature: bool


def parse_header(profile: bytes) -> IccHeader:
    if len(profile) < HEADER_LEN:
        raise ValueError(f"ICC profile shorter than {HEADER_LEN}-byte header")
    (size,) = struct.unpack_from(">I", profile, 0)
    return IccHeader(
        size=size,
        cmm=profile[4:8],
        version=profile[8:12],
        device_class=profile[12:16],
        color_space=profile[16:20],
        pcs=profile[20:24],
        datetime_raw=profile[24:36],
        platform=profile[40:44],
        manufacturer=profile[48:52],
        model=profile[52:56],
        creator=profile[80:84],
        profile_id=profile[84:100],
        valid_signature=profile[36:40] == _ACSP,
    )


def looks_like_profile(buf: bytes) -> bool:
    """Cheap structural sniff: right length and the 'acsp' signature present."""
    return len(buf) >= HEADER_LEN and buf[36:40] == _ACSP


def reassemble_jpeg_app2(segments: list[bytes]) -> bytes:
    """Concatenate ICC chunks from JPEG APP2 payloads in sequence order. Each
    payload already has its 'ICC_PROFILE\\0'+seq+total prefix; we order by seq
    (1-based) and strip the prefixes. Missing/duplicate sequence numbers raise —
    a malformed ICC set is a parse failure, and we fail closed."""
    indexed: dict[int, bytes] = {}
    total: Optional[int] = None
    for payload in segments:
        if not payload.startswith(JPEG_ICC_SIG):
            raise ValueError("APP2 payload lacks ICC_PROFILE signature")
        seq = payload[len(JPEG_ICC_SIG)]
        tot = payload[len(JPEG_ICC_SIG) + 1]
        if total is None:
            total = tot
        elif tot != total:
            raise ValueError(f"inconsistent ICC total count {tot} != {total}")
        if seq in indexed:
            raise ValueError(f"duplicate ICC chunk seq {seq}")
        indexed[seq] = payload[_JPEG_ICC_HDR:]
    if total is None:
        raise ValueError("no ICC APP2 segments given")
    if sorted(indexed) != list(range(1, total + 1)):
        raise ValueError(f"ICC chunks incomplete: have {sorted(indexed)} of {total}")
    return b"".join(indexed[i] for i in range(1, total + 1))


def compute_profile_id(profile: bytes) -> bytes:
    """The 16-byte MD5 profile ID per ICC.1:2010 §7.2.18: MD5 over the profile
    with profile-flags, rendering-intent, and the profile-ID field zeroed."""
    buf = bytearray(profile)
    for off, length in _ID_ZERO_SPANS:
        buf[off:off + length] = b"\x00" * length
    return hashlib.md5(bytes(buf)).digest()


def sanitize(profile: bytes) -> bytes:
    """Return a copy with identifying header fields zeroed and the profile ID
    recomputed so the profile stays self-consistent. Colour data (tag table +
    tag data after the header) is untouched, so rendering is unchanged; only the
    provenance in the header is destroyed. Used for wide-gamut keep-paths and by
    experiment E7."""
    if len(profile) < HEADER_LEN:
        raise ValueError("profile too short to sanitize")
    buf = bytearray(profile)
    for off, length in _SANITIZE_SPANS:
        buf[off:off + length] = b"\x00" * length
    new_id = compute_profile_id(bytes(buf))
    off, length = _F_PROFILE_ID
    buf[off:off + length] = new_id
    return bytes(buf)
