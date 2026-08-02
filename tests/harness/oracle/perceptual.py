"""F3 content-preservation guard (Part A §6; Part B §5.7).

This module answers a *different* question from the leak oracle. The §5.1 variance
engine answers "did metadata leak?"; this module answers "did the *content*
survive the scrub?" -- a hard constraint that is independent of the leak verdict.
A scrub that strips every byte of metadata but mangles the pixels still fails F3.

Metrics:
  * image: ``imagehash.phash`` (DCT-based; pHash distances are ~normally
    distributed per the perceptual-hashing literature). Hamming distance.
  * audio: Chromaprint via ``fpcalc -raw`` (raw integer vectors -- base64
    fingerprints are NOT directly comparable), correlation in [0, 1].
  * video: forward-looking, NOT built in Phase 0 (NotImplementedError stub).

Thresholds are NEVER hard-coded: see ``check_preserved`` for the per-format
calibration procedure. The threshold is a required parameter.

Import safety: ``imagehash`` / ``PIL`` are installed and imported at module top.
``acoustid``/``fpcalc`` are NOT guaranteed (fpcalc may be absent), so the audio
path is fully lazy -- this module imports cleanly with no chromaprint present and
only fails when the audio path is actually *called*.
"""
from __future__ import annotations

import subprocess

import imagehash
from PIL import Image

from ..contract import PerceptualReport


def image_distance(a_path: str, b_path: str) -> int:
    """pHash Hamming distance between two images.

    ``imagehash.phash(Image.open(...))`` for each, return ``hash_a - hash_b``
    (imagehash overloads ``-`` to the Hamming distance between the hashes). 0 means
    identical perceptual hash; larger means more divergent content.
    """
    with Image.open(a_path) as img_a:
        hash_a = imagehash.phash(img_a)
    with Image.open(b_path) as img_b:
        hash_b = imagehash.phash(img_b)
    return hash_a - hash_b


def _fpcalc_raw(path: str) -> list[int]:
    """Run ``fpcalc -raw <path>`` and parse the integer fingerprint vector.

    ``-raw`` emits the comparable integer fingerprint (the base64 form is NOT
    directly comparable). Raises a clear error if fpcalc is absent or fails --
    this is only reached when the audio path is actually called, keeping the
    module import-safe where chromaprint/fpcalc is unavailable.
    """
    try:
        proc = subprocess.run(
            ["fpcalc", "-raw", path],
            capture_output=True,
        )
    except (FileNotFoundError, OSError) as exc:
        raise RuntimeError(
            "fpcalc (chromaprint) is not installed; the audio content-preservation "
            "path cannot run. Install chromaprint (brew install chromaprint) to "
            "enable audio F3 checks."
        ) from exc
    if proc.returncode != 0:
        raise RuntimeError(
            f"fpcalc failed on {path!r} (rc={proc.returncode}): "
            f"{proc.stderr.decode('utf-8', 'replace').strip()}"
        )

    fingerprint: list[int] = []
    for line in proc.stdout.decode("utf-8", "replace").splitlines():
        if line.startswith("FINGERPRINT="):
            raw = line[len("FINGERPRINT="):].strip()
            fingerprint = [int(x) for x in raw.split(",") if x]
            break
    if not fingerprint:
        raise RuntimeError(f"fpcalc produced no raw fingerprint for {path!r}")
    return fingerprint


def _raw_vector_agreement(a: list[int], b: list[int]) -> float:
    """Bitwise agreement between two raw Chromaprint fingerprint vectors, in [0,1].

    Each 32-bit sub-fingerprint is compared bit-for-bit; agreement is the fraction
    of matching bits over the overlapping prefix. 1.0 == identical fingerprints,
    ~0.5 == unrelated audio (random bit agreement). This mirrors Chromaprint's own
    Hamming-distance matching without needing the chromaprint C bindings.
    """
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    matching_bits = 0
    for x, y in zip(a[:n], b[:n], strict=True):  # both sliced to n by construction
        # XOR then count differing bits; agreement = 32 - popcount per word.
        diff = (x ^ y) & 0xFFFFFFFF
        matching_bits += 32 - bin(diff).count("1")
    return matching_bits / (n * 32)


def audio_similarity(a_path: str, b_path: str) -> float:
    """Agreement in [0, 1] between two audio files' Chromaprint fingerprints.

    Fingerprints each file via ``fpcalc -raw`` and compares the raw integer
    vectors (``acoustid.compare_fingerprints`` only accepts base64 fingerprints and
    needs the chromaprint C bindings, so the raw-vector correlation is the primary
    and most portable path). Higher == more similar; 1.0 == identical.

    Import-safe: ``fpcalc`` is invoked lazily here, so the module imports without
    chromaprint and only this call fails (with a clear RuntimeError) when fpcalc is
    absent.
    """
    fp_a = _fpcalc_raw(a_path)
    fp_b = _fpcalc_raw(b_path)
    return _raw_vector_agreement(fp_a, fp_b)


def video_preserved(*args, **kwargs):
    """Forward-looking video content-preservation check -- NOT built in Phase 0.

    The intended Phase-N implementation samples frames via ffmpeg (e.g.
    ``ffmpeg -i in -vf fps=1 frame_%04d.png``), computes a per-frame pHash, and
    aggregates frame-wise Hamming distances against a separately *calibrated*
    per-codec threshold (video needs its own calibration, distinct from still
    images). Phase 0 ships this as a documented stub only.
    """
    raise NotImplementedError(
        "video content-preservation (ffmpeg frame-sampling + per-frame pHash) is "
        "a forward-looking Phase-N feature; not built in Phase 0."
    )


def check_preserved(modality: str, baseline_path: str, scrubbed_path: str,
                    threshold: float) -> PerceptualReport:
    """Decide whether scrubbed content is perceptually the same as the baseline.

    ``preserved = distance <= threshold`` (image) or ``similarity >= threshold``
    (audio). ``threshold`` is a REQUIRED parameter -- it is never hard-coded.

    Per-format calibration procedure (Part A §6):
      1. Take a representative file in the target format.
      2. Re-encode the *same content* twice with the target codec/pipeline.
      3. Measure the metric between the two re-encodes (image: pHash distance;
         audio: fingerprint agreement). That is the same-content floor introduced
         by the codec alone.
      4. Repeat across a few samples; take the worst observed case (max distance /
         min agreement).
      5. Set ``threshold`` just past that floor -- a hair above the max distance
         for images, a hair below the min agreement for audio. Krawetz's 10-bit
         pHash heuristic is only a sanity ceiling, not the calibrated value: an F3
         scrub is a same-content re-encode, so the expected distance is single
         digit and the gate should be tight.

    Returns a fully-populated ``PerceptualReport`` (``checked=True``).
    """
    if modality == "image":
        distance = float(image_distance(baseline_path, scrubbed_path))
        preserved = distance <= threshold
        metric = "phash_hamming"
    elif modality == "audio":
        distance = float(audio_similarity(baseline_path, scrubbed_path))
        preserved = distance >= threshold
        metric = "chromaprint_agreement"
    elif modality == "video":
        # Surfaces the forward-looking stub's NotImplementedError.
        video_preserved(baseline_path, scrubbed_path, threshold)
        raise AssertionError("unreachable")  # pragma: no cover
    else:
        raise ValueError(f"unknown modality {modality!r} (expected image|audio|video)")

    return PerceptualReport(
        checked=True,
        metric=metric,
        distance=distance,
        threshold=float(threshold),
        preserved=preserved,
    )
