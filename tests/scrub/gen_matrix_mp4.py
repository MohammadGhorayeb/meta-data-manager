"""Generate the MP4 Pareto matrix from real harness runs.

Honesty rule (CLAUDE.md): a cell is pass/fail only if we measured it.

Measured here:
  - A1@F1   differential leak oracle over content-identical MP4s whose metadata
            differs only by a sentinel. Everything F1 touches has to collapse:
            `udta/loci`, the tag list, the timestamps in all three header boxes,
            and the handler names.
  - A2@F1   four producers, three of them ffmpeg invocations and one a genuinely
            different muxer (AVFoundation, pass-through so the coded video is
            identical). E-MP4 runs the same comparison at `raw` first, so the cell
            reports what F1 CLOSED as well as what it left.

Not measured, and said so rather than left blank:
  - F2/F3   not built. An MP4 F2 means re-muxing losslessly through one canonical
            muxer — one brand, one compatible-brand list, one top-level order, one
            `moov` position — and F3 means re-encoding the video. Neither exists, so
            both are `not_tested`, which is the honest verdict for a tier nobody ran.

A2@F1 is expected to FAIL, and running it is worth it for *which* features leak:
the Phase 3 method — measure which producer channel survives before designing the
tier that closes it. The four surviving keys ARE the specification for an F2.

Run:  ./.venv/bin/python -m tests.scrub.gen_matrix_mp4
"""
from __future__ import annotations

import os
import tempfile

from tests.harness import config
from tests.harness.contract import Cell, V
from tests.harness.oracle import fingerprint_guard, leak
from tests.harness.plugins.mp4 import Mp4Plugin
from tests.harness.runner import matrix
from tests.scrub import e_mp4
from tests.scrub import mp4_corpus as mc

TOOL = {
    "name": "irreversible_scrubber",
    "version": "0.1.0-p4",
    "invocation": "python -m src.scrub {in} {out} --fidelity {fidelity}",
}


def _scrubber():
    from tests.scrub.test_harness_a1 import InProcessScrubber
    return InProcessScrubber()


def _a1_variants(tmpdir: str, n_variants: int = 3, n_repeats: int = 3):
    """Same media, same container, metadata differing only by a sentinel.

    Hand-built rather than encoded, so the coded video is identical by construction
    and every difference between two scrubbed outputs is metadata-correlated and
    nothing else. ffmpeg cannot give that guarantee: two runs of the same encode are
    not bit-identical often enough to rely on.
    """
    groups = []
    for i in range(n_variants):
        marker = "-" + chr(65 + i) * 6
        blob = mc.handbuilt(variant=marker)
        paths = []
        for r in range(n_repeats):
            path = os.path.join(tmpdir, f"a1_v{i}_r{r}.mp4")
            with open(path, "wb") as f:
                f.write(blob)
            paths.append(path)
        groups.append(paths)
    return groups


def _diverse(tmpdir: str, n: int = 4) -> list[str]:
    """Inputs sharing as little as possible, so a run common to every OUTPUT is ours.

    Varied along every axis the writer controls: which tracks exist, the media
    bytes, the layout, the brand, and whether `mdat` uses the largesize header. An
    undiverse corpus here would report the container skeleton as our fingerprint —
    the DOCX M11 failure, which was the corpus and not the guard.
    """
    paths = []
    # The TRACK SHAPE varies as well as the container, and that is not decoration.
    # The first version of this corpus varied brand, layout, largesize and track
    # count while every hand-built track kept the same duration, dimensions and
    # timescale -- so all four outputs shared a byte-identical 389-byte `trak`, and
    # the guard reported the whole box as our signature. It was right: an undiverse
    # corpus cannot tell a constant the TOOL introduces from one the CORPUS never
    # varied. DOCX M11 hit this exactly, and the fix is the same -- vary the thing,
    # do not widen the declaration.
    shapes = [
        dict(moov_first=False, major=b"isom", largesize_mdat=False,
             tracks=({"track_id": 1, "handler": b"vide", "name": "Alpha",
                      "duration": 2000, "width": 160, "height": 120,
                      "timescale": 1000},)),
        dict(moov_first=True, major=b"mp42", largesize_mdat=True,
             tracks=({"track_id": 3, "handler": b"vide", "name": "Beta",
                      "duration": 5500, "width": 640, "height": 480,
                      "timescale": 600, "language": 0x15C7},)),
        dict(moov_first=False, major=b"iso2", largesize_mdat=True,
             tracks=({"track_id": 2, "handler": b"vide", "name": "Gamma",
                      "duration": 900, "width": 320, "height": 240,
                      "timescale": 90000, "fmt": b"hvc1"},
                     {"track_id": 7, "handler": b"soun", "name": "Delta",
                      "fmt": b"mp4a", "duration": 1234, "timescale": 48000},)),
        dict(moov_first=True, major=b"mp41", largesize_mdat=False,
             table="co64",
             tracks=({"track_id": 5, "handler": b"vide", "name": "Epsilon",
                      "duration": 33333, "width": 1920, "height": 1080,
                      "timescale": 30000},)),
    ]
    for i in range(n):
        shape = dict(shapes[i % len(shapes)])
        shape["media"] = (mc.SENTINEL + bytes([(i * 37 + j) % 251
                                               for j in range(120 + i * 40)]))
        shape["variant"] = f"-div{i}"
        path = os.path.join(tmpdir, f"div_{i}.mp4")
        with open(path, "wb") as f:
            f.write(mc.handbuilt(**shape))
        paths.append(path)
    return paths


def build_doc(tmpdir: str) -> dict:
    plugin = Mp4Plugin()
    scrubber = _scrubber()

    not_built = ("mp4_f2_not_built: a lossless re-mux through one canonical muxer "
                 "(one brand, one compatible-brand list, one top-level order, one "
                 "`moov` position) is the next step, and the A2@F1 leaks are its "
                 "specification")
    f3_not_built = ("mp4_f3_not_built: would mean re-encoding the video, which "
                    "Phase 4 has not measured")

    cells = [
        leak.evaluate_a1(scrubber, plugin, _a1_variants(tmpdir), "F1", n=3,
                         sentinel_field="metadata_variant", modality="bytes"),
        Cell("A1", "F2", V.NOT_TESTED, reason=not_built),
        Cell("A1", "F3", V.NOT_TESTED, reason=f3_not_built),
        e_mp4.evaluate_cell("F1", e_mp4.build_sources(tmpdir, repeats=3), tmpdir),
        Cell("A2", "F2", V.NOT_TESTED, reason=not_built),
        Cell("A2", "F3", V.NOT_TESTED, reason=f3_not_built),
    ]

    diverse = _diverse(tmpdir, n=4)
    guard_verdict, signatures = fingerprint_guard.evaluate(
        scrubber, plugin, diverse, "F1", min_len=config.MIN_SIG_LEN)
    excluded = [{"bytes_hex": c.hex(), "decoded": c.decode("latin-1", "replace")}
                for c in plugin.mandatory_constants()]
    fp = matrix.fingerprint_block(guard_verdict, signatures, excluded)
    return matrix.assemble("mp4", TOOL, cells, fp)


def main() -> str:
    tmpdir = tempfile.mkdtemp(prefix="mp4_matrix_")
    doc = build_doc(tmpdir)
    out_path = os.path.join(str(config.RESULTS_DIR), f"mp4_{TOOL['name']}.json")
    matrix.write(doc, out_path)
    return out_path


if __name__ == "__main__":
    print(main())
