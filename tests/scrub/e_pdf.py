"""E-PDF — which producer channel identifies the application that made a PDF?

The same document, printed by five different producers. With the metadata gone, does
the *structure* still say which one made it — and if so, **which part** of the
structure?

That second question is the whole point of running this before F2 is designed. PDF
carries two producers in one file:

* the **serializer** — object numbering and order, xref style, the binary header
  comment, `/ID`, whether `/Length` is direct, object streams, linearisation. Our own
  writer decides all of it, so F1 should already collapse this channel to one value.
* the **layout engine** — the content-stream operators, how much decimal precision
  they carry, which fonts were subset, and where each glyph sits. F1 does not touch
  any of it.

Averaging the two would produce a single "A2 fails" verdict that tells M4 nothing
about what to normalise. Reporting them separately says exactly which work remains —
and W5's warning is that only *part* of the layout channel is normalisable at all:
number formatting and operator style can be canonicalised losslessly, but glyph
geometry cannot be changed without re-typesetting the page.

**File size is reported as its own channel**, not folded into either. It is a real
producer signal here precisely because the peer corpus holds the source document
constant, and hiding it would be hiding evidence — but it is a side channel rather
than a statement about how the bytes or the page were built, and
`oracle/fields.py:99` injects it before the plugin is consulted anyway.

Controls first, as Phase 2 established: if the attack cannot tell the producers apart
on an **untouched** file, its failure on a scrubbed one proves nothing. That is
asserted per channel, because a peer set can easily separate producers one way and
not another — and on a machine with only the two synthetics available, that is
exactly what would happen.
"""
from __future__ import annotations

import os
import tempfile

from tests.harness.oracle import fields, variance
from tests.harness.plugins.pdf import LAYOUT_KEYS, SERIALIZER_KEYS, PdfPlugin
from tests.scrub import pdf_corpus as pc

# The third channel: a side effect of the other two, named rather than averaged in.
SIZE_KEYS = ("struct:size",)

CHANNELS = {"serializer": SERIALIZER_KEYS, "layout": LAYOUT_KEYS, "size": SIZE_KEYS}


def build_sources(tmpdir: str, repeats: int = 3) -> dict:
    return pc.producers(tmpdir, repeats=repeats)


def run_condition(fidelity: str, sources: dict, tmpdir: str) -> dict:
    """Measure one condition. `fidelity="raw"` is the untouched control."""
    from src.scrub import cli

    plugin = PdfPlugin()
    producers = list(sources)
    repeats = list(range(min(len(v) for v in sources.values())))
    cells = {}
    for prod, paths in sources.items():
        for r, src in enumerate(paths):
            if fidelity == "raw":
                target = src
            else:
                target = os.path.join(tmpdir, f"{prod}_{r}_{fidelity}.pdf")
                cli.scrub_file(src, target, fidelity)
            cells[(prod, r)] = fields.extract(target, plugin, "F2")

    verdicts = variance.decompose(cells, producers, repeats)
    leaking = {fid: v for fid, v in verdicts.items()
               if v.leak and fid.startswith("struct:")}
    by_channel = {name: sorted(k for k in leaking if k in keys)
                  for name, keys in CHANNELS.items()}
    # A key the plugin emits that no channel claims would go unreported, which is the
    # one way this experiment could quietly under-report a leak.
    claimed = {k for keys in CHANNELS.values() for k in keys}
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
    """Per channel: did the attack separate producers on the UNTOUCHED files?

    Per channel and not overall, because a peer set can separate producers one way
    and not another. On a machine with only the two synthetics, `size` and the
    serializer channel separate while the layout channel may not — and calling the
    layout result a pass there would be reporting an unmeasured cell as clean.
    """
    raw = results["raw"]["by_channel"]
    return {name: bool(raw.get(name)) for name in CHANNELS}


def evaluate_cell(fidelity: str, sources: dict, tmpdir: str, raw: dict | None = None):
    from tests.harness.contract import Cell, Leak, Locus, V

    result = run_condition(fidelity, sources, tmpdir)
    raw = raw or run_condition("raw", sources, tmpdir)
    valid = controls_valid({"raw": raw})
    peers = ", ".join(result["producers"])

    if not any(valid.values()):
        return Cell("A2", fidelity, V.NOT_TESTED,
                    reason=f"controls invalid: the peer set ({peers}) does not "
                           "separate producers on any channel even unscrubbed, so "
                           "nothing can be concluded about the scrubbed files")

    channels = result["by_channel"]
    if result["a2_fail"]:
        leaks = [Leak("source_fingerprint", Locus("structural", feature_id=fid),
                      f"producer (across {len(result['producers'])})",
                      f"{fid} constant within producer, differs across")
                 for fid in result["leaking"]]
        named = "; ".join(f"{name}: {', '.join(keys)}"
                          for name, keys in channels.items() if keys)
        untested = [n for n, ok in valid.items() if not ok]
        note = (f" Channels not separable even unscrubbed, so untested here: "
                f"{', '.join(untested)}." if untested else "")
        return Cell("A2", fidelity, V.FAIL, leaks=leaks,
                    reason=f"producer still identifiable. Leaking by channel — "
                           f"{named}. Peer set = {peers}.{note}")

    return Cell("A2", fidelity, V.PASS,
                reason=f"no structural feature separates the producers; peer set = "
                       f"{peers}. Controls valid on: "
                       f"{', '.join(n for n, ok in valid.items() if ok)}.")


def main() -> None:
    tmpdir = tempfile.mkdtemp(prefix="e_pdf_")
    sources = build_sources(tmpdir)
    absent = [n for n, ok in pc.available_producers().items() if not ok]
    print(f"E-PDF peer set: {list(sources)} x "
          f"{min(len(v) for v in sources.values())} each")
    if absent:
        print(f"  not available on this machine (reported as untested, never as "
              f"clean): {', '.join(absent)}")
    print()

    raw = run_condition("raw", sources, tmpdir)
    for fid in ("raw", "F1", "F2"):
        r = raw if fid == "raw" else run_condition(fid, sources, tmpdir)
        print(f"[{fid:3}] A2 {'FAIL' if r['a2_fail'] else 'PASS'}")
        for name in ("serializer", "layout", "size", "unclassified"):
            keys = r["by_channel"].get(name) or []
            shown = ", ".join(k.removeprefix("struct:") for k in keys)
            print(f"        {name:<13} {shown or '— nothing separates producers —'}")
    valid = controls_valid({"raw": raw})
    print(f"\ncontrols (does the untouched file give the producer away?): "
          f"{ {k: 'yes' if v else 'NO' for k, v in valid.items()} }")


if __name__ == "__main__":
    main()
