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
    total: int | None = None
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


# Tags that name a device, a person or a date, and that no colour transform reads.
# Measured (`docs/p6_tail_plan.md` §14): the real-file sweep found display profiles
# -- "Apple Cinema Display", a Mac's built-in "Display" -- embedded in vendor TIFFs
# and PDFs, each with Apple's MakeAndModel tag (`mmod`: the display's maker, model
# and a per-unit number that differs between two displays of one model). A Mac tags
# its screenshots with the screen's profile. Blanked in every profile that is not a
# published colour space, alongside the profile's own name and copyright (a
# calibration tool names a profile after its owner's screen), the device maker and
# model descriptions, the calibration date and target, and the profile sequence
# (which repeats device descriptions).
IDENTITY_TAGS = frozenset({b"mmod", b"dmnd", b"dmdd", b"desc", b"dscm", b"cprt",
                           b"calt", b"targ", b"pseq", b"psid"})
# A profile holding any of these describes one device or one calibration, whatever
# its description claims, so it is never treated as a published colour space.
DEVICE_TAGS = frozenset({b"mmod", b"calt", b"targ", b"pseq", b"psid"})


def tags(profile: bytes) -> list[tuple[bytes, int, int]]:
    """(signature, offset, size) for each entry of the tag table that lies inside
    the profile."""
    if len(profile) < HEADER_LEN + 4:
        return []
    count = struct.unpack_from(">I", profile, HEADER_LEN)[0]
    out = []
    for i in range(min(count, 1024)):
        at = HEADER_LEN + 4 + 12 * i
        if at + 12 > len(profile):
            break
        sig, off, size = struct.unpack_from(">4sII", profile, at)
        if off + size <= len(profile) and size >= 8:
            out.append((sig, off, size))
    return out


def _blank_tag(buf: bytearray, off: int, size: int) -> None:
    """Zero a tag's text or data in place, keeping its type, counts and size, so
    every reader still parses the profile and shows an empty value."""
    end = off + size
    kind = bytes(buf[off:off + 4])

    def zero(a: int, b: int) -> None:
        a, b = max(a, off + 8), min(b, end)
        if b > a:
            buf[a:b] = b"\x00" * (b - a)

    if kind == b"mluc" and size >= 16:
        n, rsize = struct.unpack_from(">II", buf, off + 8)
        for i in range(min(n, 256)):
            rec = off + 16 + i * rsize
            if rsize < 12 or rec + 12 > end:
                break
            length, start = struct.unpack_from(">II", buf, rec + 4)
            zero(off + start, off + start + length)
    elif kind == b"desc" and size >= 12:
        n = struct.unpack_from(">I", buf, off + 8)[0]
        zero(off + 12, off + 12 + n)                  # the ASCII name
        p = off + 12 + n
        if p + 8 <= end:                              # the Unicode name
            uc = struct.unpack_from(">I", buf, p + 4)[0]
            zero(p + 8, p + 8 + 2 * uc)
            q = p + 8 + 2 * uc
            zero(q + 3, q + 3 + 67)                   # the Macintosh name
    else:                                             # text, dtim, mmod, pseq...
        zero(off + 8, end)


def sanitize(profile: bytes) -> bytes:
    """Return a copy with identifying header fields zeroed and the profile ID
    recomputed so the profile stays self-consistent. A profile that is not a
    published colour space also has its identity tags blanked in place
    (`IDENTITY_TAGS`); a published one keeps its tags, which are the same bytes in
    millions of files. Colour data -- every tag a transform reads -- is untouched,
    so rendering is unchanged. Used for wide-gamut keep-paths and by experiment
    E7."""
    if len(profile) < HEADER_LEN:
        raise ValueError("profile too short to sanitize")
    buf = bytearray(profile)
    for off, length in _SANITIZE_SPANS:
        buf[off:off + length] = b"\x00" * length
    if not is_standard(profile):
        table = tags(profile)
        keep = {(o, z) for sig, o, z in table if sig not in IDENTITY_TAGS}
        for sig, off, size in table:
            # A data block shared with a colour tag is the colour tag's: left alone.
            if sig in IDENTITY_TAGS and (off, size) not in keep:
                _blank_tag(buf, off, size)
    new_id = compute_profile_id(bytes(buf))
    off, length = _F_PROFILE_ID
    buf[off:off + length] = new_id
    return bytes(buf)


def description(profile: bytes) -> str | None:
    """The profile's `desc` text: ICC v2 `textDescriptionType` (ASCII) or v4
    `multiLocalizedUnicodeType` (first record, UTF-16BE). None if absent."""
    if len(profile) < HEADER_LEN + 4:
        return None
    count = struct.unpack_from(">I", profile, HEADER_LEN)[0]
    for i in range(min(count, 256)):
        at = HEADER_LEN + 4 + 12 * i
        if at + 12 > len(profile):
            return None
        sig, off, size = struct.unpack_from(">4sII", profile, at)
        if sig != b"desc" or off + 12 > len(profile):
            continue
        kind = profile[off:off + 4]
        if kind == b"desc":
            n = struct.unpack_from(">I", profile, off + 8)[0]
            return profile[off + 12:off + 12 + n].split(b"\0")[0].decode("latin-1")
        if kind == b"mluc":
            records, rsize = struct.unpack_from(">II", profile, off + 8)
            if records and rsize >= 12:
                length, start = struct.unpack_from(">II", profile, off + 16 + 4)
                return profile[off + start:off + start + length].decode(
                    "utf-16-be", "replace").rstrip("\0")
        return None
    return None


# Published colour spaces: a profile describing one of these is a shared canned
# profile -- the same bytes in millions of files -- and is kept as it is. Zeroing
# its header would turn a crowd value into a mark of this tool. Anything else (a
# display calibrated on someone's own screen, a printer profile made for one
# machine) is sanitized.
STANDARD_DESCRIPTIONS = (
    "srgb", "display p3", "dci(p3)", "dci-p3", "smpte rp 431-2", "p3",
    "adobe rgb (1998)", "generic rgb profile", "generic gray profile",
    "generic gray gamma 2.2 profile", "generic cmyk profile", "generic lab profile",
    "generic xyz profile", "rec. itu-r bt.2020", "rec. itu-r bt.709", "itu-r bt.2020",
    "itu-r bt.709", "itu-2020", "itu-709", "rec. 2020", "rec. 709", "romm rgb",
    "prophoto rgb", "aces cg linear", "acescg linear", "coated fogra39",
    "uncoated fogra29", "u.s. web coated (swop) v2", "japan color 2001 coated",
    "dot gain 20%", "gray gamma 2.2", "linear gray", "sgray", "image p3",
)


def is_standard(profile: bytes) -> bool:
    """Anchored at the start, a non-alphanumeric character ending the name: a
    profile CALLED `sRGB IEC61966-2.1` is the published one; one called
    `Alice sRGB calibration` is not."""
    if any(sig in DEVICE_TAGS for sig, _o, _z in tags(profile)):
        return False                  # one device's profile, whatever it is called
    desc = (description(profile) or "").strip().lower()
    for name in STANDARD_DESCRIPTIONS:
        if desc.startswith(name):
            rest = desc[len(name):]
            if not rest or not rest[0].isalnum():
                return True
    return False
