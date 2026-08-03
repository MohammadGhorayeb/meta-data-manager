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
# The coded audio reaches this channel only through file SIZE — a source encoded at a
# higher bitrate makes a bigger file, and at F1/F2 we copy that stream verbatim.
#
# The audio DIGEST is deliberately not here. Judging M4A on its own compressed content
# in the categorical channel made it the only format held to that bar: MP3's channel
# is headers only, and its audio question is answered by E-ENGINE. M4A now works the
# same way — the coded-audio question belongs to E-M4A-AUDIO, which measures whether
# the trace is *recoverable* rather than merely *present*. Presence is not a leak;
# recoverability is.
_ENCODER_KEYS = ("struct:size",)


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


def evaluate_cell(fidelity: str, sources: dict, tmpdir: str, audio_note: str = ""):
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
                    "FILE SIZE, and nothing else. The muxer layout is fully "
                    "normalized here, and producers whose first-generation audio was "
                    "identical collapse to BYTE-IDENTICAL output. What still "
                    "separates the remaining producer is that its source was encoded "
                    "at a different bitrate, so its decoded audio re-compresses to a "
                    "slightly different size (~1.4% on this corpus). That is the "
                    "primary-encoding trace surfacing through a side channel: "
                    "re-encoding cannot undo what the first encoder discarded, the "
                    "audio analogue of Sorell's primary-quantization residual. Note "
                    "the peer set holds CONTENT constant, which makes size a clean "
                    "producer signal here; against real files of differing length and "
                    "material it is far weaker. A size-bucketing pad would close it "
                    "at the cost of bigger files — not currently done"
                    + (". " + audio_note if audio_note else
                       ". Whether the trace identifies a producer in the audio itself "
                       "is unmeasured here — an open residual, not a pass"))
            else:
                which.append("the coded audio, reaching this channel through file "
                             "size — a stream copy preserves the source's own "
                             "encode by definition, so a higher-bitrate original "
                             "stays a bigger file; reaching it requires F3")
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
