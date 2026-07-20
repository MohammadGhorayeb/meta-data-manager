"""M2 gate: drive the real scrubber through the Phase 0 differential oracle.

This is the definition of "done" for JPEG F1 (implementation plan §"Done"):
content-identical / metadata-variant files, scrubbed, must show no
variant-correlated byte diff and an empty noise floor. We also run the
fingerprint guard over diverse inputs, and drive the tool once through the
harness's own `SubprocessScrubber` to prove the CLI wiring the harness will use.
"""
from __future__ import annotations

import sys

import pytest

from src.scrub import cli
from tests.harness.contract import ScrubResult, SubprocessScrubber, V
from tests.harness.oracle import fingerprint_guard, leak
from tests.harness.plugins.jpeg import JpegPlugin
from tests.scrub import corpus


class InProcessScrubber:
    """Harness `Scrubber` adapter that calls the CLI in-process (fast, no
    subprocess) while going through exactly the production code path."""
    name, version = "irreversible_scrubber", "0.1.0-p1"

    def run(self, in_path: str, out_path: str, fidelity: str) -> ScrubResult:
        try:
            cli.scrub_file(in_path, out_path, fidelity)
            return ScrubResult(ok=True, out_path=out_path, fidelity=fidelity)
        except Exception as e:  # surface as a failed ScrubResult, don't crash oracle
            return ScrubResult(ok=False, out_path=out_path, fidelity=fidelity,
                               returncode=1, stderr=str(e))


def test_a1_f1_no_variant_correlated_leak(tmp_path):
    variants = corpus.make_jpeg_a1_variants(str(tmp_path), n_variants=3, n_repeats=5)
    cell = leak.evaluate_a1(InProcessScrubber(), JpegPlugin(), variants, "F1",
                            n=5, sentinel_field="metadata_variant")
    assert cell.verdict == V.PASS, f"A1@F1 leaks: {[l.correlates_with for l in cell.leaks]}"
    assert cell.leaks == []
    assert cell.floor is not None and cell.floor.deterministic, \
        "noise floor must be empty (F1 is deterministic)"


def test_a1_floor_is_empty_for_our_scrubber(tmp_path):
    from tests.harness.oracle import floor
    variants = corpus.make_jpeg_a1_variants(str(tmp_path), n_variants=1, n_repeats=5)
    report, _ = floor.measure(InProcessScrubber(), JpegPlugin(),
                              variants[0][0], "F1", n=5)
    assert report.deterministic
    assert report.variable_loci == []


def test_fingerprint_guard_passes_over_diverse_inputs(tmp_path):
    inputs = corpus.diverse_jpeg_inputs(str(tmp_path), n=4)
    verdict, signatures = fingerprint_guard.evaluate(
        InProcessScrubber(), JpegPlugin(), inputs, "F1")
    assert verdict == "pass", f"scrubber fingerprint detected: {signatures}"


def test_a1_detects_a_deliberately_leaky_scrubber(tmp_path):
    """Guard against a vacuous pass: a scrubber that keeps the COM sentinel must
    be FAILED by the same oracle."""
    class LeakyScrubber(InProcessScrubber):
        name = "leaky"

        def run(self, in_path, out_path, fidelity):
            with open(in_path, "rb") as f:
                data = f.read()
            with open(out_path, "wb") as f:   # passthrough: metadata survives
                f.write(data)
            return ScrubResult(ok=True, out_path=out_path, fidelity=fidelity)

    variants = corpus.make_jpeg_a1_variants(str(tmp_path), n_variants=3, n_repeats=5)
    cell = leak.evaluate_a1(LeakyScrubber(), JpegPlugin(), variants, "F1", n=5)
    assert cell.verdict == V.FAIL
    assert any(l.kind == "variant_correlated" for l in cell.leaks)


@pytest.mark.filterwarnings("ignore:.*malformed MPO.*")
def test_subprocess_scrubber_contract(tmp_path):
    """Drive the tool through the harness's own SubprocessScrubber — the exact
    black-box contract a benchmark run uses."""
    src = tmp_path / "in.jpg"
    dst = tmp_path / "out.jpg"
    src.write_bytes(corpus.build_torture_jpeg())

    tmpl = f"{sys.executable} -m src.scrub {{in}} {{out}} --fidelity {{fidelity}}"
    scrubber = SubprocessScrubber("irreversible_scrubber", "0.1.0-p1", tmpl)
    # SubprocessScrubber runs with cwd=repo root implicitly via module path.
    import os
    cwd = os.getcwd()
    os.chdir(__import__("pathlib").Path(__file__).resolve().parents[2])
    try:
        result = scrubber.run(str(src), str(dst), "F1")
    finally:
        os.chdir(cwd)
    assert result.ok, result.stderr
    assert dst.exists()
