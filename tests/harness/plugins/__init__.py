"""Harness-side FormatPlugin implementations (real formats).

Phase 0 shipped only ToyFormatPlugin/GenericPlugin (dispatch.py). Real plugins
arrive with each format handler phase; this package holds them. A plugin is
*harness knowledge used to verify* — distinct from the scrubber under test
(harness README §2). It never scrubs.
"""
