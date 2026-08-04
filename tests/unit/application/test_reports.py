"""The safe run report is deep-frozen and carries no recoverable input."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from universal_rpa.application.execution import RunStarted
from universal_rpa.application.reports import ReportProjector, SafeRunReportDocument
from universal_rpa.domain.errors import ErrorCode
from universal_rpa.domain.results import ActionResult, LoopCursor, OutputCommit, RunReport
from universal_rpa.domain.targets import RuntimeEnvironment
from universal_rpa.domain.types import FrozenMapping, thaw_json
from universal_rpa.infrastructure.redaction import redact_evidence

RUN_ID = UUID("00000000-0000-0000-0000-000000000701")
WORKFLOW_ID = UUID("00000000-0000-0000-0000-000000000702")
STEP_ID = UUID("00000000-0000-0000-0000-000000000703")
LOOP_ID = UUID("00000000-0000-0000-0000-000000000704")
STARTED_AT = datetime(2026, 8, 3, 9, 0, tzinfo=UTC)
FINISHED_AT = datetime(2026, 8, 3, 9, 5, tzinfo=UTC)


def runtime_environment() -> RuntimeEnvironment:
    return RuntimeEnvironment(
        interactive_desktop=True,
        process_id=41,
        process_executable=r"C:\Program Files\MIS\mis.exe",
        top_level_hwnd=901,
        window_title="2026년 7월 생산 실적 - 고객사",
        window_class="MisMainWindow",
        foreground_hwnd=901,
        dpi_x=144,
        dpi_y=144,
        client_width=1280,
        client_height=800,
        monitor_scale=1.5,
    )


def run_started() -> RunStarted:
    return RunStarted(
        run_id=RUN_ID,
        workflow_id=WORKFLOW_ID,
        workflow_name="월간 실적 수집",
        workflow_revision=4,
        step_labels=FrozenMapping(((STEP_ID, "실적 표 추출"),)),
        started_at=STARTED_AT,
        runtime=runtime_environment(),
    )


def success_result(row_index: int) -> ActionResult:
    return ActionResult(
        run_id=RUN_ID,
        step_id=STEP_ID,
        iteration_path=(row_index,),
        iteration_cursor=(LoopCursor(loop_step_id=LOOP_ID, row_index=row_index),),
        status="success",
        started_at=STARTED_AT,
        evidence=FrozenMapping((("row_count", 3),)),
    )


def failed_result(row_index: int) -> ActionResult:
    return ActionResult(
        run_id=RUN_ID,
        step_id=STEP_ID,
        iteration_path=(row_index,),
        iteration_cursor=(LoopCursor(loop_step_id=LOOP_ID, row_index=row_index),),
        status="failed",
        started_at=STARTED_AT,
        attempt_count=3,
        error_code=ErrorCode.CONDITION_TIMEOUT,
        safe_message="대기 조건이 시간 내에 충족되지 않았습니다.",
        evidence=FrozenMapping((("elapsed_ms", 5000),)),
    )


def skipped_result(row_index: int) -> ActionResult:
    return ActionResult(
        run_id=RUN_ID,
        step_id=STEP_ID,
        iteration_path=(row_index,),
        iteration_cursor=(LoopCursor(loop_step_id=LOOP_ID, row_index=row_index),),
        status="skipped",
        started_at=STARTED_AT,
        skip_reason="skip_iteration",
    )


def output_commit(destination: Path) -> OutputCommit:
    return OutputCommit(
        destination=destination,
        format="csv",
        sheet_name=None,
        row_count=3,
        sha256="a" * 64,
        headers_sha256="b" * 64,
        committed=True,
        producer_step_id=STEP_ID,
        producer_cursor=(LoopCursor(loop_step_id=LOOP_ID, row_index=0),),
    )


def run_report(
    *,
    results: tuple[ActionResult, ...] = (),
    commits: tuple[OutputCommit, ...] = (),
    status: str = "success",
) -> RunReport:
    terminal = status not in {"success", "partial"}
    return RunReport(
        run_id=RUN_ID,
        workflow_id=WORKFLOW_ID,
        workflow_revision=4,
        status=status,  # type: ignore[arg-type]
        started_at=STARTED_AT,
        finished_at=FINISHED_AT,
        error_code=ErrorCode.CONDITION_TIMEOUT if terminal else None,
        safe_message="대기 조건이 시간 내에 충족되지 않았습니다." if terminal else "",
        results=results,
        completed_iterations=len({result.iteration_cursor for result in results}),
        total_iterations=len({result.iteration_cursor for result in results}) or None,
        last_checkpoint_cursor=(LoopCursor(loop_step_id=LOOP_ID, row_index=1),),
        output_commits=commits,
    )


def safe_report(*, environment: dict[str, Any] | None = None) -> SafeRunReportDocument:
    document = ReportProjector().project(run_started(), run_report())
    if environment is None:
        return document
    return document.model_copy(update={"environment": redact_evidence(environment)})


def test_safe_report_recursively_freezes_source_mappings() -> None:
    environment = {"safe": {"dpi": 144}}

    document = safe_report(environment=environment)

    environment["safe"]["dpi"] = 96
    with pytest.raises(TypeError):
        document.environment["safe"]["dpi"] = 120  # type: ignore[index]
    assert document.environment["safe"]["dpi"] == 144


def test_nested_evidence_removes_text_clipboard_and_secret() -> None:
    source = {
        "safe": {"row_count": 3},
        "payload": {"text": "typed-value", "clipboard_text": "table-body"},
        "token": "credential",
    }

    sanitized = redact_evidence(source)

    encoded = json.dumps(thaw_json(sanitized), ensure_ascii=False)
    assert all(value not in encoded for value in ("typed-value", "table-body", "credential"))
    assert sanitized["safe"]["row_count"] == 3


def test_projected_environment_excludes_the_window_title_and_machine_identity() -> None:
    document = ReportProjector().project(run_started(), run_report())

    encoded = json.dumps(thaw_json(document.environment), ensure_ascii=False)
    assert "고객사" not in encoded
    assert "901" not in encoded
    assert document.environment["dpi_x"] == 144
    assert document.environment["process_executable"] == "mis.exe"


def test_iteration_counts_group_results_by_cursor() -> None:
    report = run_report(
        results=(success_result(0), success_result(0), failed_result(1), skipped_result(2)),
        status="failed",
    )

    document = ReportProjector().project(run_started(), report)

    assert document.total_iterations == 3
    assert document.successful_iterations == 1
    assert document.failed_iterations == 1
    assert document.skipped_iterations == 1


def test_failures_carry_the_step_label_attempts_and_typed_error(tmp_path: Path) -> None:
    report = run_report(results=(failed_result(1),), status="failed")

    document = ReportProjector().project(run_started(), report)

    assert len(document.failures) == 1
    failure = document.failures[0]
    assert failure["step_label"] == "실적 표 추출"
    assert failure["error_code"] == ErrorCode.CONDITION_TIMEOUT.value
    assert failure["attempt_count"] == 3
    assert thaw_json(failure["iteration_cursor"]) == [
        {"loop_step_id": str(LOOP_ID), "row_index": 1}
    ]


def test_outputs_report_relative_paths_and_digests_without_customer_roots(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "exports" / "out.csv"
    report = run_report(results=(success_result(0),), commits=(output_commit(destination),))

    document = ReportProjector().project(run_started(), report, output_root=tmp_path)

    assert len(document.outputs) == 1
    output = document.outputs[0]
    assert output["relative_path"] == "exports/out.csv"
    assert output["sha256"] == "a" * 64
    assert output["row_count"] == 3
    encoded = json.dumps(thaw_json(document.outputs[0]), ensure_ascii=False)
    assert str(tmp_path) not in encoded


def test_output_without_a_known_root_reports_only_the_file_name(tmp_path: Path) -> None:
    destination = tmp_path / "exports" / "out.csv"
    report = run_report(results=(success_result(0),), commits=(output_commit(destination),))

    document = ReportProjector().project(run_started(), report)

    assert document.outputs[0]["relative_path"] == "out.csv"


def test_last_checkpoint_is_a_compact_cursor_label() -> None:
    document = ReportProjector().project(run_started(), run_report())

    assert document.last_checkpoint == f"{LOOP_ID}#1"


def test_document_serializes_to_plain_json_without_frozen_containers() -> None:
    document = ReportProjector().project(
        run_started(), run_report(results=(failed_result(0),), status="failed")
    )

    encoded = json.loads(document.model_dump_json())

    assert encoded["run_id"] == str(RUN_ID)
    assert encoded["workflow_name"] == "월간 실적 수집"
    assert isinstance(encoded["environment"], dict)
    assert isinstance(encoded["failures"], list)
