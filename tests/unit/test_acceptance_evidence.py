"""Keep the MVP acceptance matrix honest.

The matrix is the document a reviewer reads to decide whether the MVP is done,
and its whole value is that every row points at something that actually runs.  A
renamed test would leave it looking complete while proving nothing, and nobody
re-reads a table of 20 rows by hand.  So the references are checked here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MATRIX_PATH = REPOSITORY_ROOT / "docs" / "validation" / "mvp-acceptance-evidence.md"

#: Documents the pilot itself generates; absent until a pilot has been run.
PENDING_PILOT_SUMMARIES = frozenset(
    {
        "docs/validation/mis-read-only-pilot-windows-10-x64.md",
        "docs/validation/mis-read-only-pilot-windows-11-x64.md",
    }
)

_REFERENCE = re.compile(r"`((?:tests|scripts|samples)/[^`]*?)`")
_MASTER_ROW = re.compile(r"^\|\s*(\d+)\s*\|", re.MULTILINE)


@pytest.fixture(scope="module")
def matrix_text() -> str:
    assert MATRIX_PATH.is_file(), f"{MATRIX_PATH} is missing"
    return MATRIX_PATH.read_text(encoding="utf-8")


def _references(text: str) -> list[str]:
    return sorted(set(_REFERENCE.findall(text)))


def test_the_matrix_covers_all_twenty_master_rows(matrix_text: str) -> None:
    rows = [int(number) for number in _MASTER_ROW.findall(matrix_text)]

    assert rows == list(range(1, 21))


def test_every_referenced_file_exists(matrix_text: str) -> None:
    missing = [
        reference
        for reference in _references(matrix_text)
        if not (REPOSITORY_ROOT / reference.partition("::")[0]).exists()
    ]

    assert missing == [], f"acceptance evidence points at missing files: {missing}"


def test_every_referenced_test_function_exists(matrix_text: str) -> None:
    missing: list[str] = []
    for reference in _references(matrix_text):
        path, _, node = reference.partition("::")
        if not node:
            continue
        source = REPOSITORY_ROOT / path
        if not source.exists() or f"def {node}(" not in source.read_text(encoding="utf-8"):
            missing.append(reference)

    assert missing == [], f"acceptance evidence points at missing tests: {missing}"


def test_no_row_is_marked_not_applicable(matrix_text: str) -> None:
    """The plan is explicit: missing evidence is a hard failure, not N/A."""

    for row in matrix_text.splitlines():
        if row.startswith("|") and "N/A" in row:
            pytest.fail(f"acceptance rows may not be N/A: {row.strip()}")


def test_the_pilot_summaries_are_declared_outstanding_until_they_exist(
    matrix_text: str,
) -> None:
    """Guards against the matrix claiming a pilot that has not happened."""

    for relative in sorted(PENDING_PILOT_SUMMARIES):
        if (REPOSITORY_ROOT / relative).exists():
            continue
        assert relative in matrix_text, (
            f"{relative} does not exist yet, so the matrix must still name it as outstanding"
        )
        assert "미완료" in matrix_text, "outstanding rows must be stated as incomplete"
