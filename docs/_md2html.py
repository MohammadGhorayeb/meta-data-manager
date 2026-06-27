#!/usr/bin/env python3
"""Minimal, dependency-free Markdown -> styled HTML for harness_explained.md.
Handles headings, GFM pipe tables, fenced code, lists, hr, bold, inline code."""
import html
import re
import sys

CSS = """
@page { size: A4; margin: 18mm 16mm; }
* { box-sizing: border-box; }
body {
  font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  color: #1a1a1a; max-width: 820px; margin: 0 auto;
}
h1 { font-size: 26px; border-bottom: 3px solid #2563eb; padding-bottom: .3em; margin: 0 0 .6em; }
h2 { font-size: 20px; margin: 1.6em 0 .5em; color: #1e3a8a; border-bottom: 1px solid #e5e7eb; padding-bottom: .25em; }
h3 { font-size: 16px; margin: 1.2em 0 .4em; color: #1e40af; }
p { margin: .55em 0; }
strong { color: #111; }
code { background: #f3f4f6; padding: .12em .35em; border-radius: 4px;
       font: 13px/1.4 "SF Mono", Menlo, Consolas, monospace; color: #b91c1c; }
pre { background: #0f172a; color: #e2e8f0; padding: 14px 16px; border-radius: 8px;
      overflow-x: auto; font: 12.5px/1.5 "SF Mono", Menlo, Consolas, monospace; }
pre code { background: none; color: inherit; padding: 0; }
hr { border: none; border-top: 1px solid #d1d5db; margin: 1.8em 0; }
table { border-collapse: collapse; width: 100%; margin: 1em 0; font-size: 13px; }
th, td { border: 1px solid #d1d5db; padding: 7px 10px; text-align: left; vertical-align: top; }
th { background: #eff6ff; color: #1e3a8a; }
tr:nth-child(even) td { background: #f9fafb; }
ul, ol { margin: .5em 0 .5em 1.2em; padding-left: 1em; }
li { margin: .3em 0; }
blockquote { border-left: 4px solid #93c5fd; margin: 1em 0; padding: .2em 1em; color: #374151; background: #f8fafc; }
h2 { page-break-after: avoid; }
table, pre { page-break-inside: avoid; }
"""


def inline(text):
    text = html.escape(text)
    # inline code first so ** inside code is untouched
    parts = re.split(r"(`[^`]+`)", text)
    for i, p in enumerate(parts):
        if p.startswith("`") and p.endswith("`"):
            parts[i] = "<code>" + p[1:-1] + "</code>"
        else:
            parts[i] = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", p)
    return "".join(parts)


def cells(row):
    row = row.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    return [c.strip() for c in row.split("|")]


def convert(md):
    lines = md.split("\n")
    out, i, n = [], 0, len(lines)
    while i < n:
        line = lines[i]

        # fenced code
        if line.strip().startswith("```"):
            i += 1
            buf = []
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(html.escape(lines[i]))
                i += 1
            i += 1
            out.append("<pre><code>" + "\n".join(buf) + "</code></pre>")
            continue

        # table: header row followed by a |---| separator
        if line.lstrip().startswith("|") and i + 1 < n and re.match(r"^\s*\|?[\s:|-]+\|?\s*$", lines[i + 1]) and "-" in lines[i + 1]:
            header = cells(line)
            i += 2
            rows = []
            while i < n and lines[i].lstrip().startswith("|"):
                rows.append(cells(lines[i]))
                i += 1
            t = ["<table><thead><tr>"]
            t += ["<th>" + inline(h) + "</th>" for h in header]
            t.append("</tr></thead><tbody>")
            for r in rows:
                t.append("<tr>" + "".join("<td>" + inline(c) + "</td>" for c in r) + "</tr>")
            t.append("</tbody></table>")
            out.append("".join(t))
            continue

        # headings
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{inline(m.group(2))}</h{lvl}>")
            i += 1
            continue

        # hr
        if re.match(r"^\s*---+\s*$", line):
            out.append("<hr>")
            i += 1
            continue

        # blockquote
        if line.lstrip().startswith(">"):
            buf = []
            while i < n and lines[i].lstrip().startswith(">"):
                buf.append(lines[i].lstrip()[1:].strip())
                i += 1
            out.append("<blockquote>" + inline(" ".join(buf)) + "</blockquote>")
            continue

        # ordered list
        if re.match(r"^\s*\d+\.\s+", line):
            buf = []
            while i < n and re.match(r"^\s*\d+\.\s+", lines[i]):
                item = re.sub(r"^\s*\d+\.\s+", "", lines[i])
                i += 1
                while i < n and lines[i].strip() and not re.match(r"^\s*(\d+\.|-)\s+", lines[i]) and not lines[i].startswith("#"):
                    item += " " + lines[i].strip()
                    i += 1
                buf.append("<li>" + inline(item) + "</li>")
            out.append("<ol>" + "".join(buf) + "</ol>")
            continue

        # unordered list
        if re.match(r"^\s*-\s+", line):
            buf = []
            while i < n and re.match(r"^\s*-\s+", lines[i]):
                item = re.sub(r"^\s*-\s+", "", lines[i])
                i += 1
                while i < n and lines[i].strip() and not re.match(r"^\s*(\d+\.|-)\s+", lines[i]) and not lines[i].startswith("#"):
                    item += " " + lines[i].strip()
                    i += 1
                buf.append("<li>" + inline(item) + "</li>")
            out.append("<ul>" + "".join(buf) + "</ul>")
            continue

        # blank
        if not line.strip():
            i += 1
            continue

        # paragraph (gather until blank/structural)
        buf = [line]
        i += 1
        while i < n and lines[i].strip() and not re.match(r"^(#{1,6}\s|>|\s*-\s|\s*\d+\.\s|```|\s*---+\s*$|\s*\|)", lines[i]):
            buf.append(lines[i])
            i += 1
        out.append("<p>" + inline(" ".join(buf)) + "</p>")

    return f"""<!doctype html><html><head><meta charset="utf-8">
<style>{CSS}</style></head><body>
{''.join(out)}
</body></html>"""


if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8") as f:
        md = f.read()
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        f.write(convert(md))
