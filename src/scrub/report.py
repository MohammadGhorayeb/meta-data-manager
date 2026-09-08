"""The scrub report: what was in the file, and what is in it now.

A scrubber that says nothing but `exit 0` asks the user to take it on faith. This
turns a successful scrub into a short before/after account — which fields were
found, what they said, and what happened to each.

Three design points worth stating, because each is a trade rather than an obvious
choice:

**The report echoes the metadata it removed.** That is what makes it useful and it
is also a disclosure: an author's name and a GPS position that were inside a file
now appear on a terminal, in a shell history, or in a CI log if the output is
redirected. The values are therefore truncated, the report is easy to turn off
(`--no-report`), and this docstring says so plainly rather than leaving a privacy
tool quietly printing secrets.

**It reports what *we* saw, not ground truth.** Each handler describes the file
through its own walker, so the report's coverage is exactly the tool's coverage. A
locus no handler models is absent from the report *and* from the scrub — the report
cannot be read as "nothing else was in the file". `exiftool` remains the independent
check, and the report says so when it has nothing to show.

**It never changes the exit code.** A report that could fail would turn a good scrub
into a failure over a display problem; `describe()` raising is swallowed the way
advisories are.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import crosscheck

# Long values are truncated: a report is a summary, and an embedded XMP packet or a
# base64 thumbnail would otherwise fill the terminal with the very data we removed.
MAX_VALUE = 58

REMOVED, CHANGED, KEPT, ADDED = "removed", "changed", "kept", "added"


@dataclass
class Item:
    locus: str
    status: str
    before: str | None = None
    after: str | None = None
    note: str = ""


@dataclass
class Report:
    format_id: str
    fidelity: str
    size_before: int
    size_after: int
    items: list[Item] = field(default_factory=list)
    advisories: list[str] = field(default_factory=list)
    described: bool = True        # False when the handler cannot describe itself
    check: crosscheck.CrossCheck | None = None

    @property
    def removed_values(self) -> list[str]:
        """The values this report claims are gone — what the cross-check looks for."""
        return [i.before for i in self.items
                if i.status == REMOVED and i.before]

    @property
    def removed(self) -> list[Item]:
        return [i for i in self.items if i.status == REMOVED]

    @property
    def kept(self) -> list[Item]:
        return [i for i in self.items if i.status == KEPT]


def clip(value: object, limit: int = MAX_VALUE) -> str:
    """One line, printable, bounded. Control characters would let a crafted file
    write escape sequences to the user's terminal, so they do not survive."""
    text = value if isinstance(value, str) else repr(value)
    # Whitespace collapses FIRST. Doing it the other way round turns the newlines and
    # tabs of a legitimate multi-line comment into dots before they can become
    # spaces, so `two\nlines` renders as `two.lines` -- caught by its own test.
    text = " ".join(text.split())
    text = "".join(ch if ch.isprintable() else "." for ch in text)
    return text if len(text) <= limit else text[:limit - 1] + "…"


def human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def _describe(handler, data: bytes) -> dict[str, str] | None:
    """A handler's own account of the metadata in a file, or None if it has none.

    Swallowed on failure for the same reason advisories are: a display feature must
    never be able to turn a good scrub into a failure.
    """
    fn = getattr(handler, "describe", None)
    if fn is None:
        return None
    try:
        return dict(fn(data))
    except Exception:                                     # noqa: BLE001
        return None


def build(handler, before: bytes, after: bytes, fidelity: str,
          advisories: list[str] | None = None) -> Report:
    """Diff the handler's description of the input against its description of the
    output. Order follows the input's, so the report reads in the file's own order
    rather than alphabetically."""
    fmt = getattr(handler, "format_id", "?")
    rep = Report(format_id=fmt, fidelity=fidelity,
                 size_before=len(before), size_after=len(after),
                 advisories=list(advisories or []))

    was = _describe(handler, before)
    now = _describe(handler, after)
    if was is None or now is None:
        rep.described = False
        return rep

    for locus, old in was.items():
        new = now.get(locus)
        if new is None:
            rep.items.append(Item(locus, REMOVED, before=clip(old)))
        elif new != old:
            rep.items.append(Item(locus, CHANGED, before=clip(old),
                                  after=clip(new)))
        else:
            rep.items.append(Item(locus, KEPT, before=clip(old), after=clip(old)))
    for locus, new in now.items():
        if locus not in was:
            rep.items.append(Item(locus, ADDED, after=clip(new)))
    return rep


_SYMBOL = {REMOVED: "-", CHANGED: "~", KEPT: "=", ADDED: "+"}


def render(rep: Report) -> str:
    """The human-readable report. Deliberately compact: it is printed after every
    scrub, so it has to be worth reading every time."""
    head = (f"{rep.format_id.upper()} · {rep.fidelity} · "
            f"{human_size(rep.size_before)} → {human_size(rep.size_after)}")
    lines = [head]

    if not rep.described:
        lines.append("  (this format cannot yet describe its own metadata — "
                     "verify with `exiftool`)")
        return "\n".join(lines + _advisory_lines(rep))

    if not rep.items:
        lines.append("  no metadata found by this tool "
                     "(which is not the same as none being present)")
        return "\n".join(lines + _advisory_lines(rep))

    width = min(34, max((len(i.locus) for i in rep.items), default=10))
    shown = [i for i in rep.items if i.status != KEPT]
    for item in shown:
        sym = _SYMBOL[item.status]
        value = item.before if item.status in (REMOVED, CHANGED) else item.after
        line = f"  {sym} {item.locus:<{width}}  {value or ''}"
        if item.status == CHANGED:
            line += f"  →  {item.after}"
        if item.note:
            line += f"   [{item.note}]"
        lines.append(line.rstrip())

    kept = rep.kept
    if kept:
        names = ", ".join(i.locus for i in kept[:6])
        more = f" (+{len(kept) - 6} more)" if len(kept) > 6 else ""
        lines.append(f"  = kept: {names}{more}")

    counts = []
    for status, label in ((REMOVED, "removed"), (CHANGED, "changed"),
                          (ADDED, "added")):
        n = sum(1 for i in rep.items if i.status == status)
        if n:
            counts.append(f"{n} {label}")
    if kept:
        counts.append(f"{len(kept)} kept")
    if counts:
        lines.append("  " + ", ".join(counts))

    return "\n".join(lines + _advisory_lines(rep))


def _advisory_lines(rep: Report) -> list[str]:
    lines = [f"  ! {clip(a, 96)}" for a in rep.advisories]
    if rep.check is not None:
        lines.extend(crosscheck.render(rep.check))
    return lines
