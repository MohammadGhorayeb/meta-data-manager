"""HEIC corpus for Phase 4.

Two sources, and the split matters (see `docs/p4_media_plan.md` §W5):

- **Synthetic**, via `pillow-heif`. Runs everywhere including CI, carries the
  metadata items F1 must remove, and gives an **independent decoder** for checking
  that the pixels survived — which is exactly where an implementation that shares no
  code with ours belongs.
- **Real iPhone photos**, in a git-ignored directory. They are what a synthetic file
  cannot be: a 61-to-95-tile grid, six auxiliary images, a thumbnail, and Apple's
  58 KB semantic-segmentation plist. Absent on CI, and reported absent rather than
  quietly skipped — the limit-#12 precedent.

The scrubber itself depends on neither: we parse ISOBMFF ourselves. `pillow-heif` is
test-side only, for building inputs and for verifying output we did not build.
"""
from __future__ import annotations

import glob
import io
import os

try:
    import pillow_heif
    from PIL import Image
    pillow_heif.register_heif_opener()
    HAVE_HEIF = True
except ImportError:                                       # pragma: no cover
    HAVE_HEIF = False

# Real photos: git-ignored (`*.HEIC` in .gitignore), never committed, and carrying
# genuine GPS. Point this elsewhere with the env var if you keep them somewhere else.
REAL_DIR = os.environ.get(
    "HEIC_REAL_SAMPLES",
    os.path.join(os.path.dirname(__file__), "..", "..", "metadata-research", "step2"))

SENTINEL = "HEIC-SENTINEL"


def real_samples() -> list[str]:
    """Real camera HEICs on this machine, or []. Never bundled."""
    if not os.path.isdir(REAL_DIR):
        return []
    out: list[str] = []
    for pattern in ("*.HEIC", "*.heic"):
        out.extend(glob.glob(os.path.join(REAL_DIR, pattern)))
    return sorted(out)


HAVE_REAL = bool(real_samples())


def _exif_payload() -> bytes:
    import piexif
    return piexif.dump({
        "0th": {
            piexif.ImageIFD.Make: b"TestCam",
            piexif.ImageIFD.Model: b"MZ-1",
            piexif.ImageIFD.Software: f"{SENTINEL}-app 1.0".encode(),
            piexif.ImageIFD.Artist: f"{SENTINEL}-author".encode(),
        },
        "Exif": {
            piexif.ExifIFD.DateTimeOriginal: b"2020:01:01 12:00:00",
        },
        "GPS": {
            piexif.GPSIFD.GPSLatitudeRef: b"N",
            piexif.GPSIFD.GPSLatitude: ((51, 1), (30, 1), (0, 1)),
            piexif.GPSIFD.GPSLongitudeRef: b"W",
            piexif.GPSIFD.GPSLongitude: ((0, 1), (7, 1), (0, 1)),
        },
    })


def _xmp_payload() -> bytes:
    return (f'<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="XMP Core 6.0.0">'
            f'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            f'<rdf:Description><creator>{SENTINEL}-xmp-author</creator>'
            f'</rdf:Description></rdf:RDF></x:xmpmeta>').encode()


def _pixels(width: int = 64, height: int = 48, seed: int = 0):
    """A deterministic, non-flat image. Flat colour compresses to almost nothing and
    would make a pixel-identity check pass for the wrong reason."""
    img = Image.new("RGB", (width, height))
    img.putdata([((x * 7 + seed) % 256, (y * 11 + seed) % 256, (x * y + seed) % 256)
                 for y in range(height) for x in range(width)])
    return img


def synthetic(path: str, *, with_exif: bool = True, with_xmp: bool = True,
              seed: int = 0, quality: int = 80) -> str:
    """A small HEIC carrying the metadata items F1 has to remove."""
    if not HAVE_HEIF:
        raise RuntimeError("pillow-heif is required to build a synthetic HEIC")
    kwargs: dict = {"quality": quality}
    if with_exif:
        kwargs["exif"] = _exif_payload()
    if with_xmp:
        kwargs["xmp"] = _xmp_payload()
    _pixels(seed=seed).save(path, format="HEIF", **kwargs)
    return path


def torture(path: str) -> str:
    """Every metadata locus a synthetic file can carry at once."""
    return synthetic(path, with_exif=True, with_xmp=True)


def clean(path: str, seed: int = 0) -> str:
    """The control: same pixels, no metadata items. What a scrubbed file should look
    like structurally, built independently of our scrubber."""
    return synthetic(path, with_exif=False, with_xmp=False, seed=seed)


def decoded_pixels(path_or_bytes) -> bytes:
    """The image as an independent decoder sees it — the content-identity oracle.

    `pillow-heif` shares no code with our walker, so a pixel match here is evidence
    rather than us agreeing with ourselves. Returns b"" when the decoder is absent,
    so a caller reports *not measured* instead of *clean*.
    """
    if not HAVE_HEIF:
        return b""
    try:
        source = (io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, bytes)
                  else path_or_bytes)
        with Image.open(source) as im:
            return im.convert("RGB").tobytes()
    except Exception:                                     # noqa: BLE001
        return b""


def available() -> dict[str, bool]:
    """What this machine can do, so a caller can report the gap rather than shrink
    the claim silently."""
    return {"synthetic": HAVE_HEIF, "real_camera_photos": HAVE_REAL,
            "decoder": HAVE_HEIF}
