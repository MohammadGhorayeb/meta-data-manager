"""JPEG handler package: segment walker (segments.py) + fidelity handlers.

JPEG is the first leaf (implementation plan P1). Depths, per CLAUDE.md:
  lib        — not used for the strip path (we don't trust a library to drop
               every locus); libjpeg-turbo is used only as the pinned decoder
               for content-identity checks and the F2/F3 re-encoders.
  spec-parse — segments.py walks the marker stream.
  raw/hex    — f1.py does byte-exact segment surgery over big-endian lengths.
"""
