"""Generate the Excel Pareto matrix (`xlsx`) from real harness runs.

What is measured, and what is not, is in `office_matrix.py`'s docstring: A1@F1 in
every producer shape this machine can write, A2@F1 per channel (`e_office.py`);
F2/F3 not built.

Run:  ./.venv/bin/python -m tests.scrub.gen_matrix_xlsx
"""
from __future__ import annotations

from tests.scrub import office_matrix

TOOL = office_matrix.TOOL


def build_doc(tmpdir: str) -> dict:
    return office_matrix.build_doc("xlsx", tmpdir)


def main() -> str:
    return office_matrix.main("xlsx")


if __name__ == "__main__":
    print(main())
