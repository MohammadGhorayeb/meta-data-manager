"""IPTC-IIM inside Photoshop 8BIM image-resource blocks.

The IPTC "caption/keywords/byline/location" metadata lives in JPEG (and TIFF/PSD)
as an IPTC-IIM record wrapped in a Photoshop APP13 segment. APP13 is a catalogue
of 8BIM resource blocks; the IPTC record is resource 0x0404, but the same
catalogue also holds **a second embedded thumbnail** (0x040C / legacy 0x0409) —
another duplicate-image locus a named-tag scrubber misses.

Layout of an APP13-Photoshop payload:
  b"Photoshop 3.0\\0"                        identifier
  then zero+ 8BIM blocks:
    b"8BIM" | id:u16 | pascal-name | size:u32 | data[size]
  pascal-name = len:u8 | name, padded so (1+len) is even; data padded to even.

IPTC-IIM datasets inside resource 0x0404:
  0x1C | record:u8 | dataset:u8 | length:u16   (extended length if high bit set)

Phase-1 policy drops the whole APP13, so this module *parses and localizes*; it
does not rewrite IPTC. Parsing still matters: the corpus L6/L7 cases assert we
actually see the IPTC record and the 8BIM thumbnail rather than trusting that
"drop APP13" covered them.
"""
from __future__ import annotations

from dataclasses import dataclass

PHOTOSHOP_SIGS = (b"Photoshop 3.0\x00", b"Adobe_Photoshop2.5\x00")
_8BIM = b"8BIM"

IPTC_RESOURCE_ID = 0x0404
THUMBNAIL_RESOURCE_IDS = (0x040C, 0x0409)

# A few well-known resource ids for annotation/evidence (not exhaustive).
RESOURCE_NAMES = {
    0x0404: "IPTC-NAA",
    0x0409: "Thumbnail(PS4)",
    0x040C: "Thumbnail",
    0x0422: "EXIF-1",
    0x0424: "XMP",
    0x0425: "CaptionDigest",
    0x043A: "PrintFlags",
}

IPTC_MARKER = 0x1C


@dataclass
class Resource:
    id: int
    name: bytes
    data: bytes
    offset: int          # offset of the "8BIM" tag within the payload
    total_len: int       # bytes consumed by the whole block (incl. padding)


@dataclass
class IptcDataset:
    record: int
    dataset: int
    value: bytes


def photoshop_sig_len(payload: bytes) -> int:
    """Length of the leading Photoshop identifier, or 0 if none present."""
    for sig in PHOTOSHOP_SIGS:
        if payload.startswith(sig):
            return len(sig)
    return 0


def parse_8bim(payload: bytes) -> list[Resource]:
    """Walk the 8BIM resource blocks after the Photoshop identifier. Stops
    cleanly at the first malformed/short block (the handler drops the whole
    segment regardless; robustness beats raising here)."""
    i = photoshop_sig_len(payload)
    n = len(payload)
    out: list[Resource] = []
    while i + 4 <= n and payload[i:i + 4] == _8BIM:
        start = i
        i += 4
        if i + 2 > n:
            break
        rid = int.from_bytes(payload[i:i + 2], "big")
        i += 2
        # Pascal name: length byte + name, padded so (1+len) is even.
        if i >= n:
            break
        name_len = payload[i]
        name = payload[i + 1:i + 1 + name_len]
        i += 1 + name_len
        if (1 + name_len) % 2:
            i += 1  # pad to even
        if i + 4 > n:
            break
        size = int.from_bytes(payload[i:i + 4], "big")
        i += 4
        data = payload[i:i + size]
        i += size
        if size % 2:
            i += 1  # data padded to even
        out.append(Resource(id=rid, name=name, data=data,
                            offset=start, total_len=i - start))
    return out


def iptc_datasets(resource_data: bytes) -> list[IptcDataset]:
    """Parse IPTC-IIM datasets from a 0x0404 resource's data. Handles the
    standard 16-bit length; an extended-length dataset (high bit of the length
    set) stops the walk (rare, and we drop the record wholesale anyway)."""
    out: list[IptcDataset] = []
    i, n = 0, len(resource_data)
    while i + 5 <= n and resource_data[i] == IPTC_MARKER:
        record = resource_data[i + 1]
        dataset = resource_data[i + 2]
        length = int.from_bytes(resource_data[i + 3:i + 5], "big")
        i += 5
        if length & 0x8000:  # extended length: bail (uncommon)
            break
        out.append(IptcDataset(record=record, dataset=dataset,
                               value=resource_data[i:i + length]))
        i += length
    return out


def find_thumbnail_resources(resources: list[Resource]) -> list[Resource]:
    return [r for r in resources if r.id in THUMBNAIL_RESOURCE_IDS]


def find_iptc(resources: list[Resource]) -> list[Resource]:
    return [r for r in resources if r.id == IPTC_RESOURCE_ID]
