"""Output durability against the harness: locks, cancellation, and resume.

These exercise the paths where a partial write would be worst — an output file
held open by another process, a run stopped mid-flight, and a resume after a
completed iteration.  In every case the previously good bytes must survive.
"""

from __future__ import annotations

import threading
import time

import pytest

from tests.integration.windows.conftest import HarnessOptions, HarnessProcess
from tests.integration.windows.helpers import (
    output_path,
    production_services,
    run_harness_workflow,
    run_harness_workflow_detailed,
)
from universal_rpa.application.run_control import RunControl
from universal_rpa.domain.errors import ErrorCode

KNOWN_GOOD = b"known-good\r\n"
TABLE_OUTPUT = "harness/table.csv"


@pytest.fixture
def locked_output(harness: HarnessProcess) -> object:
    destination = output_path(harness, TABLE_OUTPUT)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(KNOWN_GOOD)
    return destination


@pytest.mark.windows_e2e
def test_clipboard_table_is_extracted_and_committed(harness: HarnessProcess) -> None:
    report = run_harness_workflow("clipboard-table", harness)

    destination = output_path(harness, TABLE_OUTPUT)
    assert report.status == "success"
    assert destination.is_file()
    assert report.output_commits[-1].committed is True
    assert report.output_commits[-1].row_count == 3
    assert destination.read_bytes().startswith(b"\xef\xbb\xbf")


@pytest.mark.windows_e2e
@pytest.mark.parametrize(
    "harness_options",
    [HarnessOptions()],
    ids=["default"],
)
def test_a_locked_destination_preserves_the_previous_bytes(
    harness: HarnessProcess, locked_output: object
) -> None:
    destination = locked_output  # type: ignore[assignment]
    # Deliberately not a context manager: the lock must outlive the run below.
    holder = open(destination, "r+b")
    try:
        import msvcrt

        msvcrt.locking(holder.fileno(), msvcrt.LK_NBLCK, 1)
        report = run_harness_workflow("clipboard-table", harness)
    finally:
        holder.close()

    assert report.status == "failed"
    assert report.results[-1].error_code is ErrorCode.OUTPUT_UNAVAILABLE
    assert destination.read_bytes() == KNOWN_GOOD  # type: ignore[union-attr]
    assert not list(destination.parent.glob("*.universal-rpa.tmp"))  # type: ignore[union-attr]


@pytest.mark.windows_e2e
def test_cancellation_stops_the_run_and_leaves_no_partial_output(
    harness: HarnessProcess, locked_output: object
) -> None:
    destination = locked_output  # type: ignore[assignment]
    services = production_services(harness)
    control = RunControl()

    def cancel_soon() -> None:
        time.sleep(0.3)
        control.cancel()

    canceller = threading.Thread(target=cancel_soon, daemon=True)
    canceller.start()
    report = run_harness_workflow("clipboard-table", harness, services=services, control=control)
    canceller.join(5.0)

    assert report.status in {"cancelled", "success"}
    if report.status == "cancelled":
        assert destination.read_bytes() == KNOWN_GOOD  # type: ignore[union-attr]
        assert not list(destination.parent.glob("*.universal-rpa.tmp"))  # type: ignore[union-attr]


@pytest.mark.windows_e2e
def test_a_completed_run_marks_its_checkpoint_terminal(harness: HarnessProcess) -> None:
    services = production_services(harness)

    outcome = run_harness_workflow_detailed("clipboard-table", harness, services=services)

    checkpoints = sorted((harness.root / "appdata").rglob("*.terminal.json"))
    assert outcome.report.status == "success"
    assert checkpoints, "a successful run must leave a terminal checkpoint"
    assert not sorted((harness.root / "appdata").rglob("*.journal.json"))


@pytest.mark.windows_e2e
def test_discovery_finds_no_resumable_state_after_success(harness: HarnessProcess) -> None:
    from tests.integration.windows.helpers import build_run_request, scenario_workflow

    services = production_services(harness)
    execution = services.execution_service
    assert execution is not None
    run_harness_workflow("clipboard-table", harness, services=services)

    found = execution.discover_resumable(
        build_run_request(harness, scenario_workflow("clipboard-table"))
    )

    assert all(not candidate.resumable for candidate in found)
