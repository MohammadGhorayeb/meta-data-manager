"""E-PDF-HISTORY — does an earlier revision survive?

A PDF is edited by **appending**. The editor writes new objects on the end, a new
cross-reference table, and a trailer whose `/Prev` points back at the old one. The
old objects are never touched. Open the file and you see the latest revision; walk
`/Prev` and you see every draft that came before it, in full.

This is the most famous disclosure mode the format has — the published report whose
"redacted" paragraphs are still readable one layer down. It is also the cleanest
result Phase 3 can produce: binary, provable, and independent of every contested
design question in the plan, which is why M1 comes before the walker.

Three independent attacks, because each defeats a different bad fix:

* **rollback** — walk the revision chain, truncate the file after each revision's
  `%%EOF`, and render the prefix with `pdftotext`. Recovers the earlier document as
  a *document*: its visible text, not a string that happens to be present.
* **carve** — search the raw bytes for known secrets. Catches the tool that breaks
  the `/Prev` chain while leaving the old object bytes in place, which defeats
  rollback without removing anything.
* **orphans** — every `N 0 obj` in the file versus the set pikepdf can reach. The
  difference is the removed-but-still-present set. This is the accounting ledger
  W1 describes, and it catches history that carries no known sentinel.

A tool only passes if all three come back empty.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile

from tests.scrub import pdf_corpus as pc

HAVE_MAT2 = shutil.which("mat2") is not None
HAVE_EXIFTOOL = shutil.which("exiftool") is not None

_STARTXREF = re.compile(rb"startxref\s+(\d+)\s*?%%EOF")
_OBJ = re.compile(rb"(?:^|[\s>])(\d+)\s+(\d+)\s+obj\b")
_PREV = re.compile(rb"/Prev\s+(\d+)")


# --------------------------------------------------------------------------- #
# Structure
# --------------------------------------------------------------------------- #
def revision_ends(data: bytes) -> list[int]:
    """Byte offsets just past each `startxref … %%EOF`, in file order.

    Counting `startxref` rather than `%%EOF`: a bare `%%EOF` turns up inside
    compressed streams by chance (measured — MAT2's output has one), and treating
    that as a revision would report history where there is none. `startxref`
    followed by an offset and `%%EOF` does not happen by accident.
    """
    return [m.end() for m in _STARTXREF.finditer(data)]


def prev_chain(data: bytes) -> list[int]:
    """Cross-reference offsets reachable through `/Prev`, newest first.

    Classic cross-reference tables only. An xref-*stream* file keeps `/Prev` inside
    a compressed object, so this returns a short chain for those and
    `revision_ends()` is the reliable counter there. Both are reported.
    """
    m = list(_STARTXREF.finditer(data))
    if not m:
        return []
    offsets, seen = [], set()
    off = int(m[-1].group(1))
    while 0 <= off < len(data) and off not in seen:
        seen.add(off)
        offsets.append(off)
        end = data.find(b"startxref", off)
        p = _PREV.search(data, off, end if end != -1 else len(data))
        if not p:
            break
        off = int(p.group(1))
    return offsets


def _reachable(pdf) -> set[int]:
    """Object numbers reachable by walking the object graph from the trailer.

    An explicit traversal rather than `Pdf.objects`, which is qpdf's object *table*
    — every object the xref knows about, reachable or not — and so would report
    the ledger as always balanced.
    """
    import pikepdf

    seen: set[int] = set()
    stack = [pdf.trailer]
    while stack:
        obj = stack.pop()
        if isinstance(obj, pikepdf.Object) and obj.is_indirect:
            num = obj.objgen[0]
            if num in seen:
                continue
            seen.add(num)
        if isinstance(obj, pikepdf.Dictionary | pikepdf.Stream):
            stack.extend(obj.values())
        elif isinstance(obj, pikepdf.Array):
            stack.extend(obj)
    return seen


def object_ledger(path: str) -> dict:
    """Account for every object definition physically in the file.

    Two different residues, and conflating them hides one of them:

    * **superseded** — an object number defined more than once. That *is* an
      incremental update: the earlier definitions are the old content, still at
      their original offsets. The raw corpus has no orphans at all and would look
      clean to an orphan-only check.
    * **orphaned** — a number present in the bytes that nothing reaches from the
      trailer. This is what a tool leaves when it rewrites the xref but not the body.
    """
    import pikepdf

    with open(path, "rb") as f:
        data = f.read()
    defined: dict[int, int] = {}
    for m in _OBJ.finditer(data):
        num = int(m.group(1))
        defined[num] = defined.get(num, 0) + 1
    superseded = {n: c - 1 for n, c in defined.items() if c > 1}
    try:
        with pikepdf.open(path, attempt_recovery=False) as pdf:
            orphaned = sorted(set(defined) - _reachable(pdf))
    except Exception:
        orphaned = sorted(defined)
    return {"defined": sum(defined.values()), "superseded": superseded,
            "orphaned": orphaned,
            "stale_definitions": sum(superseded.values()) + len(orphaned)}


# --------------------------------------------------------------------------- #
# Attacks
# --------------------------------------------------------------------------- #
def _docinfo(path: str) -> dict:
    import pikepdf

    try:
        with pikepdf.open(path) as pdf:
            if pdf.trailer.get("/Info") is None:
                return {}
            return {str(k): str(v) for k, v in pdf.docinfo.items()}
    except Exception:
        return {}


def rollback(path: str) -> list[dict]:
    """Every revision except the current one, recovered by truncation.

    Reports the **text and the `/Info` dictionary** of each earlier revision.
    Text alone is not enough: a tool can destroy the document's own history and
    still leave its own `/Info` — name, version, and a wall-clock timestamp — one
    revision down, which is a leak about the person who ran the scrub rather than
    about the document. That was found by hand first; it belongs in the attack.
    """
    with open(path, "rb") as f:
        data = f.read()
    ends = revision_ends(data)
    out = []
    with tempfile.TemporaryDirectory(prefix="rollback_") as tmp:
        for i, end in enumerate(ends[:-1]):          # [-1] is the visible document
            frag = os.path.join(tmp, f"rev{i}.pdf")
            with open(frag, "wb") as f:
                f.write(data[:end] + b"\n")
            out.append({"text": pc.pdftotext(frag), "info": _docinfo(frag)})
    return out


def carve(data: bytes, secrets) -> list[str]:
    """Raw-byte search, ASCII and UTF-16LE, as in `test_forensic_recovery`."""
    hits = []
    for s in secrets:
        if s in data or s.decode("latin-1").encode("utf-16-le") in data:
            hits.append(s.decode("latin-1"))
    return hits


def attack(path: str, secrets) -> dict:
    """Every attack at once.

    `recovered` is the planted secrets an analyst gets back. `prior_metadata` is
    everything else the rollback yields — `/Info` entries that are gone from the
    visible document but still readable one revision down, whoever they belong to.
    """
    with open(path, "rb") as f:
        data = f.read()
    rolled = rollback(path)
    carved = carve(data, secrets)
    recovered = sorted({s.decode("latin-1") for s in secrets
                        if any(s.decode("latin-1") in r["text"] for r in rolled)}
                       | set(carved))
    now = _docinfo(path)
    prior = {f"{k}={v}" for r in rolled for k, v in r["info"].items()
             if now.get(k) != v}
    return {
        "size": len(data),
        "revisions": len(revision_ends(data)),
        "prev_chain": len(prev_chain(data)),
        "ledger": object_ledger(path),
        "rolled_back": rolled,
        "carved": carved,
        "recovered": recovered,
        "prior_metadata": sorted(prior),
        "visible_text": pc.pdftotext(path),
    }


# --------------------------------------------------------------------------- #
# The cleaners under measurement
# --------------------------------------------------------------------------- #
def _copy(src: str, dst: str) -> str:
    shutil.copyfile(src, dst)
    return dst


def clean_raw(src: str, dst: str) -> str:
    """The control: no cleaning at all."""
    return _copy(src, dst)


def clean_pikepdf(src: str, dst: str) -> str:
    """A plain pikepdf rewrite — the semantic layer Phase 3 keeps, on its own.

    Not a scrubber: it strips nothing. It is here to separate "the rewrite collapsed
    the revisions" from "the metadata scrub removed the content", because those are
    different mechanisms and only the first is what defeats history.
    """
    import pikepdf

    with pikepdf.open(src, attempt_recovery=False) as pdf:
        pdf.save(dst, deterministic_id=True)
    return dst


def clean_mat2(src: str, dst: str, lightweight: bool = False) -> str:
    _copy(src, dst)
    cmd = ["mat2", "--inplace"] + (["--lightweight"] if lightweight else []) + [dst]
    subprocess.run(cmd, capture_output=True, check=False)
    return dst


def clean_exiftool(src: str, dst: str) -> str:
    _copy(src, dst)
    subprocess.run(["exiftool", "-all=", "-overwrite_original", dst],
                   capture_output=True, check=False)
    return dst


def cleaners() -> dict:
    """Everything measurable on this machine. Absent tools are absent, never faked."""
    out = {"raw": clean_raw, "pikepdf_rewrite": clean_pikepdf}
    if HAVE_MAT2:
        out["mat2"] = lambda s, d: clean_mat2(s, d, lightweight=False)
        out["mat2_lightweight"] = lambda s, d: clean_mat2(s, d, lightweight=True)
    if HAVE_EXIFTOOL:
        out["exiftool_all"] = clean_exiftool
    try:                                    # our own handler, tier by tier
        from src.scrub import cli
        from src.scrub.dispatch import default_dispatcher
        handler = default_dispatcher().resolve(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        for fid in handler.fidelities:      # only the tiers that actually exist
            out[f"scrubber_{fid}"] = (
                lambda s, d, _f=fid: (cli.scrub_file(s, d, _f), d)[1])
    except Exception:
        pass
    return out


SENTINEL = "SECRET"
SECRETS = [f"CONFIDENTIAL-REV{i}-{SENTINEL}".encode() for i in (1, 2)] + \
          [f"Author-REV1-{SENTINEL}".encode(), f"Draft-REV1-{SENTINEL}".encode()]


def run(tmpdir: str, n_revisions: int = 3) -> dict:
    """Measure every available cleaner against the incremental-history corpus."""
    src = pc.incremental_pdf(os.path.join(tmpdir, "history.pdf"),
                             sentinel=SENTINEL, n_revisions=n_revisions)
    visible = pc.pdftotext(src)
    results = {}
    for name, fn in cleaners().items():
        dst = os.path.join(tmpdir, f"cleaned_{name}.pdf")
        try:
            fn(src, dst)
        except Exception as exc:                      # a tool that refuses the file
            results[name] = {"error": f"{type(exc).__name__}: {exc}"}
            continue
        r = attack(dst, SECRETS)
        r["text_preserved"] = (r["visible_text"].strip() == visible.strip())
        r["history_gone"] = (not r["recovered"] and r["revisions"] <= 1
                             and not r["ledger"]["stale_definitions"])
        r["clean"] = r["history_gone"] and not r["prior_metadata"] \
            and r["text_preserved"]
        results[name] = r
    return results


def main() -> None:
    tmpdir = tempfile.mkdtemp(prefix="e_pdf_history_")
    results = run(tmpdir)
    print(f"E-PDF-HISTORY — 3-revision document, secrets planted in revisions 1-2\n"
          f"corpus: {tmpdir}/history.pdf\n")
    print(f"{'cleaner':<20} {'size':>7} {'revs':>5} {'stale':>6} {'text':>6}  "
          f"recovered from earlier revisions")
    for name, r in results.items():
        if "error" in r:
            print(f"{name:<20} {r['error']}")
            continue
        got = r["recovered"] + r["prior_metadata"]
        print(f"{name:<20} {r['size']:>7} {r['revisions']:>5} "
              f"{r['ledger']['stale_definitions']:>6} "
              f"{'kept' if r['text_preserved'] else 'LOST':>6}  "
              f"{'; '.join(got) or '— nothing —'}")
    print("\n'revs' counts startxref…%%EOF; 'stale' counts object definitions that are "
          "superseded\nor unreachable — history that is physically still in the file. "
          "A cleaner passes only\nwhen nothing is recoverable AND the document's text "
          "survives.")


if __name__ == "__main__":
    main()
