"""Per-format handlers. Each registers with the dispatcher by magic number and
implements the ImageHandler interface (formats/base.py). Handlers call the shared
standards modules; they never re-implement EXIF/XMP/ICC/IPTC parsing.
"""
