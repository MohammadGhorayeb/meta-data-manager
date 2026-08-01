"""The generated MP3 Pareto matrix validates and records honest verdicts:
A1 pass at F1/F3 (F2 n/a), A2 fail at F1 but pass at F3 (F2 n/a)."""
from __future__ import annotations

import pytest

from tests.harness.runner import matrix
from tests.scrub import gen_matrix_mp3
from tests.scrub import mp3_corpus as mc

pytestmark = pytest.mark.skipif(not mc.HAVE_FFMPEG, reason="ffmpeg not installed")


def _cell(doc, adv, fid):
    return next(c for c in doc["cells"]
               if c["adversary"] == adv and c["fidelity"] == fid)


def test_mp3_matrix_builds_and_validates(tmp_path):
    doc = gen_matrix_mp3.build_doc(str(tmp_path))
    matrix.validate(doc)   # raises on schema violation

    assert _cell(doc, "A1", "F1")["verdict"] == "pass"
    assert _cell(doc, "A1", "F2")["verdict"] == "not_applicable"
    assert _cell(doc, "A2", "F2")["verdict"] == "not_applicable"
    assert doc["scrubber_fingerprint"]["verdict"] == "pass"

    if mc.HAVE_LAME:
        # F3 = the A2 defense: encoder fingerprint survives F1, erased at F3.
        assert _cell(doc, "A1", "F3")["verdict"] == "pass"
        assert _cell(doc, "A2", "F1")["verdict"] == "fail"
        assert _cell(doc, "A2", "F3")["verdict"] == "pass"
