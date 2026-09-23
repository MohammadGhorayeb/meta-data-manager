"""E-MP4 — muxer-layout peer-set experiment.

An MP4 carries two producers, and this experiment exists to keep them apart rather
than blur them into one verdict:

  * **Muxer layout** — the brand it stamps, the order it writes the top-level boxes
    in, whether `moov` precedes `mdat`, the `free` slack it leaves, what it calls
    each track's handler, and whether it writes `mdat`'s length in 32 or 64 bits.
    None of that depends on the picture.
  * **Coded video** — the H.264 itself. F1 copies it by definition, so F1 cannot
    touch that fingerprint; it reaches this channel only through file SIZE, and the
    harder question (is the encoder recoverable *from the picture*?) is an
    image-space one this cell does not pretend to answer.

The peer set is stronger than M4A's in one specific way, and it is the reason this
experiment is worth running rather than assuming: `avfoundation` is a genuinely
different **muxer**, not another ffmpeg invocation. Measuring a container channel
against one implementation's options would mostly measure the options.
"""
from __future__ import annotations

import os
import tempfile

from tests.harness.oracle import fields, variance
from tests.harness.plugins.mp4 import Mp4Plugin
from tests.scrub import mp4_corpus as mc

# What the MUXER decided. Content-independent: the same picture through two muxers
# differs on these, and the same muxer given two pictures does not.
_MUXER_KEYS = (
    "struct:brand", "struct:compatible_brands", "struct:top_level_order",
    "struct:moov_before_mdat", "struct:free_bytes", "struct:box_inventory",
    "struct:handler_names", "struct:mdat_header_form",
    "struct:timestamps_present",
)

# The coded video reaches this channel only through file SIZE. The video DIGEST is
# deliberately absent, for the reason E-M4A records: judging a format on its own
# compressed content in the categorical channel holds it to a bar MP3 is not held
# to, since MP3's structural channel is headers only. Presence of an encoder trace
# is not a leak; recoverability is, and recoverability is not a hash comparison.
_ENCODER_KEYS = ("struct:size",)


def build_sources(tmpdir: str, repeats: int = 3) -> dict:
    return mc.producers(tmpdir, repeats=repeats)


def run_condition(fidelity: str, sources: dict, tmpdir: str) -> dict:
    from src.scrub import cli
    plugin = Mp4Plugin()
    producers = list(sources)
    repeats = list(range(min(len(v) for v in sources.values())))
    cells = {}
    for prod, paths in sources.items():
        for r, src in enumerate(paths):
            if fidelity == "raw":
                target = src
            else:
                target = os.path.join(tmpdir, f"{prod}_{r}_{fidelity}.mp4")
                cli.scrub_file(src, target, fidelity)
            cells[(prod, r)] = fields.extract(target, plugin, "F1")
    verdicts = variance.decompose(cells, producers, repeats)
    fps = {fid: v for fid, v in verdicts.items()
           if v.leak and fid.startswith("struct:")}
    return {"fidelity": fidelity, "producers": producers,
            "struct_fingerprints": fps,
            "muxer_fail": any(k in fps for k in _MUXER_KEYS),
            "encoder_fail": any(k in fps for k in _ENCODER_KEYS)}


def evaluate_cell(fidelity: str, sources: dict, tmpdir: str):
    """One A2 cell, naming the channel and the surviving keys.

    Reported per key rather than as a channel verdict, because a channel verdict
    alone reads the same whether a tier collapsed most of it or none of it — the
    correction DOCX's F2 cell had to make.
    """
    from tests.harness.contract import Cell, Leak, Locus, V
    r = run_condition(fidelity, sources, tmpdir)
    absent = "" if mc.HAVE_AVFOUNDATION else (
        " Peer set is ffmpeg-only here: the AVFoundation muxer is macOS-only and "
        "was unavailable, so the container channel was measured against one "
        "implementation's options rather than against a second implementation.")

    if not (r["muxer_fail"] or r["encoder_fail"]):
        return Cell(adversary="A2", fidelity=fidelity, verdict=V.PASS,
                    reason=f"no structural feature separates the producers. "
                           f"Peer set = {', '.join(r['producers'])}.{absent}")

    leaks = [Leak("source_fingerprint", Locus("structural", feature_id=fid),
                  f"producer (across {v.n_between})",
                  f"{fid} constant within producer, differs across")
             for fid, v in sorted(r["struct_fingerprints"].items())]
    by_channel = []
    if r["muxer_fail"]:
        keys = sorted(k for k in r["struct_fingerprints"] if k in _MUXER_KEYS)
        by_channel.append("muxer: " + ", ".join(keys))
    if r["encoder_fail"]:
        keys = sorted(k for k in r["struct_fingerprints"] if k in _ENCODER_KEYS)
        by_channel.append("size: " + ", ".join(keys))
    return Cell(adversary="A2", fidelity=fidelity, verdict=V.FAIL,
                reason=("producer still identifiable. Leaking by channel — "
                        + "; ".join(by_channel)
                        + f". Peer set = {', '.join(r['producers'])}.{absent}"),
                leaks=leaks)


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        sources = build_sources(td, repeats=3)
        print(f"producers: {', '.join(sources)}")
        for fid in ("raw", "F1"):
            r = run_condition(fid, sources, td)
            leaking = sorted(r["struct_fingerprints"])
            print(f"\n{fid}: muxer_fail={r['muxer_fail']} "
                  f"encoder_fail={r['encoder_fail']}")
            for key in leaking:
                print(f"    leaks {key}")
            closed = [k for k in _MUXER_KEYS if k not in r["struct_fingerprints"]]
            if fid != "raw":
                print(f"    closed: {', '.join(closed) or 'nothing'}")


if __name__ == "__main__":
    main()
