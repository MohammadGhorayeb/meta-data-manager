"""Scrubber package: dispatch + shared standard modules + per-format handlers.

Layout (see docs/p1_images_plan.md):
  cli.py            entry point; fails closed on any parse/handler error
  dispatch.py       magic-number sniff -> handler registry
  errors.py         ScrubError hierarchy (fail-closed signalling)
  fidelity.py       F1 / F2 / F3 constants + validation
  standards/        cross-format metadata standards (EXIF/TIFF-IFD, XMP, ICC, IPTC)
  formats/          per-format handlers (jpeg first; png next)

Shared standard modules are written ONCE and called by every handler — a missed
copy is a leak (CLAUDE.md).
"""
