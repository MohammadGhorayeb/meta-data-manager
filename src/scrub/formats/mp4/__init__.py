"""MP4 / QuickTime video handler (Phase 4). One handler for both, because every
iPhone video is QuickTime (`qt  `), not MP4, and the box grammar is shared. The box
layer lives in `standards/isobmff.py`, shared with M4A and HEIC.
"""
