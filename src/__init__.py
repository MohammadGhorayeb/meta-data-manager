"""Irreversible metadata scrubber — the tool under test.

`src/` is the product (a black-box `Scrubber` in harness terms); `tests/harness/`
is the verification infrastructure. The two are deliberately separate lifecycles
(harness README §2). The scrubber is exercised by the harness via its CLI
(`python -m src.scrub {in} {out} --fidelity {F1|F2|F3}`) wrapped in a
`SubprocessScrubber`.
"""
