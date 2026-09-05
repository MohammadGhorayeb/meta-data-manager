"""What an MP3's metadata says — for the scrub report.

ID3 frames are decoded by hand rather than through a library: the tag is the thing
being removed, and a reporting path that needed `mutagen` would make the report
depend on something the scrubber does not.
"""
from __future__ import annotations

from . import walker as w

# The frames a person recognises. Others are counted, not listed.
_FRAMES = {
    b"TIT2": "Title", b"TPE1": "Artist", b"TALB": "Album", b"TDRC": "Recorded",
    b"TYER": "Year", b"TCON": "Genre", b"COMM": "Comment", b"TENC": "Encoded by",
    b"TSSE": "Encoder settings", b"TCOP": "Copyright", b"TPUB": "Publisher",
    b"APIC": "Cover art", b"GEOB": "Embedded object", b"PRIV": "Private frame",
    b"TXXX": "User text", b"WXXX": "User URL", b"UFID": "Unique file id",
    # ID3v2.2 spellings, three characters rather than four.
    b"TT2": "Title", b"TP1": "Artist", b"TAL": "Album", b"PIC": "Cover art",
}


def _text(payload: bytes) -> str:
    """An ID3 text frame: one encoding byte, then the string."""
    if not payload:
        return ""
    enc, body = payload[0], payload[1:]
    codec = {0: "latin-1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}.get(enc, "latin-1")
    try:
        return body.decode(codec, "replace").split("\x00", 1)[0].strip()
    except Exception:                                     # noqa: BLE001
        return ""


def _comment(payload: bytes) -> str:
    """A COMM/USLT frame: encoding, language, description\\0, then the text."""
    if len(payload) < 5:
        return ""
    enc, body = payload[0], payload[4:]
    codec = {0: "latin-1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}.get(enc, "latin-1")
    sep = b"\x00\x00" if enc in (1, 2) else b"\x00"
    _, _, text = body.partition(sep)
    try:
        return text.decode(codec, "replace").strip("\x00").strip()
    except Exception:                                     # noqa: BLE001
        return ""


def _id3v2_frames(tag: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    if len(tag) < 10:
        return out
    version = tag[3]
    id_len, size_len = (3, 3) if version == 2 else (4, 4)
    header = id_len + size_len + (0 if version == 2 else 2)
    pos, unnamed = 10, 0
    while pos + header <= len(tag):
        fid = tag[pos:pos + id_len]
        if not fid.strip(b"\x00"):
            break
        raw = tag[pos + id_len:pos + id_len + size_len]
        if version == 4:                       # synchsafe sizes
            size = (raw[0] << 21) | (raw[1] << 14) | (raw[2] << 7) | raw[3]
        else:
            size = int.from_bytes(raw, "big")
        payload = tag[pos + header:pos + header + size]
        label = _FRAMES.get(fid)
        if label is None:
            unnamed += 1
        elif fid in (b"APIC", b"PIC", b"GEOB", b"PRIV", b"UFID"):
            out[f"ID3:{label}"] = f"({len(payload)} bytes)"
        elif fid in (b"COMM", b"USLT"):
            # COMM is not a plain text frame: encoding byte, a 3-byte language
            # code, then a NUL-terminated description, THEN the comment. Decoding
            # it as text reports the language ("eng") as the comment.
            value = _comment(payload)
            if value:
                out[f"ID3:{label}"] = value
        else:
            value = _text(payload)
            if value:
                out[f"ID3:{label}"] = value
        pos += header + size
        if size <= 0:
            break
    if unnamed:
        out["ID3:other frames"] = f"{unnamed} more"
    return out


def describe(data: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    layout = w.walk(data)

    if layout.id3v2:
        off, length = layout.id3v2
        out.update(_id3v2_frames(data[off:off + length]))

    for trailer in layout.trailers:
        blob = data[trailer.offset:trailer.offset + trailer.length]
        if trailer.kind == "id3v1":
            out["ID3v1:Title"] = blob[3:33].decode("latin-1", "replace").strip("\x00 ")
            out["ID3v1:Artist"] = blob[33:63].decode("latin-1", "replace").strip("\x00 ")
        else:
            out[f"{trailer.kind} tag"] = f"({trailer.length} bytes)"

    if layout.xing is not None and layout.xing.lame_version:
        # Not removable without re-encoding: the LAME tag lives inside an audio
        # frame, which is why MP3's A2 defense needs F3 (limit #1).
        out["LAME header"] = layout.xing.lame_version.decode("latin-1", "replace")

    if layout.appended and layout.appended[1]:
        out["trailing bytes"] = f"({layout.appended[1]} bytes after the last frame)"
    return out
