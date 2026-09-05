"""What a FLAC's metadata says — for the scrub report."""
from __future__ import annotations

from . import walker as w

_LABEL = {"PICTURE": "cover art", "APPLICATION": "application block",
          "CUESHEET": "cue sheet", "SEEKTABLE": "seek table",
          "PADDING": "padding"}


def describe(data: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    layout = w.walk(data)

    if layout.id3v2:
        out["ID3v2 tag"] = f"({layout.id3v2[1]} bytes — out of spec on FLAC)"

    for block in layout.blocks:
        name = block.name
        if name == "STREAMINFO":
            continue                       # the audio's own description, not metadata
        if name == "VORBIS_COMMENT":
            try:
                vendor, comments = w.parse_vorbis_comment(block.payload)
            except Exception:                             # noqa: BLE001
                out["Vorbis comment"] = f"({block.length} bytes, unparseable)"
                continue
            if vendor:
                # The vendor string names the encoder outright, which is why FLAC's
                # A2 defense costs nothing: it sits in a metadata block rather than
                # inside the compressed audio.
                out["Vorbis vendor"] = vendor
            for entry in comments:
                key, _, value = entry.partition("=")
                out[f"Vorbis:{key}"] = value
            continue
        if name == "PICTURE":
            out["cover art"] = f"({block.length} bytes)"
            continue
        out[_LABEL.get(name, name)] = f"({block.length} bytes)"
    return out
