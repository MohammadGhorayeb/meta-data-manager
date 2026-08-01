"""MP3 format handler (Phase 2 — audio leaf).

Three depths, as elsewhere in the project:
  - library:     mutagen / ffmpeg are used only as a pinned decoder + F3 re-encoder.
  - spec-parse:  walker.py walks ID3v2 (synchsafe sizes, unsync, footer), the MPEG
                 audio-frame stream (located by frame-header size, never a 0xFFFB
                 scan), the Xing/Info+LAME header frame, and the EOF trailer stack.
  - raw/hex:     f1.py is byte surgery over those regions (drop tags, canonicalize
                 the encoder frame, truncate appended data), keeping audio bytes.
"""
