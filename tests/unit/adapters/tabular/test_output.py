"""Containment, cancellation, and durability rules for committed tabular output.

``AtomicTableWriter`` checks cancellation at a fixed, documented sequence of
points so that every commit boundary is reachable from a test:

1. once on entry to :meth:`AtomicTableWriter.save`;
2. once before each serialized data row;
3. once before the temporary file is reopened and validated;
4. once immediately before the destination is replaced.

``_phase_call_index`` converts a boundary name into the ordinal of the
``raise_if_cancelled`` call that reaches it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest
from openpyxl import load_workbook

from universal_rpa.adapters.tabular.output import (
    TEMP_SUFFIX,
    AtomicTableWriter,
    TableOutputSpec,
    canonical_header_hash,
)
from universal_rpa.domain.errors import ErrorCode, RpaError
from universal_rpa.domain.results import LoopCursor, OutputCommit, TableData
from universal_rpa.domain.workflow import OutputRelativePath
from universal_rpa.infrastructure.checkpoint_store import Checkpoint, ResumeFingerprint
from universal_rpa.ports.automation import CancellationToken

STEP_ID = UUID("00000000-0000-0000-0000-000000000501")
LOOP_ID = UUID("00000000-0000-0000-0000-000000000502")
WORKFLOW_ID = UUID("00000000-0000-0000-0000-000000000503")
RUN_ID = UUID("00000000-0000-0000-0000-000000000504")
NOW = datetime(2026, 8, 3, tzinfo=UTC)


def table_data() -> TableData:
    return TableData(
        headers=("factory", "period", "amount"),
        rows=(
            ("A동", "2026-07", 100),
            ("B동", "2026-07", 200),
            ("C동", "2026-07", 300),
        ),
    )


def csv_output(relative_path: OutputRelativePath | str) -> TableOutputSpec:
    path = (
        relative_path
        if isinstance(relative_path, OutputRelativePath)
        else OutputRelativePath(relative_path)
    )
    return TableOutputSpec(format="csv", relative_path=path)


def xlsx_output(relative_path: str, sheet_name: str = "결과") -> TableOutputSpec:
    return TableOutputSpec(
        format="xlsx",
        relative_path=OutputRelativePath(relative_path),
        sheet_name=sheet_name,
    )


def token() -> CancellationToken:
    return CancellationToken()


class TrippingCancellation(CancellationToken):
    """Cancels itself on the *trip_at_call*-th ``raise_if_cancelled`` call."""

    def __init__(self, trip_at_call: int) -> None:
        super().__init__()
        self._trip_at_call = trip_at_call
        self.calls = 0

    def raise_if_cancelled(self) -> None:
        self.calls += 1
        if self.calls >= self._trip_at_call:
            self.cancel()
        super().raise_if_cancelled()


def _phase_call_index(phase: str, row_count: int) -> int:
    if phase == "entry":
        return 1
    if phase.startswith("row:"):
        return 1 + int(phase.partition(":")[2])
    if phase == "before_validation":
        return 2 + row_count
    if phase == "before_replace":
        return 3 + row_count
    raise AssertionError(f"unknown cancellation phase: {phase}")


def cancel_at(phase: str, row_count: int = 3) -> TrippingCancellation:
    return TrippingCancellation(_phase_call_index(phase, row_count))


def cancel_after_row(row_index: int) -> TrippingCancellation:
    return cancel_at(f"row:{row_index + 1}")


def save_csv(
    root: Path,
    relative_path: OutputRelativePath | str,
    *,
    cancellation: CancellationToken | None = None,
    producer_cursor: tuple[LoopCursor, ...] = (),
) -> OutputCommit:
    return AtomicTableWriter().save(
        table_data(),
        csv_output(relative_path),
        root,
        cancellation or token(),
        STEP_ID,
        producer_cursor,
    )


def failing_flush() -> object:
    def _flush(path: Path) -> None:
        raise OSError(f"destination flush refused: {path.name}")

    return _flush


def writer(**overrides: object) -> AtomicTableWriter:
    return AtomicTableWriter(**overrides)  # type: ignore[arg-type]


def test_relative_output_is_resolved_beneath_runtime_root(tmp_path: Path) -> None:
    root = tmp_path / "selected-output"

    commit = AtomicTableWriter().save(
        table_data(),
        csv_output(OutputRelativePath("exports/out.csv")),
        root,
        token(),
        STEP_ID,
        (LoopCursor(loop_step_id=LOOP_ID, row_index=2),),
    )

    assert commit.destination == (root / "exports" / "out.csv").resolve()
    assert commit.producer_step_id == STEP_ID
    assert commit.producer_cursor == (LoopCursor(loop_step_id=LOOP_ID, row_index=2),)


@pytest.mark.parametrize(
    "relative_path",
    ["../escape.csv", "C:/escape.csv", "//server/share/out.csv", "con.csv"],
)
def test_output_rejects_escape_absolute_unc_and_device_names(
    tmp_path: Path, relative_path: str
) -> None:
    with pytest.raises(RpaError) as error:
        save_csv(tmp_path, OutputRelativePath.model_construct(root=relative_path))

    assert error.value.code == ErrorCode.INVALID_SCHEMA
    assert list(tmp_path.iterdir()) == []


def test_cancel_during_serialization_preserves_old_bytes_and_removes_temp(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "out.csv"
    destination.write_bytes(b"known-good")
    cancellation = cancel_after_row(2)

    with pytest.raises(RpaError) as error:
        save_csv(tmp_path, "out.csv", cancellation=cancellation)

    assert error.value.code == ErrorCode.CANCELLED
    assert destination.read_bytes() == b"known-good"
    assert list(tmp_path.glob(f"*{TEMP_SUFFIX}")) == []


@pytest.mark.parametrize("cancel_point", ["before_validation", "before_replace"])
def test_cancel_at_commit_boundaries_preserves_destination(
    tmp_path: Path, cancel_point: str
) -> None:
    destination = tmp_path / "out.xlsx"
    destination.write_bytes(b"known-good")

    with pytest.raises(RpaError) as error:
        AtomicTableWriter().save(
            table_data(),
            xlsx_output("out.xlsx"),
            tmp_path,
            cancel_at(cancel_point),
            STEP_ID,
            (),
        )

    assert error.value.code == ErrorCode.CANCELLED
    assert destination.read_bytes() == b"known-good"
    assert list(tmp_path.glob(f"*{TEMP_SUFFIX}")) == []


def test_destination_flush_failure_restores_previous_bytes_and_never_commits(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "out.csv"
    destination.write_bytes(b"known-good")

    with pytest.raises(RpaError) as error:
        writer(flush_destination=failing_flush()).save(
            table_data(), csv_output("out.csv"), tmp_path, token(), STEP_ID, ()
        )

    assert error.value.code == ErrorCode.OUTPUT_UNAVAILABLE
    assert destination.read_bytes() == b"known-good"
    assert list(tmp_path.glob(f"*{TEMP_SUFFIX}")) == []


def test_flush_failure_for_a_new_destination_leaves_no_partial_file(tmp_path: Path) -> None:
    with pytest.raises(RpaError) as error:
        writer(flush_destination=failing_flush()).save(
            table_data(), csv_output("out.csv"), tmp_path, token(), STEP_ID, ()
        )

    assert error.value.code == ErrorCode.OUTPUT_UNAVAILABLE
    assert list(tmp_path.iterdir()) == []


def test_output_commit_contains_durable_hashes_and_producer_identity(
    tmp_path: Path,
) -> None:
    commit = save_csv(
        tmp_path,
        "out.csv",
        producer_cursor=(LoopCursor(loop_step_id=LOOP_ID, row_index=4),),
    )

    assert commit.committed is True
    assert commit.format == "csv"
    assert commit.sheet_name is None
    assert commit.row_count == 3
    assert commit.sha256 == sha256(commit.destination.read_bytes()).hexdigest()
    assert commit.headers_sha256 == canonical_header_hash(table_data().headers)
    assert commit.producer_step_id == STEP_ID
    assert commit.producer_cursor == (LoopCursor(loop_step_id=LOOP_ID, row_index=4),)


def test_csv_is_written_as_utf8_sig_and_reads_back_exactly(tmp_path: Path) -> None:
    commit = save_csv(tmp_path, "out.csv")

    raw = commit.destination.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    decoded = raw.decode("utf-8-sig").splitlines()
    assert decoded[0] == "factory,period,amount"
    assert decoded[1] == "A동,2026-07,100"
    assert len(decoded) == 4


def test_xlsx_is_written_to_the_requested_sheet(tmp_path: Path) -> None:
    commit = AtomicTableWriter().save(
        table_data(), xlsx_output("out.xlsx", "매출"), tmp_path, token(), STEP_ID, ()
    )

    assert commit.sheet_name == "매출"
    workbook = load_workbook(commit.destination, read_only=True)
    try:
        sheet = workbook["매출"]
        rows = [tuple(row) for row in sheet.values]
    finally:
        workbook.close()
    assert rows[0] == ("factory", "period", "amount")
    assert len(rows) == 4


def test_missing_required_header_fails_before_writing_anything(tmp_path: Path) -> None:
    spec = TableOutputSpec(
        format="csv",
        relative_path=OutputRelativePath("out.csv"),
        required_headers=frozenset({"factory", "unit_price"}),
    )

    with pytest.raises(RpaError) as error:
        AtomicTableWriter().save(table_data(), spec, tmp_path, token(), STEP_ID, ())

    assert error.value.code == ErrorCode.INVALID_SCHEMA
    assert list(tmp_path.iterdir()) == []


def test_replacing_an_existing_destination_leaves_no_rollback_copy(tmp_path: Path) -> None:
    destination = tmp_path / "out.csv"
    destination.write_bytes(b"stale")

    commit = save_csv(tmp_path, "out.csv")

    assert commit.destination.read_bytes() != b"stale"
    assert sorted(item.name for item in tmp_path.iterdir()) == ["out.csv"]


def _fingerprint() -> ResumeFingerprint:
    digest = "0" * 64
    return ResumeFingerprint(
        workflow_sha256=digest,
        resolved_inputs_sha256=digest,
        output_root_sha256=digest,
        data_sources=(),
        adapters=(),
        environment_sha256=digest,
    )


def _commit(destination: Path, digest: str) -> OutputCommit:
    return OutputCommit(
        destination=destination,
        format="csv",
        sheet_name=None,
        row_count=3,
        sha256=digest * 64,
        headers_sha256="a" * 64,
        committed=True,
        producer_step_id=STEP_ID,
    )


def test_checkpoint_keeps_only_latest_commit_per_normalized_destination(
    tmp_path: Path,
) -> None:
    old_commit = _commit(tmp_path / "out.csv", "1")
    new_commit = _commit(tmp_path / "OUT.csv", "2")

    checkpoint = Checkpoint(
        workflow_id=WORKFLOW_ID,
        run_id=RUN_ID,
        date_context_today="2026-08-03",
        date_context_run_date="2026-08-03",
        fingerprint=_fingerprint(),
        output_commits=(old_commit, new_commit),
        updated_at=NOW,
    )

    assert checkpoint.output_commits == (new_commit,)
