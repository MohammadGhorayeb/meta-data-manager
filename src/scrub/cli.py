"""Command-line entry: `scrub <in> <out> --fidelity F1|F2|F3`.

Fail-closed contract:
  - Read input, dispatch by magic, scrub, and — before writing — re-inspect the
    output for residual metadata (defense in depth: a handler bug that leaves a
    locus behind must not ship a file that looks clean).
  - Any failure exits non-zero and writes NO output file. The output is written
    atomically (temp + rename) so a crash mid-write can't leave a partial file.

Exit codes (stable, so the harness/tests can assert on them):
  0 ok · 2 usage · 3 unsupported format · 4 parse error · 5 fidelity error
  6 content/residual error · 1 unexpected
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile

from . import fidelity as fid
from .dispatch import default_dispatcher
from .errors import (ContentError, FidelityError, ParseError, ScrubError,
                     UnsupportedFormatError)

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
               dispatcher=None) -> None:
    """Scrub in_path -> out_path at the given fidelity. Raises ScrubError
    (fail-closed) on any problem; writes output only on full success."""
    fid.validate(fidelity)
    with open(in_path, "rb") as f:
        data = f.read()

    dispatcher = dispatcher or default_dispatcher()
    handler = dispatcher.resolve(data)          # raises UnsupportedFormatError
    scrubbed = handler.scrub(data, fidelity)    # raises ParseError/FidelityError

    verify = getattr(handler, "verify", None)
    if verify is not None:
        residuals = verify(scrubbed, fidelity)
        if residuals:
            raise ContentError(
                "post-scrub verification found residual metadata: "
                + "; ".join(residuals))

    _write_atomic(out_path, scrubbed)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="scrub",
        description="Irreversibly strip metadata from a file.")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--fidelity", default=fid.F1, choices=fid.ALL,
                   help="F1 bit-preserving (default), F2 lossless, F3 lossy")
    args = p.parse_args(argv)

    try:
        scrub_file(args.input, args.output, args.fidelity)
    except ScrubError as e:
        print(f"scrub: {type(e).__name__}: {e}", file=sys.stderr)
        return _EXIT.get(type(e), 1)
    except FileNotFoundError as e:
        print(f"scrub: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # unexpected: still fail closed, no output written
        print(f"scrub: unexpected {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
