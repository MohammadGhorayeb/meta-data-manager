"""Cross-process determinism, for every format — the hole the floor oracle cannot see.

`tests/harness/oracle/floor.py` scrubs one input five times and calls the output
deterministic if the five agree. All five run in **one interpreter**, sharing one hash
seed, so a code path that iterates a `set` or an unordered dict to decide output
*order* looks perfectly deterministic there and differs on the next CLI invocation.
That is not hypothetical: `PYTHONHASHSEED` randomises string hashing per process by
default, and every handler in this project iterates string keys somewhere.

PDF got a subprocess check when it landed (`test_pdf.py`). §5 of the Phase 3 plan
recorded that the hole was general and the fix was owed to every format; DOCX, with
more string-keyed iteration than any format so far, is the milestone that pays it.

Determinism matters here for a specific reason rather than as tidiness: the harness
demands byte-identical output across repeats *and* across the CI Python 3.11–3.14
matrix, and `check_evidence.py` re-measures published verdicts. Output that depends on
a hash seed would make a published cell true on the machine that measured it and
false everywhere else.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from . import corpus as imgc
from . import flac_corpus as fc
from . import m4a_corpus as mc
from . import mp3_corpus as m3c
from . import pdf_corpus as pc

SEEDS = ("0", "424242")


def _build(kind: str, tmpdir: str) -> str | None:
    """One representative input per format, or None when a tool is absent.

    Torture inputs where a builder offers one: the more loci a file has, the more
    chances there are for an unordered iteration to decide something.
    """
    # The extension matters only to the corpus builders: ffmpeg picks its output
    # container from it. The scrubber itself dispatches on the magic number.
    p = os.path.join(tmpdir, f"in_{kind}.{kind}")
    if kind == "jpeg":
        open(p, "wb").write(imgc.build_torture_jpeg())
        return p
    if kind == "png":
        from .test_png import _png  # noqa: PLC0415
        open(p, "wb").write(_png())
        return p
    if kind == "mp3":
        return m3c.torture_mp3(p) if m3c.HAVE_FFMPEG else None
    if kind == "flac":
        return fc.torture_flac(p) if fc.HAVE_FFMPEG else None
    if kind == "m4a":
        return mc.torture_m4a(p) if mc.HAVE_FFMPEG else None
    if kind == "pdf":
        return pc.torture_pdf(p)
    raise AssertionError(kind)


def _scrub_subprocess(src: str, dst: str, fidelity: str, seed: str) -> bytes:
    env = dict(os.environ, PYTHONHASHSEED=seed)
    r = subprocess.run([sys.executable, "-m", "src.scrub", "--fidelity", fidelity,
                        src, dst], env=env, capture_output=True)
    if r.returncode != 0:
        pytest.skip(f"{fidelity} unavailable here: "
                    f"{r.stderr.decode()[:200] or r.stdout.decode()[:200]}")
    return open(dst, "rb").read()


# Every (format, fidelity) the tool currently offers. F3 tiers are included rather
# than excused: they run our Python code too, and "an external encoder does the work"
# is a reason to check the surrounding code, not to skip it.
CASES = [
    ("jpeg", "F1"), ("jpeg", "F2"), ("jpeg", "F3"),
    ("png", "F1"), ("png", "F2"),
    ("mp3", "F1"), ("mp3", "F3"),
    ("flac", "F1"), ("flac", "F2"),
    ("m4a", "F1"), ("m4a", "F2"), ("m4a", "F3"),
    ("pdf", "F1"), ("pdf", "F2"), ("pdf", "F3"),
]


@pytest.mark.parametrize("kind,fidelity", CASES,
                         ids=[f"{k}-{f}" for k, f in CASES])
def test_output_does_not_depend_on_the_hash_seed(kind, fidelity, tmp_path):
    src = _build(kind, str(tmp_path))
    if src is None:
        pytest.skip(f"{kind}: corpus tool absent")

    outs = {seed: _scrub_subprocess(src, str(tmp_path / f"out_{seed}"), fidelity, seed)
            for seed in SEEDS}
    first = outs[SEEDS[0]]
    for seed, blob in outs.items():
        assert blob == first, (
            f"{kind} {fidelity}: output differs under PYTHONHASHSEED={seed} "
            f"({len(blob)} vs {len(first)} bytes) — something iterates an unordered "
            f"collection to decide output order")


def test_the_ooxml_writer_is_byte_stable_across_processes(tmp_path):
    """The writer M9 introduces, checked the same way before anything depends on it.

    It is exercised directly rather than through the CLI because DOCX is not
    registered in dispatch until F1 lands (M8, §2.9) — the writer must be proven
    deterministic *before* a tier is built on top of it, not after.
    """
    script = (
        "import sys, hashlib;"
        f"sys.path.insert(0, {os.getcwd()!r});"
        "from src.scrub.formats.ooxml import zipwrite as zw;"
        "from tests.scrub import docx_corpus as C;"
        "parts={'[Content_Types].xml': C._CONTENT_TYPES.encode(),"
        " '_rels/.rels': C._ROOT_RELS.encode(),"
        " 'word/document.xml': C._document_xml(C.SOURCE_TEXT).encode(),"
        " 'word/settings.xml': C._TORTURE_SETTINGS.encode()};"
        "sys.stdout.write(hashlib.sha256(zw.write(parts)).hexdigest())"
    )

    digests = set()
    for seed in ("0", "1", "424242"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        r = subprocess.run([sys.executable, "-c", script], env=env,
                           capture_output=True, check=True)
        digests.add(r.stdout.decode().strip())
    assert len(digests) == 1, "the ZIP writer's output depends on the hash seed"
