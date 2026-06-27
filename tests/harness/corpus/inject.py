"""Real-seed metadata-variant injection (Part B §6.2; Part A §4, §7).

SECONDARY / OPTIONAL corpus generator. The primary corpus is the synthetic
TOYF format (`corpus/synthetic.py`), where content-identity is a theorem. This
module gives *external validity*: it takes a real file (JPEG/PNG/audio/PDF/...)
and produces ``n_variants`` copies that differ **only in metadata**, each
carrying a known per-variant sentinel value (``AAAA..`` / ``BBBB..`` / ``CCCC..``)
so a variant-correlated diff is decidable and self-localizing.

Unlike the synthetic path, content-identity here must be **verified, not
assumed** -- injection tools can perturb the content stream. Every emitted
variant is passed through ``content_identity.same_content(seed, variant, ...)``
and is **rejected** (the whole call raises) if its canonical content changed.

Injectors (chosen by ``injector=`` / by file extension for the fallbacks):
  * ``"exiftool"`` (default): ``exiftool -<Tag>="<sentinel>" -o <out> <seed>``.
    ExifTool rewrites APP/metadata segments and leaves the compressed content
    stream byte-for-byte; it is the universal injector here.
  * format-native fallbacks: ``piexif`` (JPEG/WebP EXIF), ``mutagen`` (audio
    tags/atoms), ``pikepdf`` (PDF /Info + XMP), ``zipfile`` (OOXML parts).

Source seeds live at ``config.REAL_SEED_DIR`` (``~/metadata-research/step2/
originals/``), which is **absent** in CI / this environment. Callers MUST guard
with ``real_seed_available()`` and ``pytest.skip(...)`` when it returns False;
nothing in this module touches the seed directory at import time.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

from . import content_identity
from .. import config

# Per-variant sentinel byte (variant 0 -> b"A", 1 -> b"B", ...), matching the
# synthetic A1 convention in `synthetic._sentinel`.
_SENTINEL_LEN = 16

# Default ExifTool tag per modality family. ExifTool accepts these without
# re-encoding the content stream. `-Comment` is the broadest writable tag.
_DEFAULT_EXIFTOOL_TAG = "Comment"

# Extension -> modality for the content-identity check (§6.3).
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".tif", ".tiff", ".gif"}
_AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".oga", ".opus"}

# Extension -> native-fallback injector name (wired into the dispatch below).
_PIEXIF_EXTS = {".jpg", ".jpeg", ".webp", ".tif", ".tiff"}
_MUTAGEN_EXTS = _AUDIO_EXTS
_PIKEPDF_EXTS = {".pdf"}
_ZIPFILE_EXTS = {".docx", ".xlsx", ".pptx"}


# ---------------------------------------------------------------------------
# Seed availability / helpers
# ---------------------------------------------------------------------------
def real_seed_available() -> bool:
    """True iff the real-seed originals directory exists and is non-empty.

    Callers (pytest) should ``pytest.skip`` when this is False -- the seeds live
    outside the repo and are absent in CI / this environment. Never crashes.
    """
    try:
        d = config.REAL_SEED_DIR
        return d.is_dir() and any(d.iterdir())
    except OSError:
        return False


def list_real_seeds() -> list[str]:
    """Absolute paths of the files under ``config.REAL_SEED_DIR`` (empty if
    absent). Convenience for tests that fan out over every available seed."""
    if not real_seed_available():
        return []
    d = config.REAL_SEED_DIR
    return sorted(str(p) for p in d.iterdir() if p.is_file())


def modality_for(path: str) -> str:
    """Map a file extension to a content-identity modality (§6.3).

    jpg/png/webp/heic/... -> "image"; mp3/m4a/flac/wav/... -> "audio";
    everything else -> "bytes" (whole-file / content-substream exact hash).
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in _IMAGE_EXTS:
        return "image"
    if ext in _AUDIO_EXTS:
        return "audio"
    return "bytes"


def _sentinel(i: int, length: int = _SENTINEL_LEN) -> str:
    """Per-variant sentinel string: variant 0 -> 'AAAA..', 1 -> 'BBBB..', ..."""
    return chr(ord("A") + (i % 26)) * length


def _exiftool_available() -> bool:
    return shutil.which("exiftool") is not None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def inject_variants(seed_path: str, n_variants: int,
                    injector: str = "exiftool",
                    fields: list[str] | None = None,
                    tmpdir: str | None = None) -> list[str]:
    """Produce ``n_variants`` copies of ``seed_path`` differing only in metadata.

    Each variant carries a distinct sentinel value (``AAAA..``/``BBBB..``/...) in
    the chosen metadata field(s). Every emitted variant is verified against
    ``content_identity.same_content`` and the call **raises** if any variant's
    canonical content differs from the seed (a corpus bug -- never silently
    admitted).

    Parameters
    ----------
    seed_path : absolute path to the real source file.
    n_variants : how many metadata-only variants to produce (use >=3 so the A1
        correlation test separates from the floor; see Part A §4).
    injector : "exiftool" (default) or "auto" to pick a format-native fallback
        by extension, or one of "piexif"/"mutagen"/"pikepdf"/"zipfile".
    fields : metadata tag(s) to stamp the sentinel into. If None a sensible
        default is chosen per injector (ExifTool: ``-Comment``).
    tmpdir : directory for the output variants (a TemporaryDirectory is created
        and reused for the process lifetime if None).

    Returns
    -------
    list[str] : absolute paths of the ``n_variants`` content-identical variants.
    """
    if not os.path.isfile(seed_path):
        raise FileNotFoundError(seed_path)
    if n_variants < 1:
        raise ValueError("n_variants must be >= 1")

    if tmpdir is None:
        tmpdir = tempfile.mkdtemp(prefix="inject_")
    os.makedirs(tmpdir, exist_ok=True)

    modality = modality_for(seed_path)
    ext = os.path.splitext(seed_path)[1].lower()

    # Resolve the injector backend.
    backend = injector
    if injector == "auto":
        backend = _pick_native_injector(ext)

    out_paths: list[str] = []
    for i in range(n_variants):
        sentinel = _sentinel(i)
        base = os.path.splitext(os.path.basename(seed_path))[0]
        out = os.path.join(tmpdir, f"{base}_v{i}{ext}")
        if os.path.exists(out):
            os.unlink(out)

        if backend == "exiftool":
            _inject_exiftool(seed_path, out, fields, sentinel)
        elif backend == "piexif":
            _inject_piexif(seed_path, out, fields, sentinel)
        elif backend == "mutagen":
            _inject_mutagen(seed_path, out, fields, sentinel)
        elif backend == "pikepdf":
            _inject_pikepdf(seed_path, out, fields, sentinel)
        elif backend == "zipfile":
            _inject_zipfile(seed_path, out, fields, sentinel)
        else:
            raise ValueError(f"unknown injector {injector!r}")

        if not os.path.isfile(out):
            raise RuntimeError(
                f"injector {backend!r} did not produce an output for variant {i}"
            )

        # MANDATORY content-identity gate (§6.3). Reject on any content drift.
        if not content_identity.same_content(seed_path, out, modality, plugin=None):
            try:
                os.unlink(out)
            except OSError:
                pass
            raise AssertionError(
                f"injector {backend!r} perturbed content for variant {i} of "
                f"{seed_path!r} (modality={modality}); variant rejected"
            )
        out_paths.append(out)

    return out_paths


def _pick_native_injector(ext: str) -> str:
    if ext in _PIEXIF_EXTS:
        return "piexif"
    if ext in _MUTAGEN_EXTS:
        return "mutagen"
    if ext in _PIKEPDF_EXTS:
        return "pikepdf"
    if ext in _ZIPFILE_EXTS:
        return "zipfile"
    # No native handler for this extension -- ExifTool is the universal fallback.
    return "exiftool"


# ---------------------------------------------------------------------------
# Injector backends
# ---------------------------------------------------------------------------
def _inject_exiftool(seed: str, out: str, fields: list[str] | None,
                     sentinel: str) -> None:
    """Universal injector: ``exiftool -<Tag>="<sentinel>" -o <out> <seed>``.

    Rewrites metadata segments, leaving the compressed content stream intact.
    Raises if exiftool is unavailable or returns non-zero.
    """
    if not _exiftool_available():
        raise RuntimeError("exiftool not on PATH; cannot use the exiftool injector")
    tags = fields if fields else [_DEFAULT_EXIFTOOL_TAG]
    cmd = ["exiftool"]
    for tag in tags:
        # `-Tag=value` form; exiftool writes without re-encoding the stream.
        cmd.append(f"-{tag.lstrip('-')}={sentinel}")
    cmd += ["-o", out, seed]
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0 or not os.path.isfile(out):
        raise RuntimeError(
            "exiftool injection failed (rc="
            f"{p.returncode}): {p.stderr.decode('utf-8', 'replace').strip()}"
        )


def _inject_piexif(seed: str, out: str, fields: list[str] | None,
                   sentinel: str) -> None:
    """Format-native EXIF injector for JPEG/WebP/TIFF (pure-Python, MIT).

    Stamps the sentinel into the EXIF ImageDescription tag (or the user-supplied
    field name resolved against ``piexif.ImageIFD``) without touching pixels.
    """
    import piexif  # local import: keep module import-safe if piexif absent

    shutil.copyfile(seed, out)
    try:
        exif = piexif.load(out)
    except Exception:
        exif = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}

    tag_id = piexif.ImageIFD.ImageDescription
    if fields:
        name = fields[0].lstrip("-")
        tag_id = getattr(piexif.ImageIFD, name, tag_id)
    exif.setdefault("0th", {})[tag_id] = sentinel.encode("ascii")

    exif_bytes = piexif.dump(exif)
    piexif.insert(exif_bytes, out)


def _inject_mutagen(seed: str, out: str, fields: list[str] | None,
                    sentinel: str) -> None:
    """Format-native audio-tag injector (ID3/Vorbis/MP4 atoms) via mutagen.

    Writes the sentinel into a free-text comment tag; mutagen edits the tag
    region only, leaving the encoded audio frames intact.
    """
    import mutagen  # local import

    shutil.copyfile(seed, out)
    audio = mutagen.File(out, easy=True)
    if audio is None:
        raise RuntimeError(f"mutagen could not parse {seed!r}")
    key = (fields[0].lstrip("-") if fields else "comment")
    try:
        audio[key] = [sentinel]
    except Exception:
        # easy tag map may not expose the requested key; fall back to title.
        audio["title"] = [sentinel]
    audio.save()


def _inject_pikepdf(seed: str, out: str, fields: list[str] | None,
                    sentinel: str) -> None:
    """Format-native PDF injector: stamp the sentinel into /Info and XMP.

    pikepdf rewrites the document info dictionary / metadata stream without
    re-rendering page content.
    """
    import pikepdf  # local import

    field = (fields[0].lstrip("-") if fields else "Subject")
    with pikepdf.open(seed) as pdf:
        with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
            # dc:description is the XMP analogue of the /Info Subject field.
            meta["dc:description"] = sentinel
        pdf.docinfo[pikepdf.Name("/" + field)] = sentinel
        pdf.save(out)


def _inject_zipfile(seed: str, out: str, fields: list[str] | None,
                    sentinel: str) -> None:
    """Format-native OOXML injector: stamp the sentinel into docProps/core.xml.

    OOXML files are ZIP containers; this rewrites the core-properties part
    (a metadata XML part) and copies every other part byte-for-byte, leaving
    the document content parts untouched.
    """
    import zipfile

    core_part = "docProps/core.xml"
    field = (fields[0].lstrip("-") if fields else "subject")
    with zipfile.ZipFile(seed, "r") as zin:
        names = zin.namelist()
        items = {n: zin.read(n) for n in names}
        infos = {n: zin.getinfo(n) for n in names}

    if core_part in items:
        xml = items[core_part].decode("utf-8", "replace")
        tag = f"cp:{field}" if field in ("keywords",) else f"dc:{field}"
        open_t, close_t = f"<{tag}>", f"</{tag}>"
        if open_t in xml and close_t in xml:
            pre, _, rest = xml.partition(open_t)
            _, _, post = rest.partition(close_t)
            xml = f"{pre}{open_t}{sentinel}{close_t}{post}"
        else:
            xml = xml.replace("</cp:coreProperties>",
                              f"<dc:{field}>{sentinel}</dc:{field}></cp:coreProperties>")
        items[core_part] = xml.encode("utf-8")
    else:
        # No core-properties part; add one. Other parts copied verbatim.
        items[core_part] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties '
            'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f"<dc:{field}>{sentinel}</dc:{field}>"
            "</cp:coreProperties>"
        ).encode("utf-8")

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for n, data in items.items():
            if n in infos:
                zi = zipfile.ZipInfo(n, date_time=infos[n].date_time)
                zi.compress_type = infos[n].compress_type
                zi.external_attr = infos[n].external_attr
                zout.writestr(zi, data)
            else:
                zout.writestr(n, data)
