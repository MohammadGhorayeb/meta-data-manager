"""Command-line entry: `scrub <in> <out> --fidelity F1|F2|F3`.

Fail-closed contract:
  - Read input, dispatch by magic, scrub, and — before writing — re-inspect the
    output for residual metadata (defense in depth: a handler bug that leaves a
    locus behind must not ship a file that looks clean).
  - Any failure exits non-zero and writes NO output file. The output is written
    atomically (temp + rename) so a crash mid-write can't leave a partial file.

Advisories are the one thing that is *not* fail-closed, and deliberately so. A
handler may report risks about the **input** — today only PDF, which warns when a
document looks like someone tried to redact it by drawing a box. Those are printed to
stderr and never change the exit code: the scrub did what it promised, and refusing to
scrub a file because it also has a redaction problem would help nobody. The warning
exists because a user who reads "scrubbed" as "redacted" is the one this tool could
most easily mislead.

After a successful scrub the CLI prints a **before/after report** of the metadata it
found and what happened to each field. It goes to stdout (advisories and errors go to
stderr), it never affects the exit code, and `--no-report` turns it off — because the
report necessarily echoes the values it just removed, which is exactly what makes it
useful on a terminal and a disclosure in a log. See `report.py`.

The report ends with a check by **ExifTool rather than by us** — a different
implementation reading the same file, since a handler bug that failed to remove a
field would also fail to report it. It can disagree, and says so loudly when it does.
`--no-verify` skips it. See `crosscheck.py`.

Exit codes (stable, so the harness/tests can assert on them):
  0 ok · 2 usage · 3 unsupported format · 4 parse error · 5 fidelity error
  6 content/residual error · 1 unexpected
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile

from . import crosscheck
from . import fidelity as fid
from . import report as rep
from .dispatch import default_dispatcher
from .errors import ContentError, FidelityError, ParseError, ScrubError, UnsupportedFormatError

_EXIT = {
    UnsupportedFormatError: 3,
    ParseError: 4,
    FidelityError: 5,
    ContentError: 6,
}


def _write_atomic(path: str, data: bytes) -> None:
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def scrub_file(in_path: str, out_path: str, fidelity: str,
               dispatcher=None) -> list[str]:
    """Scrub in_path -> out_path at the given fidelity. Raises ScrubError
    (fail-closed) on any problem; writes output only on full success.

    Returns any **advisories about the input** — things the caller should be told
    that are not failures of the scrub. See the module docstring.
    """
    return scrub_file_reported(in_path, out_path, fidelity, dispatcher)[0]


def scrub_file_reported(in_path: str, out_path: str, fidelity: str,
                        dispatcher=None, verify_with_exiftool: bool = False
                        ) -> tuple[list[str], rep.Report]:
    """The same scrub, also returning a before/after account of the metadata.

    Split from `scrub_file` rather than folded into it because the report is a
    *display* concern: every existing caller wants a scrub and an exit code, and a
    tuple return would have rippled through the harness and the matrix generators
    for a feature none of them use.
    """
    fid.validate(fidelity)
    with open(in_path, "rb") as f:
        data = f.read()

    dispatcher = dispatcher or default_dispatcher()
    handler = dispatcher.resolve(data)          # raises UnsupportedFormatError
    advisories = _advise(handler, data)
    scrubbed = handler.scrub(data, fidelity)    # raises ParseError/FidelityError

    verify = getattr(handler, "verify", None)
    if verify is not None:
        residuals = verify(scrubbed, fidelity)
        if residuals:
            raise ContentError(
                "post-scrub verification found residual metadata: "
                + "; ".join(residuals))

    _write_atomic(out_path, scrubbed)
    report = rep.build(handler, data, scrubbed, fidelity, advisories)
    report.advisories.extend(_kept(handler, scrubbed, fidelity))
    if verify_with_exiftool:
        # After the write, and never able to raise: an independent check is worth
        # having and is not worth failing a good scrub over.
        try:
            report.check = crosscheck.run(in_path, out_path,
                                          report.removed_values)
        except Exception:                                 # noqa: BLE001
            report.check = None
    return advisories, report


def _kept(handler, data: bytes, fidelity: str) -> list[str]:
    """What the tier knowingly left in the output. Swallowed on failure, like
    `_advise`: a reporting path must never fail a good scrub."""
    fn = getattr(handler, "kept", None)
    if fn is None:
        return []
    try:
        return list(fn(data, fidelity))
    except Exception:                                     # noqa: BLE001
        return []


def _advise(handler, data: bytes) -> list[str]:
    """Handler advisories about the input, never fatal.

    An advisory that crashed the scrub would be worse than no advisory at all — the
    user would lose a working scrub over an optional warning — so a failure here is
    swallowed rather than raised.
    """
    advise = getattr(handler, "advise", None)
    if advise is None:
        return []
    try:
        return list(advise(data))
    except Exception:
        return []


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="scrub",
        description="Irreversibly strip metadata from a file.")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--fidelity", default=fid.F1, choices=fid.ALL,
                   help="F1 bit-preserving (default), F2 lossless, F3 lossy")
    p.add_argument("--no-report", action="store_true",
                   help="do not print the before/after metadata report. The report "
                        "echoes the values it removed, which is useful on a terminal "
                        "and a disclosure in a log or a shared session")
    p.add_argument("--no-verify", action="store_true",
                   help="skip the independent exiftool check at the end of the "
                        "report (it reads the file twice, which costs a moment)")
    args = p.parse_args(argv)

    try:
        advisories, report = scrub_file_reported(
            args.input, args.output, args.fidelity,
            verify_with_exiftool=not (args.no_report or args.no_verify))
    except ScrubError as e:
        print(f"scrub: {type(e).__name__}: {e}", file=sys.stderr)
        return _EXIT.get(type(e), 1)
    except FileNotFoundError as e:
        print(f"scrub: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # unexpected: still fail closed, no output written
        print(f"scrub: unexpected {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    if not args.no_report:
        print(rep.render(report))
    else:
        for note in advisories:
            print(f"scrub: warning: {note}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
