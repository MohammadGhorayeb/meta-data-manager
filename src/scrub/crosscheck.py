"""Independent verification: ask ExifTool, not ourselves.

The scrub report says what *this tool* found and removed. That is our own account of
our own work, and a reader has no reason to take it on faith — a handler bug that
failed to remove a field would also fail to report it, because both come from the
same walker. So the report ends with a check by a **different implementation**.

ExifTool is this project's designated measuring stick (CLAUDE.md), and it is the
right one here precisely because it shares no code with us: where it disagrees, it is
evidence rather than a second opinion.

Three things are reported, in increasing order of how much they are worth:

1. **Counts.** Metadata tags before and after, as ExifTool sees them.
2. **Groups that vanished entirely** — `GPS`, `IPTC`, `ID3v2_4`, `XMP-dc`. This is
   the honest headline: a whole standard's worth of fields is gone, said by something
   that is not us.
3. **Whether any removed value is still findable in the output.** This is the check
   that can *fail*, and it is the reason the module exists: if a value we reported as
   removed is still visible to ExifTool, our report was wrong and the user is told
   loudly rather than reassured.

What is deliberately NOT attempted: classifying the remaining tags as metadata versus
structure. ExifTool's group taxonomy is not consistent across formats — a JPEG's
dimensions arrive under `File`, a PNG's under `PNG` — so any automatic verdict would
be wrong for some format. The remaining groups are named and counted, and the user is
given the command to look for themselves, which is the point.
"""
from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field

TOOL = "exiftool"

# Read with `-a` (duplicate tags), `-G1` (specific group names) and `-s` (tag ids).
ARGS = ["-a", "-G1", "-s"]

# The same, but with family 4 added so *duplicate* tags get distinct keys
# (`ZIP:Copy1:ZipFileName`). Without it JSON silently collapses duplicates onto one
# key: a scrubbed DOCX read as 8 tags instead of 72, and — the part that matters — a
# value hiding in a collapsed duplicate could have escaped the leak search below.
# `ARGS` stays the plain form because it is what the user is told to run.
_READ_ARGS = ["-a", "-G1:4", "-s"]

# Groups ExifTool derives rather than reads: its own version, the filesystem's view,
# values decoded from the image structure, and its computed composites. None of them
# is stored metadata, so counting them would make every file look dirty forever.
DERIVED_GROUPS = {"ExifTool", "System", "File", "Composite"}

TIMEOUT = 60
# Short values produce false positives when searched for in the output ("1", "RGB"),
# so only values long enough to be distinctive are used for the leak check.
MIN_SEARCHABLE = 5


@dataclass
class CrossCheck:
    available: bool
    version: str = ""
    before: dict[str, str] = field(default_factory=dict)
    after: dict[str, str] = field(default_factory=dict)
    leaked: list[str] = field(default_factory=list)
    error: str = ""
    command: str = ""

    @property
    def gone_groups(self) -> list[str]:
        return sorted(_groups(self.before) - _groups(self.after))

    @property
    def remaining_groups(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for key in self.after:
            counts[group_of(key)] = counts.get(group_of(key), 0) + 1
        return dict(sorted(counts.items()))


def _groups(tags: dict[str, str]) -> set[str]:
    return {group_of(k) for k in tags}


def available() -> bool:
    return shutil.which(TOOL) is not None


def version() -> str:
    try:
        out = subprocess.run([TOOL, "-ver"], capture_output=True, text=True,
                             timeout=TIMEOUT)
        return out.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def read_tags(path: str) -> dict[str, str]:
    """`Group:Tag -> value` for the metadata ExifTool reads out of a file.

    Raises nothing: this is a reporting path, and a file ExifTool cannot read is a
    result to show, not an exception to propagate into a successful scrub.
    """
    try:
        proc = subprocess.run([TOOL, "-json", *_READ_ARGS, path],
                              capture_output=True, timeout=TIMEOUT)
        payload = json.loads(proc.stdout.decode("utf-8", "replace") or "[]")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, ValueError):
        return {}
    if not payload:
        return {}
    return {k: str(v) for k, v in payload[0].items()
            if ":" in k and k.split(":", 1)[0] not in DERIVED_GROUPS}


def group_of(key: str) -> str:
    """`ZIP:Copy3:ZipFileName` -> `ZIP`. The copy number is family 4's doing and is
    not part of the group name a user would recognise."""
    return key.split(":", 1)[0]


def command_for(path: str) -> str:
    """The exact command a user can paste to check the output themselves."""
    return f"{TOOL} {' '.join(ARGS)} {shlex.quote(path)}"


def run(in_path: str, out_path: str, removed_values=()) -> CrossCheck:
    """Compare what ExifTool reads before and after, and look for what we claimed
    to remove."""
    cmd = command_for(out_path)
    if not available():
        return CrossCheck(available=False, command=cmd)

    before, after = read_tags(in_path), read_tags(out_path)
    check = CrossCheck(available=True, version=version(), before=before,
                       after=after, command=cmd)

    # The part that can fail: is a value we said we removed still readable?
    haystack = "\n".join(f"{k} {v}" for k, v in after.items()).lower()
    for value in removed_values:
        text = str(value).strip()
        if len(text) < MIN_SEARCHABLE or text.startswith("("):
            continue
        if text.lower() in haystack:
            check.leaked.append(text)
    return check


def render(check: CrossCheck) -> list[str]:
    """The lines the scrub report appends. Always ends with the command, because the
    point is that the user does not have to believe any of the lines above it."""
    if not check.available:
        return [
            "",
            "  Not independently checked: exiftool is not installed.",
            "  Check it yourself:",
            f"    brew install exiftool && {check.command}",
        ]

    lines = ["", f"  Checked with exiftool {check.version} — a different "
                 f"implementation, not us:"]
    lines.append(f"    {len(check.before)} metadata tags before "
                 f"→ {len(check.after)} after")

    gone = check.gone_groups
    if gone:
        lines.append(f"    gone entirely: {', '.join(gone)}")
    remaining = check.remaining_groups
    if remaining:
        shown = ", ".join(f"{g} ({n})" for g, n in remaining.items())
        lines.append(f"    still reported: {shown}")

    if check.leaked:
        # The whole reason this module exists. Loud, and never softened: our own
        # report said these were gone and a different reader can still see them.
        lines.append("")
        lines.append("  ** A value this report listed as removed is still readable "
                     "in the output:")
        for value in check.leaked:
            lines.append(f"    ** {value}")
        lines.append("  ** Treat the scrub as incomplete and please report this.")
    elif check.before:
        lines.append("    none of the removed values appear in the output")

    lines.append("  Check it yourself:")
    lines.append(f"    {check.command}")
    return lines
