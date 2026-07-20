"""Cross-format metadata standards, written once and shared by every handler.

  tiff_ifd   EXIF/TIFF-IFD (JPEG APP1-Exif, TIFF, RAW, PNG eXIf, HEIC Exif item)
  xmp        XMP packet + ExtendedXMP (JPEG APP1-XMP, PNG iTXt, PDF, most others)
  icc        ICC colour profile (JPEG APP2 multi-segment, PNG iCCP, TIFF, PDF)
  iptc_iim   IPTC-IIM inside Photoshop 8BIM resources (JPEG APP13, TIFF, PSD)

Each module operates on the *payload buffer* a format walker hands it — it does
not know about JPEG segments or PNG chunks. That separation is what lets the
same EXIF stripper serve JPEG, RAW, HEIC and PNG unchanged (CLAUDE.md: shared
modules never copy-pasted per format).
"""
