"""FLAC F1 — bit-preserving metadata strip.

FLAC keeps metadata blocks strictly outside the audio frames, so this tier is exact:
rebuild the metadata chain with the tag-bearing blocks removed and copy the audio
bytes through untouched. No decode, no re-encode, no quality question at all.

What goes, and why:
  * VORBIS_COMMENT — the tags themselves, AND the vendor string, which names the
    encoder ("reference libFLAC 1.4.3", "Lavf60.16.100"). The vendor string is a
    producer fingerprint that happens to live in a metadata block rather than in the
    audio, so unlike MP3's LAME tag it costs nothing to remove. The block goes
    entirely; an earlier version emitted a canonical EMPTY comment block instead, and
    the scrubber-fingerprint guard caught it red-handed: `84 00 00 08` followed by
    eight zero bytes appeared in every output regardless of input — our own signature,
    stamped on every file. Both choices are recognisable as scrubbed (no real encoder
    writes an empty vendor string either), so the guard decides it.
  * PICTURE — cover art. Not a byte blob to delete blindly: embedded art carries its
    own EXIF/GPS, so leaving it would be the container-recursion failure mode.
  * APPLICATION, CUESHEET — third-party payloads and disc layout with ISRCs.
  * A leading ID3v2 tag, which is out of spec for FLAC but common, and invisible to
    any scrubber that starts reading at "fLaC".

What stays: STREAMINFO (mandatory; describes the audio) and SEEKTABLE (an index into
the audio — kept at F1 because removing it is a playback regression, and regenerated
canonically at F2).

PADDING is dropped entirely rather than rewritten to a fixed size. Padding length is
an encoder tell, so it cannot be passed through; but replacing it with a fixed block
only swaps the source's tell for OUR tell, and a multi-kilobyte run of zeroes is a
poor thing to stamp on every output — it is exactly the kind of constant the
scrubber-fingerprint guard exists to flag. Emitting none is equally deterministic,
smaller, and valid: padding exists so taggers can write in place, which is not
something a privacy scrubber should be optimising for.
"""
from __future__ import annotations

from ...errors import ParseError
from . import walker as w

# No padding in output (see module docstring): normalising it to a fixed size would
# replace the source's tell with our own, and a kilobytes-long constant run is a
# scrubber signature by any reasonable definition.
CANONICAL_PADDING = 0

# Likewise no empty VORBIS_COMMENT block: a fixed 12-byte run in every output is a
# tool signature, and the fingerprint guard fails the build over it (correctly).
EMIT_EMPTY_COMMENT = False


def _block_header(btype: int, length: int, last: bool) -> bytes:
    return bytes([(0x80 if last else 0) | btype]) + length.to_bytes(3, "big")


def _empty_vorbis_comment() -> bytes:
    """A VORBIS_COMMENT with an empty vendor string and zero comments — the canonical
    form. Note the little-endian lengths (Vorbis heritage, unlike the rest of FLAC)."""
    return (0).to_bytes(4, "little") + (0).to_bytes(4, "little")


def scrub(data: bytes) -> bytes:
    layout = w.walk(data)

    keep: list[tuple[int, bytes]] = []
    for b in layout.blocks:
        if b.type == w.STREAMINFO:
            keep.append((b.type, b.payload))
        elif b.type == w.SEEKTABLE:
            keep.append((b.type, b.payload))
        elif b.type in w.METADATA_BLOCKS or b.type == w.PADDING:
            continue                       # dropped, or rebuilt canonically below
        else:
            # Reserved/unknown block type: fail closed rather than copy through a
            # region we cannot reason about.
            raise ParseError(f"FLAC: unknown metadata block type {b.type}")

    if EMIT_EMPTY_COMMENT:
        keep.append((w.VORBIS_COMMENT, _empty_vorbis_comment()))
    if CANONICAL_PADDING:
        keep.append((w.PADDING, b"\x00" * CANONICAL_PADDING))

    out = bytearray(w.MAGIC)
    for i, (btype, payload) in enumerate(keep):
        out += _block_header(btype, len(payload), last=(i == len(keep) - 1))
        out += payload
    out += data[layout.audio_start:layout.audio_end]
    return bytes(out)


def residuals(data: bytes) -> list[str]:
    """Re-walk scrubbed output; anything but a clean audio-only stream is a leak."""
    out: list[str] = []
    layout = w.walk(data)
    if layout.id3v2 is not None:
        out.append("ID3v2 tag survived on a FLAC file")
    for b in layout.blocks:
        if b.type in (w.APPLICATION, w.CUESHEET, w.PICTURE):
            out.append(f"{b.name} block survived at {b.offset}")
    vc = layout.vorbis_comment
    if vc is not None:
        vendor, items = w.parse_vorbis_comment(vc.payload)
        if vendor:
            out.append(f"vendor string survived: {vendor!r}")
        if items:
            out.append(f"{len(items)} Vorbis comment(s) survived")
    # Defense in depth: art and tag magics must not appear anywhere in the output.
    for magic, label in ((b"\xff\xd8\xff", "JPEG cover art"), (b"\x89PNG", "PNG cover art"),
                         (b"ID3", "ID3 magic"), (b"APETAGEX", "APEv2")):
        if magic in data[:layout.audio_start]:
            out.append(f"{label} present in the metadata region")
    return out
