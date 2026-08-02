"""E-M4A — muxer-layout peer-set experiment.

An M4A carries two producers, not one: the **encoder** that produced the coded audio,
and the **muxer** that arranged it into boxes. They fingerprint independently, and
our tiers reach them differently — which is exactly what this experiment has to keep
separate rather than blur into a single verdict.

  * Muxer layout — brand, top-level box order, whether `moov` precedes `mdat`
    (faststart), how much `free` slack is left. Content-independent, and normalised
    by the F2 re-mux at zero cost, since the coded audio is copied.
  * Coded audio — the AAC bitstream itself. Copying it preserves the encoder's
    fingerprint by definition, so F2 cannot touch it; that needs a canonical
    re-encode (F3), which is not built yet. The peer set therefore reports the two
    separately, and the matrix records the audio-side residual honestly instead of
    letting an F2 pass imply more than it delivers.
"""
from __future__ import annotations

import os
import tempfile

from tests.harness.oracle import fields, variance
from tests.harness.plugins.m4a import M4aPlugin
from tests.scrub import m4a_corpus as mc

# Layout features: the muxer's choices, independent of the audio.
_MUXER_KEYS = ("struct:brand", "struct:top_level_order", "struct:moov_before_mdat",
               "struct:free_bytes", "struct:box_inventory")
# The coded audio: the ENCODER's fingerprint, out of reach of a stream copy.
_ENCODER_KEYS = ("struct:audio_digest",)


def build_sources(tmpdir: str, repeats: int = 3) -> dict:
    return mc.producers(tmpdir, repeats=repeats)


def run_condition(fidelity: str, sources: dict, tmpdir: str) -> dict:
    from src.scrub import cli
    plugin = M4aPlugin()
    producers = list(sources)
    repeats = list(range(min(len(v) for v in sources.values())))
    cells = {}
    for prod, paths in sources.items():
        for r, src in enumerate(paths):
            if fidelity == "raw":
                target = src
            else:
                target = os.path.join(tmpdir, f"{prod}_{r}_{fidelity}.m4a")
                cli.scrub_file(src, target, fidelity)
            cells[(prod, r)] = fields.extract(target, plugin, "F2")
    verdicts = variance.decompose(cells, producers, repeats)
    fps = {fid: v for fid, v in verdicts.items()
           if v.leak and fid.startswith("struct:")}
    return {"fidelity": fidelity, "producers": producers,
            "struct_fingerprints": fps,
            "muxer_fail": any(k in fps for k in _MUXER_KEYS),
            "encoder_fail": any(k in fps for k in _ENCODER_KEYS)}


# Producers that encoded the SAME first-generation audio and differ only in how it
# was muxed. Anything that still separates THESE is a container-level leak; anything
# that separates them from `mux_faststart_192` may simply be the trace of a different
# original encode, which is a residual rather than a container failure. Keeping the
# two apart is what stops one number from meaning two different things.
SAME_AUDIO_PRODUCERS = ("mux_plain", "mux_faststart")


def same_audio_collapse(fidelity: str, sources: dict, tmpdir: str) -> bool:
    """Do the same-source producers become byte-identical at this tier?"""
    from src.scrub import cli
    from tests.harness.plugins.m4a import M4aPlugin
    plugin = M4aPlugin()
    digests = set()
    for prod in SAME_AUDIO_PRODUCERS:
        src = sources[prod][0]
        if fidelity == "raw":
            target = src
        else:
            target = os.path.join(tmpdir, f"collapse_{prod}_{fidelity}.m4a")
            cli.scrub_file(src, target, fidelity)
        f = plugin.structural_features(target)
        digests.add((f.get("audio_digest"), f.get("top_level_order"),
                     f.get("free_bytes")))
    return len(digests) == 1


def evaluate_cell(fidelity: str, sources: dict, tmpdir: str):
    from tests.harness.contract import Cell, Leak, Locus, V
    r = run_condition(fidelity, sources, tmpdir)
    if r["muxer_fail"] or r["encoder_fail"]:
        leaks = [Leak("source_fingerprint", Locus("structural", feature_id=fid),
                      f"producer (across {v.n_between})",
                      f"{fid} constant within producer, differs across")
                 for fid, v in r["struct_fingerprints"].items()]
        collapsed = same_audio_collapse(fidelity, sources, tmpdir)
        which = []
        if r["muxer_fail"]:
            which.append("muxer layout (box order / brand / free slack)")
        if r["encoder_fail"]:
            if fidelity == "F3" and collapsed:
                which.append(
                    "the coded audio — but ONLY where the source's own first-"
                    "generation encode differed (a 192 kbps original vs a 128 kbps "
                    "one). Producers that encoded identical audio and differed only "
                    "in muxing collapse to BYTE-IDENTICAL output here. What remains "
                    "is the primary-encoding trace: re-encoding cannot undo what the "
                    "first encoder already discarded, the audio analogue of Sorell's "
                    "primary-quantization residual in JPEG. Measuring whether that "
                    "trace actually identifies a producer needs an audio-space "
                    "classifier (the MP3 E-ENGINE treatment), which M4A does not "
                    "have yet — so it is recorded as an open residual, not a pass")
            else:
                which.append("the coded audio — the AAC encoder's own fingerprint, "
                             "which a stream copy preserves by definition; reaching "
                             "it requires the canonical re-encode at F3")
        return Cell("A2", fidelity, V.FAIL, leaks=leaks,
                    reason="source fingerprint survives in " + "; ".join(which) +
                           ". Features: " + ", ".join(sorted(r["struct_fingerprints"])))
    return Cell("A2", fidelity, V.PASS,
                reason="muxer layout and coded audio both normalized; peer set = "
                       + ", ".join(r["producers"]))


def main() -> None:
    tmpdir = tempfile.mkdtemp(prefix="e_m4a_")
    sources = build_sources(tmpdir)
    print(f"E-M4A peer set: {list(sources)} x "
          f"{min(len(v) for v in sources.values())} each\n")
    for fid in ("raw", "F1", "F2"):
        r = run_condition(fid, sources, tmpdir)
        fps = sorted(r["struct_fingerprints"])
        print(f"[{fid:3}] separating producers: {fps or 'none'}")
        print(f"      muxer {'FAIL' if r['muxer_fail'] else 'pass'} | "
              f"encoder {'FAIL' if r['encoder_fail'] else 'pass'}")


if __name__ == "__main__":
    main()
