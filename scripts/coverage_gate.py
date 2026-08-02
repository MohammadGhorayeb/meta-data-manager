"""Coverage gate that a fast-moving codebase cannot quietly erode.

The problem with a single global floor here: this project lands each new format
as scaffolding first and wires its tests second. Every time a skeleton appears
the global number drops, CI goes red for work done in the right order, and the
floor gets nudged down to compensate. Do that three times and the gate means
nothing — the number only ever ratchets downward.

So the gate is split in two:

  * **Exercised code** — every file the tests touch at all — must stay above the
    floor. This is the real quality bar, and it does NOT move when new,
    untested scaffolding lands.
  * **Untouched files** — zero coverage, no test reaches them — are excluded
    from the floor but reported loudly, by name and statement count, in the job
    log and in the QA report. They are visible debt, not hidden debt.

The honest limitation, stated rather than glossed: a file that loses its last
test drops to 0% and leaves the gated set instead of failing it. That is the
price of not blocking scaffolding. It is mitigated by the count and names being
printed every run, by --max-untouched, and by the evidence job, which
independently requires any format that publishes results to be re-measurable.

    python scripts/coverage_gate.py --coverage-json coverage.json --floor 82
"""
from __future__ import annotations

import argparse
import json


def combined_percent(summaries: list[dict]) -> float:
    """Line + branch coverage, the same measure `coverage report` prints."""
    covered = sum(s["covered_lines"] for s in summaries)
    total = sum(s["num_statements"] for s in summaries)
    covered += sum(s.get("covered_branches", 0) for s in summaries)
    total += sum(s.get("num_branches", 0) for s in summaries)
    return 100.0 * covered / total if total else 100.0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Gate on the coverage of tested code.")
    ap.add_argument("--coverage-json", default="coverage.json")
    ap.add_argument("--floor", type=float, required=True,
                    help="minimum coverage across files the tests actually reach")
    ap.add_argument("--max-untouched", type=int, default=None,
                    help="fail if more than N files have zero coverage")
    ap.add_argument("--out", default="coverage-gate.json")
    args = ap.parse_args(argv)

    try:
        with open(args.coverage_json, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"::error::cannot read {args.coverage_json}: {exc}")
        return 1

    files = data.get("files", {})
    if not files:
        print("::error::coverage data contains no files — did the tests run?")
        return 1

    exercised, untouched = [], []
    for path, entry in sorted(files.items()):
        s = entry["summary"]
        if s["num_statements"] == 0:
            continue
        (untouched if s["percent_covered"] == 0 else exercised).append((path, s))

    gated = combined_percent([s for _, s in exercised])
    overall = float(data.get("totals", {}).get("percent_covered", 0.0))
    debt = sum(s["num_statements"] for _, s in untouched)

    payload = {
        "gated_percent": round(gated, 2),
        "overall_percent": round(overall, 2),
        "floor": args.floor,
        "passed": gated >= args.floor,
        "exercised_files": len(exercised),
        "untouched_files": [{"path": p, "statements": s["num_statements"]}
                            for p, s in untouched],
        "untouched_statements": debt,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    for path, s in untouched:
        print(f"::warning file={path}::No test reaches this file "
              f"({s['num_statements']} statements). It is excluded from the "
              f"coverage floor until a test does — visible debt, not hidden.")

    print(f"Tested code:  {gated:.1f}%  across {len(exercised)} files "
          f"(floor {args.floor:.0f}%)")
    print(f"Whole tree:   {overall:.1f}%  — {debt} statements in "
          f"{len(untouched)} untouched file(s) sit outside the gate")

    failed = False
    if gated < args.floor:
        print(f"::error::Coverage of tested code is {gated:.1f}%, below the "
              f"{args.floor:.0f}% floor. This counts only files the tests "
              f"already reach, so new untested scaffolding is not the cause.")
        failed = True
    if args.max_untouched is not None and len(untouched) > args.max_untouched:
        print(f"::error::{len(untouched)} files have no test at all, more than "
              f"the {args.max_untouched} allowed. Wire them up or raise the "
              f"limit deliberately.")
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
