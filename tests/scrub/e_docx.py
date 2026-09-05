"""E-DOCX (W13/M11) — does the package still name its producer, and which half?

The E-PDF design, in another format. A DOCX has two producers in one file — the
**packager** that laid out the ZIP and the **document model** that wrote the
WordprocessingML — so the cell names which one leaked rather than averaging them.

Why that matters here specifically: F1 rewrites the container through our own writer
but edits the XML only by deletion, so the prediction is that the packager channel
closes outright while the model channel does not. If that holds, F2's scope is
exactly the model channel, and it is scoped from a measurement rather than a guess —
the same reason M3 ran before PDF's F2 was designed.

**Controls are checked per channel.** A classifier that cannot separate producers on
an *untouched* file proves nothing about a scrubbed one, and a peer set can easily
separate them one way and not another. On a machine with only the synthetic producer
available, that is exactly what would happen, and calling the result a pass there
would report an unmeasured cell as clean.

Run:  ./.venv/bin/python -m tests.scrub.e_docx
"""
from __future__ import annotations

import os
import tempfile

from tests.harness.oracle import fields, variance
from tests.harness.plugins.docx import MODEL_KEYS, PACKAGER_KEYS, DocxPlugin
from tests.scrub import docx_corpus as C

SIZE_KEYS = ("struct:size",)

CHANNELS = {"packager": PACKAGER_KEYS, "model": MODEL_KEYS, "size": SIZE_KEYS}


def build_sources(tmpdir: str, repeats: int = 3) -> dict:
    return C.producers_matched(tmpdir, repeats=repeats)


def run_condition(fidelity: str, sources: dict, tmpdir: str) -> dict:
    """Measure one condition. `fidelity="raw"` is the untouched control."""
    from src.scrub import cli

    plugin = DocxPlugin()
    producers = list(sources)
    repeats = list(range(min(len(v) for v in sources.values())))
    cells = {}
    for prod, paths in sources.items():
        for r, src in enumerate(paths):
            if fidelity == "raw":
                target = src
            else:
                target = os.path.join(tmpdir, f"{prod}_{r}_{fidelity}.docx")
                cli.scrub_file(src, target, fidelity)
            cells[(prod, r)] = fields.extract(target, plugin, "F1")

    verdicts = variance.decompose(cells, producers, repeats)
    leaking = {fid: v for fid, v in verdicts.items()
               if v.leak and fid.startswith("struct:")}
    by_channel = {name: sorted(k for k in leaking if k in keys)
                  for name, keys in CHANNELS.items()}
    claimed = {k for keys in CHANNELS.values() for k in keys}
    # A key the plugin emits that no channel claims would go unreported — the one way
    # this experiment could quietly under-report a leak.
    by_channel["unclassified"] = sorted(k for k in leaking if k not in claimed)
    return {
        "fidelity": fidelity,
        "producers": producers,
        "n_producers": len(producers),
        "leaking": sorted(leaking),
        "by_channel": by_channel,
        "a2_fail": bool(leaking),
    }


def controls_valid(results: dict) -> dict:
    """Per channel: did the attack separate producers on the UNTOUCHED files?"""
    raw = results["raw"]["by_channel"]
    return {name: bool(raw.get(name)) for name in CHANNELS}


def evaluate_cell(fidelity: str, sources: dict, tmpdir: str, raw: dict | None = None):
    from tests.harness.contract import Cell, Leak, Locus, V

    result = run_condition(fidelity, sources, tmpdir)
    raw = raw or run_condition("raw", sources, tmpdir)
    valid = controls_valid({"raw": raw})
    peers = ", ".join(result["producers"])
    absent = [n for n, ok in C.matched_producers_available().items() if not ok]
    gap = (f" Not in the peer set (cannot render our document): "
           f"{', '.join(absent)}." if absent else "")

    if not any(valid.values()):
        return Cell("A2", fidelity, V.NOT_TESTED,
                    reason=f"controls invalid: the peer set ({peers}) does not "
                           f"separate producers on any channel even unscrubbed, so "
                           f"nothing can be concluded about the scrubbed files.{gap}")

    if result["a2_fail"]:
        leaks = [Leak("source_fingerprint", Locus("structural", feature_id=fid),
                      f"producer (across {len(result['producers'])})",
                      f"{fid} constant within producer, differs across")
                 for fid in result["leaking"]]
        named = "; ".join(f"{name}: {', '.join(keys)}"
                          for name, keys in result["by_channel"].items() if keys)
        untested = [n for n, ok in valid.items() if not ok]
        note = (f" Channels not separable even unscrubbed, so untested here: "
                f"{', '.join(untested)}." if untested else "")
        return Cell("A2", fidelity, V.FAIL, leaks=leaks,
                    reason=f"producer still identifiable. Leaking by channel — "
                           f"{named}. Peer set = {peers}.{note}{gap}")

    return Cell("A2", fidelity, V.PASS,
                reason=f"no structural feature separates the producers; peer set = "
                       f"{peers}. Controls valid on: "
                       f"{', '.join(n for n, ok in valid.items() if ok)}.{gap}")


def main() -> None:
    tmpdir = tempfile.mkdtemp(prefix="e_docx_")
    sources = build_sources(tmpdir)
    absent = [n for n, ok in C.matched_producers_available().items() if not ok]
    print(f"E-DOCX peer set: {list(sources)} x "
          f"{min(len(v) for v in sources.values())} each")
    if absent:
        print(f"  cannot render our document, reported as untested rather than "
              f"clean: {', '.join(absent)}")
    print()

    raw = run_condition("raw", sources, tmpdir)
    for fid in ("raw", "F1", "F2", "F3"):
        r = raw if fid == "raw" else run_condition(fid, sources, tmpdir)
        print(f"[{fid:3}] A2 {'FAIL' if r['a2_fail'] else 'PASS'}")
        for name in ("packager", "model", "size", "unclassified"):
            keys = r["by_channel"].get(name) or []
            shown = ", ".join(k.removeprefix("struct:") for k in keys)
            print(f"        {name:<13} {shown or '— nothing separates producers —'}")
    valid = controls_valid({"raw": raw})
    print(f"\ncontrols (does the untouched file give the producer away?): "
          f"{ {k: ('yes' if v else 'NO') for k, v in valid.items()} }")


if __name__ == "__main__":
    main()
