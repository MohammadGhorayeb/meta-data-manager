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
import os
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
    """ExifTool's version, for the report's header.

    Only used when a read has not already supplied it: `_read` lifts the version out
    of the JSON it is already fetching, so the common path spends **no** extra
    process on it. Asking `exiftool -ver` separately cost a third of the whole
    check's runtime for a string we were already being handed.
    """
    try:
        out = subprocess.run([TOOL, "-ver"], capture_output=True, text=True,
                             timeout=TIMEOUT)
        return out.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def read_tags(path: str) -> tuple[dict[str, str], str]:
    """`(tags, error)` — see `_read`, which also reports the version."""
    tags, error, _ = _read(path)
    return tags, error


def _read(path: str) -> tuple[dict[str, str], str, str]:
    """`(Group:Tag -> value, error)` for the metadata ExifTool reads out of a file.

    The error string is what separates *"ExifTool read this file and it holds no
    metadata"* from *"ExifTool did not read this file"*. Conflating them is the worst
    failure this module could have: a read that silently returned nothing rendered as
    `0 tags before → 0 after, none of the removed values appear`, which is exactly
    what a clean scrub looks like. A check that reassures on its own failure is worse
    than no check.

    Raises nothing: this is a reporting path, and a file ExifTool cannot read is a
    result to show, not an exception to propagate into a successful scrub.
    """
    # Absolute, always. A relative path beginning with `-` is parsed by ExifTool as
    # an OPTION rather than a filename, so `read_tags("-photo.jpg")` read nothing at
    # all -- and an absolute path also makes the command we print work from any
    # directory the reader happens to be in.
    path = os.path.abspath(path)
    try:
        proc = subprocess.run([TOOL, "-json", *_READ_ARGS, path],
                              capture_output=True, timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {}, f"exiftool could not be run ({exc})", ""

    raw = proc.stdout.decode("utf-8", "replace").strip()
    if not raw:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        return {}, f"exiftool returned nothing ({detail or 'no output'})", ""
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        return {}, f"exiftool output could not be parsed ({exc})", ""
    if not payload:
        return {}, "exiftool reported no file", ""

    entry = payload[0]
    ver = str(entry.get("ExifTool:ExifToolVersion", "")).strip()
    return ({k: str(v) for k, v in entry.items()
             if ":" in k and group_of(k) not in DERIVED_GROUPS}, "", ver)


def group_of(key: str) -> str:
    """`ZIP:Copy3:ZipFileName` -> `ZIP`. The copy number is family 4's doing and is
    not part of the group name a user would recognise."""
    return key.split(":", 1)[0]


def command_for(path: str) -> str:
    """The exact command a user can paste to check the output themselves.

    Absolute and shell-quoted, so it works from whatever directory they are in and
    survives a filename with spaces or a leading dash.
    """
    return f"{TOOL} {' '.join(ARGS)} {shlex.quote(os.path.abspath(path))}"


def run(in_path: str, out_path: str, removed_values=()) -> CrossCheck:
    """Compare what ExifTool reads before and after, and look for what we claimed
    to remove."""
    cmd = command_for(out_path)
    if not available():
        return CrossCheck(available=False, command=cmd)

    before, err_before, ver = _read(in_path)
    after, err_after, ver_after = _read(out_path)
    check = CrossCheck(available=True, version=ver or ver_after or version(),
                       before=before, after=after, command=cmd,
                       error="; ".join(e for e in (err_before, err_after) if e))
    if check.error:
        # Nothing below can be trusted if a read failed, so do not run the leak
        # search and do not let the render imply a clean result.
        return check

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

    if check.error:
        return [
            "",
            f"  Not independently checked: {check.error}.",
            "  This is a failure of the check, NOT a clean result.",
            "  Check it yourself:",
            f"    {check.command}",
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
