from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, get_type_hints
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from universal_rpa.domain.errors import (
    ErrorCode,
    RpaError,
    ValidationIssue,
    ValidationReport,
)
from universal_rpa.domain.results import (
    ActionResult,
    LoopCursor,
    OutputCommit,
    RunReport,
    TableData,
    aggregate_run_status,
)
from universal_rpa.domain.types import FrozenJsonObject, FrozenMapping


class _DefaultError:
    pass


_DEFAULT_ERROR = _DefaultError()


def action_result(
    *,
    status: str = "success",
    skip_reason: str | None = None,
    error_code: ErrorCode | str | _DefaultError | None = _DEFAULT_ERROR,
    safe_message: str = "",
    evidence: object | None = None,
) -> ActionResult:
    if status == "failed" and error_code is _DEFAULT_ERROR:
        error_code = ErrorCode.ACTION_FAILED
        safe_message = safe_message or "작업 수행 실패"
    if status == "cancelled" and error_code is _DEFAULT_ERROR:
        error_code = ErrorCode.CANCELLED
        safe_message = safe_message or "사용자가 실행을 취소했습니다"
    if error_code is _DEFAULT_ERROR:
        error_code = None
    return ActionResult.model_validate(
        {
            "run_id": uuid4(),
            "step_id": uuid4(),
            "iteration_path": (),
            "status": status,
            "started_at": datetime.now(UTC),
            "duration_ms": 1,
            "attempt_count": 1,
            "error_code": error_code,
            "safe_message": safe_message,
            "evidence": evidence or {},
            "skip_reason": skip_reason,
        }
    )


def run_report(
    *,
    status: str = "success",
    results: tuple[ActionResult, ...] = (),
    error_code: ErrorCode | str | None = None,
    safe_message: str = "",
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    output_commits: tuple[OutputCommit, ...] = (),
) -> RunReport:
    started = started_at or datetime.now(UTC)
    return RunReport.model_validate(
        {
            "run_id": uuid4(),
            "workflow_id": uuid4(),
            "workflow_revision": 1,
            "status": status,
            "started_at": started,
            "finished_at": finished_at or started,
            "error_code": error_code,
            "safe_message": safe_message,
            "results": results,
            "completed_iterations": 0,
            "output_commits": output_commits,
        }
    )


def output_commit(
    destination: Path,
    *,
    digest: str = "a" * 64,
    format: str = "csv",
    sheet_name: str | None = None,
) -> OutputCommit:
    return OutputCommit.model_validate(
        {
            "destination": destination,
            "format": format,
            "sheet_name": sheet_name,
            "row_count": 2,
            "sha256": digest,
            "headers_sha256": "b" * 64,
            "committed": True,
            "producer_step_id": uuid4(),
        }
    )


def test_safe_result_rejects_clipboard_body() -> None:
    with pytest.raises(ValidationError):
        ActionResult(
            run_id=uuid4(),
            step_id=uuid4(),
            iteration_path=(),
            status="failed",
            started_at=datetime.now(UTC),
            duration_ms=1,
            attempt_count=1,
            error_code="assertion_failed",
            safe_message="표 검증 실패",
            evidence={"clipboard_text": "secret rows"},
        )


def test_action_result_evidence_is_immutable_and_serializes_as_ordinary_json() -> None:
    source = {"adapter": "fake", "details": [{"row_count": 2}]}
    result = action_result(evidence=source)

    source["details"][0]["row_count"] = 99

    assert get_type_hints(ActionResult)["evidence"] == FrozenJsonObject
    assert isinstance(result.evidence, FrozenMapping)
    assert result.model_dump(mode="json")["evidence"] == {
        "adapter": "fake",
        "details": [{"row_count": 2}],
    }


def test_optional_absence_does_not_make_run_partial() -> None:
    results = [
        action_result(status="success"),
        action_result(status="skipped", skip_reason="if_present_absent"),
    ]
    assert aggregate_run_status(results) == "success"


def test_disabled_step_does_not_make_run_partial() -> None:
    assert (
        aggregate_run_status([action_result(status="skipped", skip_reason="disabled")]) == "success"
    )


def test_explicit_failed_row_skip_makes_run_partial() -> None:
    results = [
        action_result(status="success"),
        action_result(status="skipped", skip_reason="skip_iteration"),
    ]
    assert aggregate_run_status(results) == "partial"


def test_preflight_failure_is_typed_without_fabricating_action_result() -> None:
    report = run_report(
        status="failed",
        results=(),
        error_code=ErrorCode.ENVIRONMENT_MISMATCH,
        safe_message="대상 실행 환경이 기록 환경과 다릅니다",
    )
    assert report.results == ()
    assert report.error_code is ErrorCode.ENVIRONMENT_MISMATCH
    assert report.safe_message == "대상 실행 환경이 기록 환경과 다릅니다"


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_failed_and_cancelled_take_precedence(status: str) -> None:
    assert aggregate_run_status([action_result(status=status)]) == status


def test_cancelled_takes_precedence_over_failed_and_partial() -> None:
    results = [
        action_result(status="failed"),
        action_result(status="cancelled"),
        action_result(status="skipped", skip_reason="skip_iteration"),
    ]
    assert aggregate_run_status(results) == "cancelled"


def test_failed_takes_precedence_over_partial() -> None:
    results = [
        action_result(status="skipped", skip_reason="skip_iteration"),
        action_result(status="failed"),
    ]
    assert aggregate_run_status(results) == "failed"


def test_empty_action_results_aggregate_to_success() -> None:
    assert aggregate_run_status([]) == "success"


@pytest.mark.parametrize(
    ("status", "error_code", "safe_message", "skip_reason"),
    [
        ("success", ErrorCode.ACTION_FAILED, "failure", None),
        ("skipped", ErrorCode.ACTION_FAILED, "failure", "disabled"),
        ("failed", None, "failure", None),
        ("failed", ErrorCode.ACTION_FAILED, "   ", None),
        ("cancelled", ErrorCode.ACTION_FAILED, "cancelled", None),
        ("cancelled", ErrorCode.CANCELLED, "   ", None),
        ("success", None, "", "disabled"),
        ("failed", ErrorCode.ACTION_FAILED, "failure", "skip_iteration"),
        ("skipped", None, "", None),
    ],
)
def test_action_status_error_and_skip_reason_invariants(
    status: str,
    error_code: ErrorCode | None,
    safe_message: str,
    skip_reason: str | None,
) -> None:
    with pytest.raises(ValidationError):
        action_result(
            status=status,
            error_code=error_code,
            safe_message=safe_message,
            skip_reason=skip_reason,
        )


def test_action_result_requires_utc_timestamp() -> None:
    payload = action_result().model_dump()
    payload["started_at"] = datetime.now()
    with pytest.raises(ValidationError):
        ActionResult.model_validate(payload)

    payload["started_at"] = datetime.now(timezone(timedelta(hours=9)))
    with pytest.raises(ValidationError):
        ActionResult.model_validate(payload)


@pytest.mark.parametrize(
    ("status", "error_code", "safe_message"),
    [
        ("success", ErrorCode.ACTION_FAILED, "failure"),
        ("partial", ErrorCode.ACTION_FAILED, "failure"),
        ("failed", None, "failure"),
        ("failed", ErrorCode.ACTION_FAILED, " "),
        ("cancelled", ErrorCode.ACTION_FAILED, "cancelled"),
        ("cancelled", ErrorCode.CANCELLED, " "),
    ],
)
def test_run_status_error_invariants(
    status: str,
    error_code: ErrorCode | None,
    safe_message: str,
) -> None:
    with pytest.raises(ValidationError):
        run_report(status=status, error_code=error_code, safe_message=safe_message)


def test_run_report_requires_utc_ordered_timestamps() -> None:
    started = datetime.now(UTC)

    with pytest.raises(ValidationError):
        run_report(started_at=started.replace(tzinfo=None), finished_at=started)
    with pytest.raises(ValidationError):
        run_report(started_at=started, finished_at=started - timedelta(microseconds=1))


def test_cancelled_run_requires_cancelled_error_code() -> None:
    report = run_report(
        status="cancelled",
        error_code=ErrorCode.CANCELLED,
        safe_message="사용자가 실행을 취소했습니다",
    )
    assert report.error_code is ErrorCode.CANCELLED


def test_output_commit_enforces_format_specific_sheet_name() -> None:
    with pytest.raises(ValidationError):
        output_commit(Path("report.csv"), sheet_name="Sheet1")
    with pytest.raises(ValidationError):
        output_commit(Path("report.xlsx"), format="xlsx")
    with pytest.raises(ValidationError):
        output_commit(Path("report.xlsx"), format="xlsx", sheet_name="  ")

    commit = output_commit(Path("report.xlsx"), format="xlsx", sheet_name="Results")
    assert commit.sheet_name == "Results"


def test_run_report_keeps_only_latest_commit_per_resolved_casefolded_destination(
    tmp_path: Path,
) -> None:
    first = output_commit(tmp_path / "exports" / ".." / "exports" / "Result.csv")
    other = output_commit(tmp_path / "other.csv", digest="c" * 64)
    latest = output_commit(tmp_path / "EXPORTS" / "result.csv", digest="d" * 64)

    report = run_report(output_commits=(first, other, latest))

    assert report.output_commits == (latest, other)


def test_table_data_copies_inputs_and_rejects_invalid_shape() -> None:
    headers = ["id", "name"]
    rows: list[list[Any]] = [[1, "first"]]
    table = TableData(headers=headers, rows=rows)  # type: ignore[arg-type]

    headers[0] = "mutated"
    rows[0][1] = "mutated"

    assert table.headers == ("id", "name")
    assert table.rows == ((1, "first"),)
    with pytest.raises(ValueError):
        TableData(headers=("id", ""), rows=())
    with pytest.raises(ValueError):
        TableData(headers=("id", "id"), rows=())
    with pytest.raises(ValueError):
        TableData(headers=("id", "name"), rows=((1,),))
    with pytest.raises(ValueError):
        TableData(headers=("nested",), rows=((["unsafe"],),))  # type: ignore[list-item]


def test_loop_cursor_and_output_commit_do_not_alias_input_sequences() -> None:
    cursors = [LoopCursor(loop_step_id=uuid4(), row_index=1)]
    commit = OutputCommit(
        destination=Path("report.csv"),
        format="csv",
        sheet_name=None,
        row_count=1,
        sha256="a" * 64,
        headers_sha256="b" * 64,
        committed=True,
        producer_step_id=uuid4(),
        producer_cursor=cursors,  # type: ignore[arg-type]
    )

    cursors.clear()

    assert len(commit.producer_cursor) == 1


def test_validation_report_filters_issues_in_source_order() -> None:
    warning = ValidationIssue(
        code=ErrorCode.ADAPTER_MISSING,
        path="steps.0",
        safe_message="어댑터를 사용할 수 없습니다",
        severity="warning",
    )
    error = ValidationIssue(
        code=ErrorCode.INVALID_SCHEMA,
        path="workflow",
        safe_message="워크플로 스키마가 올바르지 않습니다",
    )
    report = ValidationReport(issues=(warning, error))

    assert report.warnings == (warning,)
    assert report.errors == (error,)
    assert report.is_valid is False
    assert ValidationReport(issues=(warning,)).is_valid is True


def test_rpa_error_retains_only_safe_typed_information() -> None:
    evidence = FrozenMapping.from_mapping({"adapter": "fake"})
    error = RpaError(
        ErrorCode.TARGET_NOT_FOUND,
        "대상 요소를 찾을 수 없습니다",
        evidence,
    )

    assert str(error) == "대상 요소를 찾을 수 없습니다"
    assert error.args == ("대상 요소를 찾을 수 없습니다",)
    assert error.code is ErrorCode.TARGET_NOT_FOUND
    assert error.evidence == evidence
    assert not hasattr(error, "raw_exception")


def test_result_models_are_frozen_and_reject_unknown_fields() -> None:
    result = action_result()

    with pytest.raises(ValidationError):
        result.status = "failed"
    with pytest.raises(ValidationError):
        ActionResult.model_validate({**result.model_dump(), "unknown": True})


def test_exact_error_code_values_are_stable_for_serialization() -> None:
    expected = {
        "invalid_schema",
        "unsupported_schema",
        "adapter_missing",
        "action_unsupported",
        "target_not_found",
        "target_ambiguous",
        "environment_mismatch",
        "foreground_mismatch",
        "condition_timeout",
        "assertion_failed",
        "data_source_invalid",
        "secret_missing",
        "output_unavailable",
        "action_failed",
        "checkpoint_invalid",
        "resume_mismatch",
        "resume_unsafe",
        "cancelled",
        "internal_error",
    }
    assert {code.value for code in ErrorCode} == expected


def test_loop_cursor_rejects_negative_rows() -> None:
    with pytest.raises(ValidationError):
        LoopCursor(loop_step_id=UUID(int=0), row_index=-1)
