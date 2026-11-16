#!/usr/bin/env python3
"""Render the plain-language limits page from docs/limits.md.

    python scripts/limits_page.py            # writes docs/what_we_cant_do_yet.html
    python scripts/limits_page.py --pdf      # ... and the PDF beside it (Chrome)

The page's text is the SHORT block of `docs/limits.md`, and nothing else: the page
used to be written by hand, and two weeks later it still said a 4 GB video needed
10 GB of memory and that Excel had not been measured. `tests/scrub/
test_limits_page.py` fails when the committed page differs from what this script
makes, when a limit that is not solved is missing from the block, or when a "can
they tell" verdict disagrees with the published results.

The block's grammar, line by line (it reads as ordinary Markdown on GitHub):

    <!-- updated: 16 November 2026 -->       the page's date
    plain lines before the first ###          the opening paragraph
    > ...                                     the box under the cards
    ### Title                                 a section
    <!-- card: Short title | One line. -->    the section's card at the top
    plain lines after a ###                   the section's lead (or, after a
                                              list or table, its closing note)
    - ...                                     an item
    | id | label | verdict | text |           a "can they tell" row; id is a
                                              published results file
"""
from __future__ import annotations

import argparse
import html
import os
import re
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIMITS = os.path.join(REPO, "docs", "limits.md")
OUT = os.path.join(REPO, "docs", "what_we_cant_do_yet.html")
TITLE = "What scrubmeta Can't Do Yet"

VERDICTS = {"No": "no", "Barely": "barely", "Partly": "partly", "Yes": "yes",
            "Not tested": "untested"}

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #
def block(text: str, name: str) -> str:
    m = re.search(rf"<!-- {name}:BEGIN -->\n(.*?)<!-- {name}:END -->", text, re.S)
    if not m:
        raise SystemExit(f"docs/limits.md has no {name} block")
    return m.group(1)


def limit_rows(text: str) -> dict[int, str]:
    """Row number -> status cell, from the LIMITS table."""
    rows = {}
    for line in block(text, "LIMITS").splitlines():
        m = re.match(r"\| \*\*(\d+)\*\* \|", line)
        if m:
            rows[int(m.group(1))] = line.rstrip().rstrip("|").rsplit("|", 1)[1].strip()
    return rows


def parse(short: str) -> dict:
    page = {"updated": "", "standfirst": [], "strengths": [], "sections": []}
    sec = None
    for line in short.splitlines():
        line = line.rstrip()
        if not line:
            continue
        if m := re.fullmatch(r"<!-- updated: (.+?) -->", line):
            page["updated"] = m.group(1)
        elif line.startswith("### "):
            sec = {"title": line[4:], "card": None, "lead": [], "items": [],
                   "rows": [], "note": []}
            page["sections"].append(sec)
        elif m := re.fullmatch(r"<!-- card: (.+?) \| (.+?) -->", line):
            sec["card"] = (m.group(1), m.group(2))
        elif sec is None:
            page["strengths" if line.startswith("> ") else "standfirst"].append(
                line[2:] if line.startswith("> ") else line)
        elif line.startswith("- "):
            sec["items"].append(line[2:])
        elif line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if cells[0] in ("Results", "---") or set(cells[0]) <= {"-", ":"}:
                continue
            if len(cells) != 4 or cells[2] not in VERDICTS:
                raise SystemExit(f"bad table row in the SHORT block: {line}")
            sec["rows"].append(cells)
        else:
            (sec["note"] if sec["items"] or sec["rows"] else sec["lead"]).append(line)
    return page


def citations(text: str) -> set[int]:
    return {int(n) for group in re.findall(r"\((#\d+(?:, #\d+)*)\)", text)
            for n in re.findall(r"\d+", group)}


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #
def inline(text: str) -> str:
    out = html.escape(text, quote=False)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    return re.sub(r" ?\((#\d+(?:, #\d+)*)\)", r' <span class="ref">\1</span>', out)


CSS = """\
  @page { size: A4; margin: 16mm 16mm 15mm; }
  @page { @bottom-right { content: counter(page) " / " counter(pages);
          font: 8px "Helvetica Neue", Helvetica, Arial, sans-serif; color: #7c8d99; } }

  :root {
    --ink: #121e27; --ink-2: #485966; --ink-3: #7c8d99;
    --rule: #d5dee4; --soft: #f4f7f8;
    --accent: #0e6a74; --accent-soft: #e2f0f1;
    --serif: "Iowan Old Style", "Palatino", Georgia, serif;
    --sans: "Helvetica Neue", Helvetica, Arial, sans-serif;
  }
  * { box-sizing: border-box; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  html, body { margin: 0; padding: 0; }
  body { color: var(--ink); font: 10.2px/1.45 var(--sans); background: #fff; }
  p { margin: 0; }

  .eyebrow { font-size: 8.2px; letter-spacing: .16em; text-transform: uppercase;
             color: var(--accent); font-weight: 600; }
  h1 { font: 600 28px/1.1 var(--serif); margin: 6px 0 10px; letter-spacing: -.01em; }
  .standfirst { font-size: 12.4px; line-height: 1.5; color: var(--ink-2); max-width: 88ch; }
  .dateline { margin-top: 12px; padding-top: 8px; border-top: 1px solid var(--rule);
              font-size: 9px; color: var(--ink-3); letter-spacing: .04em; }

  .cards { display: grid; grid-template-columns: repeat(%(cards)d, 1fr); gap: 8px;
           margin: 16px 0 12px; }
  .card { background: var(--soft); border-radius: 6px; padding: 10px 10px 11px;
          border-top: 3px solid var(--accent); }
  .card .n { font: 600 15px/1 var(--serif); color: var(--accent); }
  .card h3 { font: 700 10.2px/1.3 var(--sans); margin: 6px 0 4px; }
  .card p { font-size: 9.2px; line-height: 1.4; color: var(--ink-2); }

  .strengths { border-left: 3px solid var(--accent); background: var(--accent-soft);
               padding: 8px 12px; border-radius: 0 6px 6px 0; color: var(--ink);
               font-size: 10.2px; }

  section { margin-top: 13px; }
  h2 { font: 600 16px/1.2 var(--serif); margin: 0 0 4px; break-after: avoid; }
  .lead { color: var(--ink-2); margin-bottom: 6px; }
  ul.items { list-style: none; margin: 0; padding: 0; }
  ul.items li { position: relative; padding: 3px 0 3px 14px; border-top: 1px solid #edf1f3;
                break-inside: avoid; }
  ul.items li:first-child { border-top: none; }
  ul.items li::before { content: ""; position: absolute; left: 1px; top: 9px; width: 5px;
                        height: 5px; border-radius: 50%%; background: var(--accent); }
  ul.items b { font-weight: 700; }
  code { font: 9.4px "SF Mono", Menlo, monospace; background: var(--soft); padding: 0 3px;
         border-radius: 3px; }
  .ref { color: var(--ink-3); font-size: 8.4px; white-space: nowrap; }

  table { width: 100%%; border-collapse: collapse; margin-top: 4px; font-size: 9.8px; }
  th { text-align: left; font-size: 8.2px; letter-spacing: .08em; text-transform: uppercase;
       color: var(--ink-3); font-weight: 600; padding: 0 8px 6px 0;
       border-bottom: 1px solid var(--rule); }
  td { padding: 4px 8px 4px 0; border-bottom: 1px solid #edf1f3; vertical-align: top; }
  tr { break-inside: avoid; }
  td:first-child { font-weight: 600; width: 30%%; }
  td:nth-child(2) { width: 15%%; }
  .pill { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 9px;
          font-weight: 700; white-space: nowrap; }
  .no { background: #e2f0f1; color: #0e6a74; }
  .barely { background: #e8f2e3; color: #3d6b2a; }
  .partly { background: #f6ecd9; color: #8f5d10; }
  .yes { background: #f7e2df; color: #9b2c22; }
  .untested { background: #eceff1; color: #5b6b77; }
  .note { color: var(--ink-2); margin-top: 8px; }

  .closing { margin-top: 14px; padding-top: 8px; border-top: 1px solid var(--rule);
             color: var(--ink-3); font-size: 9.4px; }

  @media screen and (max-width: 640px) {
    body { padding: 0 16px; }
    .cards { grid-template-columns: 1fr 1fr; }
  }
"""


def render(text: str) -> str:
    page = parse(block(text, "SHORT"))
    n_limits = len(limit_rows(text))
    cards = [s["card"] for s in page["sections"] if s["card"]]
    out = ["<!DOCTYPE html>", '<html lang="en">', "<head>", '<meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width, initial-scale=1">',
           f"<title>{html.escape(TITLE)}</title>",
           "<!-- Generated by scripts/limits_page.py from docs/limits.md. "
           "Edit that file, not this one. -->",
           "<style>", CSS % {"cards": len(cards)}, "</style>", "</head>", "<body>", "",
           '<div class="eyebrow">scrubmeta · Known limits</div>',
           f"<h1>{html.escape(TITLE)}</h1>",
           f'<p class="standfirst">{inline(" ".join(page["standfirst"]))}</p>',
           f'<div class="dateline">{html.escape(page["updated"])}</div>', "",
           '<div class="cards">']
    for i, (head, line) in enumerate(cards, 1):
        out.append(f'  <div class="card"><div class="n">{i}</div><h3>{inline(head)}</h3>'
                   f"<p>{inline(line)}</p></div>")
    out += ["</div>", "",
            f'<p class="strengths">{inline(" ".join(page["strengths"]))}</p>']
    for s in page["sections"]:
        out += ["", "<section>", f"  <h2>{inline(s['title'])}</h2>"]
        if s["lead"]:
            out.append(f'  <p class="lead">{inline(" ".join(s["lead"]))}</p>')
        if s["rows"]:
            out += ["  <table>", "    <thead><tr><th>File type</th><th>Can they tell?</th>"
                    "<th>What gives it away</th></tr></thead>", "    <tbody>"]
            for _, label, verdict, gives in s["rows"]:
                out.append(f"      <tr><td>{inline(label)}</td><td><span class=\"pill "
                           f"{VERDICTS[verdict]}\">{verdict}</span></td>"
                           f"<td>{inline(gives)}</td></tr>")
            out += ["    </tbody>", "  </table>"]
        if s["items"]:
            out.append('  <ul class="items">')
            out += [f"    <li>{inline(item)}</li>" for item in s["items"]]
            out.append("  </ul>")
        if s["note"]:
            out.append(f'  <p class="note">{inline(" ".join(s["note"]))}</p>')
        out.append("</section>")
    out += ["", f'<p class="closing">Every point on this page was measured, not assumed. '
            f"The numbers in grey are rows of the project's full list of limits "
            f"(<code>docs/limits.md</code>, {n_limits} entries), where each one is "
            "explained with the measurement behind it.</p>", "", "</body>", "</html>", ""]
    return "\n".join(out)


def write_pdf(html_path: str, pdf_path: str) -> None:
    """Print with Chrome, then clean the PDF with this tool's own light clean: a
    page about metadata should not carry Chrome's name and the time it was made."""
    chrome = CHROME if os.path.exists(CHROME) else shutil.which("google-chrome")
    if not chrome:
        raise SystemExit("--pdf needs Google Chrome")
    with tempfile.TemporaryDirectory() as tmp:
        raw = os.path.join(tmp, "page.pdf")
        subprocess.run([chrome, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                        f"--print-to-pdf={raw}", "file://" + html_path],
                       check=True, capture_output=True, timeout=120)
        clean = os.path.join(tmp, "clean.pdf")
        subprocess.run([sys.executable, "-m", "src.scrub", raw, clean,
                        "--fidelity", "F1", "--no-report"],
                       check=True, cwd=REPO, timeout=120)
        shutil.copyfile(clean, pdf_path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--pdf", action="store_true", help="also print the PDF (Chrome)")
    args = ap.parse_args(argv)
    page = render(open(LIMITS, encoding="utf-8").read())
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(page)
    print(os.path.relpath(OUT, REPO))
    if args.pdf:
        pdf = OUT[:-5] + ".pdf"
        write_pdf(OUT, pdf)
        print(os.path.relpath(pdf, REPO))
    return 0


if __name__ == "__main__":
    sys.exit(main())
