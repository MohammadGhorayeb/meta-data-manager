"""Confirm the published Pareto matrices still describe reality.

This project's standing rule is *no claims without empirical validation*. The
matrices in `tests/harness/results/` are where those claims live, and the README
and the QA report both read from them. If the code's behaviour changes and the
matrices are not regenerated, the project silently starts publishing a claim it
can no longer back — which is the one failure mode that matters most here.

So: re-measure every format from scratch, and compare against what is committed.

What is compared, and what deliberately is not
----------------------------------------------
Only the **verdict grid** — the set of (adversary, fidelity) -> verdict triples.
That is the actual scientific claim. Everything else in the JSON is run-local
noise that would make a naive `git diff` fail on every single run:

  * `generated_at`  — a timestamp, different every time;
  * `evidence_dir`  — a path into a per-run temp directory;
  * `reason` prose  — depends on which optional encoders exist on the machine
                      (the MP3 A2@F3 note names the peer set, and `shineenc` is
                      not available on the CI runner);
  * `noise_floor`   — a measured float that moves within tolerance.

Cells the run could not actually measure (verdict `not_tested`, e.g. because an
optional external encoder is missing) are reported as *unmeasured* rather than
as differences. A missing optional binary must not turn the build red for a
claim it never had the means to check.

    python scripts/check_evidence.py --out evidence.json

Exits non-zero if a published verdict no longer matches the measured one.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import tempfile
import traceback

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

# format id -> the module whose build_doc() re-measures it from scratch.
GENERATORS = {
    "jpeg": "tests.scrub.gen_matrix",
    "png": "tests.scrub.gen_matrix_png",
    "mp3": "tests.scrub.gen_matrix_mp3",
}

RESULTS_DIR = os.path.join(REPO, "tests", "harness", "results")

# A verdict the run could not establish. Never counted as a mismatch.
UNMEASURED = "not_tested"


def grid(doc: dict) -> dict[tuple[str, str], str]:
    """Reduce a matrix document to just its claims."""
    return {(c["adversary"], c["fidelity"]): c["verdict"]
            for c in doc.get("cells", [])}


def published_path(fmt: str, tool_name: str) -> str:
    return os.path.join(RESULTS_DIR, f"{fmt}_{tool_name}.json")


def check_format(fmt: str, module_name: str) -> dict:
    """Re-measure one format and diff its verdict grid against the committed one."""
    result: dict = {"format": fmt, "differences": [], "unmeasured": [],
                    "error": None, "checked": 0}
    try:
        module = importlib.import_module(module_name)
    except Exception:
        result["error"] = f"could not import {module_name}\n{traceback.format_exc()}"
        return result

    tool_name = getattr(module, "TOOL", {}).get("name", "irreversible_scrubber")
    path = published_path(fmt, tool_name)
    if not os.path.exists(path):
        result["error"] = f"no published matrix at {os.path.relpath(path, REPO)}"
        return result

    with open(path, encoding="utf-8") as f:
        published = grid(json.load(f))

    # build_doc() rather than main(): main() would overwrite the very file we
    # are checking against, which would make the comparison vacuous.
    try:
        with tempfile.TemporaryDirectory(prefix=f"{fmt}_evidence_") as tmp:
            measured = grid(module.build_doc(tmp))
    except Exception:
        result["error"] = (f"re-measuring {fmt} raised:\n{traceback.format_exc()}")
        return result

    for key, want in sorted(published.items()):
        adversary, fidelity = key
        got = measured.get(key)
        if got is None:
            result["unmeasured"].append({
                "format": fmt, "adversary": adversary, "fidelity": fidelity,
                "published": want, "measured": "absent",
                "note": "this run produced no cell for that combination"})
        elif got == UNMEASURED and want != UNMEASURED:
            result["unmeasured"].append({
                "format": fmt, "adversary": adversary, "fidelity": fidelity,
                "published": want, "measured": got,
                "note": "an optional external tool was unavailable here"})
        elif got != want:
            result["differences"].append({
                "format": fmt, "adversary": adversary, "fidelity": fidelity,
                "published": want, "measured": got})
        else:
            result["checked"] += 1
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="evidence.json",
                    help="where to write the machine-readable result")
    ap.add_argument("--format", action="append", dest="formats", default=None,
                    choices=sorted(GENERATORS), help="limit to one format")
    args = ap.parse_args(argv)

    wanted = args.formats or sorted(GENERATORS)
    results = [check_format(f, GENERATORS[f]) for f in wanted]

    differences = [d for r in results for d in r["differences"]]
    unmeasured = [u for r in results for u in r["unmeasured"]]
    errors = [{"format": r["format"], "error": r["error"]}
              for r in results if r["error"]]
    checked = sum(r["checked"] for r in results)

    payload = {
        "ok": not differences and not errors,
        "formats": wanted,
        "confirmed": checked,
        "differences": differences,
        "unmeasured": unmeasured,
        "errors": errors,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    for e in errors:
        print(f"::error::evidence check for {e['format']} failed: {e['error']}")
    for d in differences:
        print(f"::error::{d['format'].upper()} {d['adversary']}@{d['fidelity']}: "
              f"published '{d['published']}' but this run measured "
              f"'{d['measured']}' — regenerate the matrix or fix the regression.")
    for u in unmeasured:
        print(f"::warning::{u['format'].upper()} {u['adversary']}@{u['fidelity']} "
              f"could not be re-measured here ({u['note']}); "
              f"published verdict '{u['published']}' left unchallenged.")

    print(f"Evidence: {checked} published verdict(s) re-confirmed, "
          f"{len(differences)} mismatch(es), {len(unmeasured)} unmeasured "
          f"-> {args.out}")
    return 1 if payload["ok"] is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
