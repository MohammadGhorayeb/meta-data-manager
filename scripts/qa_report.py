"""Professional QA dashboard generator (non-technical friendly).

Turns raw pytest results (JUnit XML) + the scrub-flow report + the Pareto
matrices into:
  * a fancy self-contained HTML dashboard (for GitHub Pages / artifact), and
  * a Markdown summary (for the Actions job summary and PR comments).

Tests are grouped into plain-English QA areas (Metadata Removal, Picture
Preserved, Made Untraceable, Cannot Be Recovered, File Stays Valid, Tool
Behaviour) so a non-technical reader sees what was checked and what passed.
"""
from __future__ import annotations

import argparse
import html
import importlib.util
import os
import sys
import xml.etree.ElementTree as ET

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import scripts.scrub_flow_report as flow  # noqa: E402  (after sys.path)

# --------------------------------------------------------------------------- #
# QA categories: plain-English areas + which tests belong to each (by nodeid).
# Order = display order; first matching rule wins.
# --------------------------------------------------------------------------- #
CATEGORIES = [
    ("metadata", "🧹", "Metadata Removal",
     "Hidden data — GPS location, camera model, timestamps, embedded thumbnails, "
     "comments — is stripped so it's gone for good.",
     ["removes_every_metadata", "removes_metadata", "removes_text", "no_secret",
      "residual", "no_leak", "_a1_", "leak", "strip", "canonicaliz",
      "present_before", "comment_leak"]),
    ("content", "🖼️", "Picture Preserved",
     "The image still looks identical (pixel-perfect for lossless modes) after "
     "cleaning.",
     ["preserves_pixel", "content_preserved", "lossless", "preserves_alpha",
      "preserves_palette", "preserves_quantization", "phash", "pixel",
      "preserved", "bit_exact", "entropy_bytes", "dimensions"]),
    ("fingerprint", "🕵️", "Made Untraceable",
     "You can't tell which app or phone produced the file — the compressor "
     "fingerprint is erased.",
     ["e3_dqt", "fingerprint_guard", "peerset_a2", "png_a2", "fingerprint",
      "normalizes_dqt", "normalizes_deflate", "dqt", "deflate", "a2"]),
    ("irreversibility", "🔒", "Cannot Be Recovered",
     "Forensic tools cannot bring back anything that was removed.",
     ["forensic_recovery", "recover"]),
    ("format", "🧩", "File Stays Valid",
     "The file structure and checksums are correct, so it still opens everywhere.",
     ["segment", "chunk", "crc", "walk", "assemble", "standard", "toy_format",
      "diff", "variance", "floor", "exit_gate"]),
    ("tooling", "⚙️", "Tool Behaviour",
     "The command line, automatic file-type detection, and fail-safe behaviour "
     "all work correctly.",
     ["cli", "dispatch", "matrix", "exit", "integration_exiftool", "determinist",
      "subprocess_scrubber"]),
    ("other", "✅", "Other Checks", "Additional internal quality checks.", []),
]


def parse_junit(path: str):
    """Return list of (nodeid, status) where status in pass|fail|skip."""
    if not os.path.exists(path):
        return []
    tree = ET.parse(path)
    cases = []
    for tc in tree.iter("testcase"):
        nid = f"{tc.get('classname', '')}.{tc.get('name', '')}".lower()
        status = "pass"
        for child in tc:
            if child.tag in ("failure", "error"):
                status = "fail"
            elif child.tag == "skipped":
                status = "skip"
        cases.append((nid, status))
    return cases


# Match specific areas before broad ones (metadata's "leak"/"no_secret" are broad,
# so forensic-recovery and fingerprint tests must be matched first). Display still
# follows CATEGORIES order.
_MATCH_ORDER = ["irreversibility", "fingerprint", "content", "tooling",
                "format", "metadata", "other"]


def categorize(cases):
    buckets = {key: {"pass": 0, "fail": 0, "skip": 0, "fails": []}
               for key, *_ in CATEGORIES}
    pats_by_key = {key: pats for key, _, _, _, pats in CATEGORIES}
    rules = [(key, pats_by_key[key]) for key in _MATCH_ORDER]
    for nid, status in cases:
        placed = "other"
        for key, pats in rules:
            if any(p in nid for p in pats):
                placed = key
                break
        buckets[placed][status] += 1
        if status == "fail":
            buckets[placed]["fails"].append(nid)
    out = []
    for key, icon, title, desc, _ in CATEGORIES:
        b = buckets[key]
        total = b["pass"] + b["fail"] + b["skip"]
        if total == 0:
            continue
        out.append({"key": key, "icon": icon, "title": title, "desc": desc,
                    "passed": b["pass"], "failed": b["fail"], "skipped": b["skip"],
                    "total": total, "fails": b["fails"]})
    return out


def load_capabilities():
    """Plain-language capability rows derived from the Pareto matrices."""
    import json
    rows = []
    fmap = {"jpeg": "JPEG (photos)", "png": "PNG (graphics / screenshots)"}
    for fmt, label in fmap.items():
        p = os.path.join(REPO, "tests", "harness", "results",
                         f"{fmt}_irreversible_scrubber.json")
        if not os.path.exists(p):
            continue
        doc = json.load(open(p))
        cells = {(c["adversary"], c["fidelity"]): c["verdict"] for c in doc["cells"]}
        a1 = [cells.get(("A1", f)) for f in ("F1", "F2", "F3")]
        a2 = [cells.get(("A2", f)) for f in ("F1", "F2", "F3")]
        removes = "✅ Yes" if any(v == "pass" for v in a1) else "❌ No"
        keeps = "✅ Yes"  # F1/F2 are lossless by construction
        # untraceable: which fidelity first passes A2
        untr = "❌ No"
        for f, v in zip(("F1", "F2", "F3"), a2):
            if v == "pass":
                cost = {"F2": "no quality loss" if fmt == "png" else "lossless",
                        "F3": "tiny quality cost", "F1": "no quality loss"}[f]
                untr = f"✅ Yes ({cost})"
                break
        rows.append((label, removes, keeps, untr))
    return rows


# --------------------------------------------------------------------------- #
# Markdown summary (Actions job summary + PR comment)
# --------------------------------------------------------------------------- #
def render_markdown(cats, flow_md, caps, meta):
    total_pass = sum(c["passed"] for c in cats)
    total_fail = sum(c["failed"] for c in cats)
    total_skip = sum(c["skipped"] for c in cats)
    ok = total_fail == 0
    banner = (f"## {'🟢 ALL QUALITY CHECKS PASSED' if ok else '🔴 SOME CHECKS FAILED'}"
              f"  —  {total_pass} passed"
              + (f", {total_fail} failed" if total_fail else "")
              + (f", {total_skip} skipped" if total_skip else ""))
    lines = [
        "# 🛡️ Metadata Scrubber — Quality Report",
        "",
        "_A tool that permanently removes hidden data from images. Every check "
        "below runs automatically on each change._",
        "",
        banner,
        "",
        "### What we checked",
        "",
        "| Area | Result | Checks | What it means |",
        "|---|---|---|---|",
    ]
    for c in cats:
        status = "✅ Pass" if c["failed"] == 0 else f"❌ {c['failed']} failed"
        checks = f"{c['passed']}/{c['total']}"
        lines.append(f"| {c['icon']} **{c['title']}** | {status} | {checks} | {c['desc']} |")
    lines += ["", "### What the tool can do", "",
              "| Format | Removes hidden data | Keeps picture identical | Makes it untraceable |",
              "|---|---|---|---|"]
    for label, removes, keeps, untr in caps:
        lines.append(f"| **{label}** | {removes} | {keeps} | {untr} |")
    lines += ["", "### See it in action — before vs after", "", flow_md]
    if meta.get("commit"):
        lines += ["", "---",
                  f"_Commit `{meta['commit'][:7]}` on `{meta.get('branch','?')}` · "
                  f"run #{meta.get('run','?')}._"]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# HTML dashboard (GitHub Pages / artifact)
# --------------------------------------------------------------------------- #
def _md_to_html_body(md: str) -> str:
    spec = importlib.util.spec_from_file_location(
        "md2html", os.path.join(REPO, "docs", "_md2html.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    full = mod.convert(md)
    return full.split("<body>", 1)[1].rsplit("</body>", 1)[0]


def render_html(cats, flow_md, caps, meta):
    total_pass = sum(c["passed"] for c in cats)
    total_fail = sum(c["failed"] for c in cats)
    total_skip = sum(c["skipped"] for c in cats)
    ok = total_fail == 0

    cards = []
    for c in cats:
        good = c["failed"] == 0
        pct = round(100 * c["passed"] / c["total"]) if c["total"] else 0
        badge = ("PASS" if good else f"{c['failed']} FAILED")
        cards.append(f"""
      <div class="card {'ok' if good else 'bad'}">
        <div class="card-h"><span class="ico">{c['icon']}</span>
          <span class="ctitle">{html.escape(c['title'])}</span>
          <span class="pill {'ok' if good else 'bad'}">{badge}</span></div>
        <p class="cdesc">{html.escape(c['desc'])}</p>
        <div class="bar"><div class="fill" style="width:{pct}%"></div></div>
        <div class="cmeta">{c['passed']}/{c['total']} checks passed
          {'· ' + str(c['skipped']) + ' skipped' if c['skipped'] else ''}</div>
      </div>""")

    cap_rows = "".join(
        f"<tr><td><b>{html.escape(l)}</b></td><td>{r}</td><td>{k}</td><td>{u}</td></tr>"
        for l, r, k, u in caps)

    flow_html = _md_to_html_body(flow_md)
    meta_line = ""
    if meta.get("commit"):
        meta_line = (f"commit {meta['commit'][:7]} · {meta.get('branch','')} · "
                     f"run #{meta.get('run','')}")

    banner_txt = ("All quality checks passed" if ok else "Some checks failed")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Metadata Scrubber — Quality Report</title>
<style>
:root{{--bg:#f6f8fb;--card:#fff;--ink:#1a2233;--mut:#5b6675;--line:#e4e9f0;
--ok:#12a150;--okbg:#e7f7ee;--bad:#e5484d;--badbg:#fdecec;--brand:#2563eb;}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}}
.wrap{{max-width:960px;margin:0 auto;padding:0 20px 60px}}
header{{background:linear-gradient(120deg,#1e3a8a,#2563eb 60%,#3b82f6);color:#fff;
padding:44px 20px 40px;text-align:center}}
header h1{{margin:0 0 6px;font-size:30px}}header p{{margin:0;opacity:.9}}
.banner{{margin:-24px auto 26px;max-width:920px;border-radius:14px;padding:18px 24px;
display:flex;align-items:center;gap:14px;font-size:19px;font-weight:700;
box-shadow:0 8px 24px rgba(20,40,80,.10)}}
.banner.ok{{background:var(--okbg);color:var(--ok);border:1px solid #b7ebca}}
.banner.bad{{background:var(--badbg);color:var(--bad);border:1px solid #f6c2c4}}
.banner .n{{margin-left:auto;font-size:15px;font-weight:600;color:var(--mut)}}
h2{{font-size:20px;margin:34px 0 14px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:14px;
padding:18px 18px 16px;box-shadow:0 2px 8px rgba(20,40,80,.04)}}
.card.bad{{border-color:#f6c2c4}}
.card-h{{display:flex;align-items:center;gap:9px;margin-bottom:6px}}
.ico{{font-size:22px}}.ctitle{{font-weight:700;font-size:16px}}
.pill{{margin-left:auto;font-size:11px;font-weight:800;letter-spacing:.3px;
padding:3px 9px;border-radius:999px}}
.pill.ok{{background:var(--okbg);color:var(--ok)}}.pill.bad{{background:var(--badbg);color:var(--bad)}}
.cdesc{{color:var(--mut);font-size:13.5px;margin:.2em 0 .8em;min-height:38px}}
.bar{{height:8px;background:#eef2f7;border-radius:99px;overflow:hidden}}
.fill{{height:100%;background:var(--ok)}}
.card.bad .fill{{background:var(--bad)}}
.cmeta{{font-size:12.5px;color:var(--mut);margin-top:8px}}
table{{border-collapse:collapse;width:100%;background:#fff;border:1px solid var(--line);
border-radius:12px;overflow:hidden;font-size:14px;margin:.6em 0}}
th,td{{padding:11px 13px;text-align:left;border-bottom:1px solid var(--line)}}
th{{background:#eef3fb;color:#1e3a8a;font-size:12.5px;text-transform:uppercase;letter-spacing:.4px}}
tr:last-child td{{border-bottom:none}}
code{{background:#eef2f7;padding:.1em .4em;border-radius:5px;font-size:12.5px}}
.flow :is(h1,h2,h3){{font-size:16px;margin:1.1em 0 .4em}}
.foot{{text-align:center;color:var(--mut);font-size:12.5px;margin-top:34px}}
@media(prefers-color-scheme:dark){{
:root{{--bg:#0f1420;--card:#161d2b;--ink:#e7ecf3;--mut:#93a1b5;--line:#243044;--okbg:#0f2a1c;--badbg:#2a1315}}
th{{background:#1b2537;color:#8fb4ff}}code{{background:#1b2537}}.bar{{background:#1b2537}}}}
</style></head><body>
<header><h1>🛡️ Metadata Scrubber — Quality Report</h1>
<p>Permanently removes hidden data (GPS, camera, timestamps) from images. Checked automatically on every change.</p></header>
<div class="wrap">
  <div class="banner {'ok' if ok else 'bad'}">
    <span>{'🟢' if ok else '🔴'} {banner_txt}</span>
    <span class="n">{total_pass} passed{f' · {total_fail} failed' if total_fail else ''}{f' · {total_skip} skipped' if total_skip else ''}</span>
  </div>

  <h2>What we checked</h2>
  <div class="grid">{''.join(cards)}</div>

  <h2>What the tool can do</h2>
  <table><thead><tr><th>Format</th><th>Removes hidden data</th>
    <th>Keeps picture identical</th><th>Makes it untraceable</th></tr></thead>
    <tbody>{cap_rows}</tbody></table>

  <h2>See it in action — before vs after</h2>
  <div class="flow">{flow_html}</div>

  <div class="foot">{html.escape(meta_line)}</div>
</div></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--junit", default="pytest-results.xml")
    ap.add_argument("--html", default="qa-site/index.html")
    ap.add_argument("--summary", default="")
    ap.add_argument("--flow-out", default="scrub_artifacts")
    args = ap.parse_args()

    cats = categorize(parse_junit(args.junit))
    caps = load_capabilities()
    flow_md = flow.build_body(args.flow_out)
    meta = {"commit": os.environ.get("GITHUB_SHA", ""),
            "branch": os.environ.get("GITHUB_REF_NAME", ""),
            "run": os.environ.get("GITHUB_RUN_NUMBER", "")}

    md = render_markdown(cats, flow_md, caps, meta)
    html_doc = render_html(cats, flow_md, caps, meta)

    os.makedirs(os.path.dirname(args.html) or ".", exist_ok=True)
    with open(args.html, "w", encoding="utf-8") as f:
        f.write(html_doc)
    if args.summary:
        with open(args.summary, "w", encoding="utf-8") as f:
            f.write(md)
    print(f"QA report: {sum(c['passed'] for c in cats)} passed, "
          f"{sum(c['failed'] for c in cats)} failed across {len(cats)} areas "
          f"-> {args.html}")


if __name__ == "__main__":
    main()
