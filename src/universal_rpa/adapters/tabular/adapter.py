"""Output-only tabular automation adapter.

Input loading stays behind M3's ``DataSourcePort``; this adapter never reads a
workflow input.  It commits an already-extracted table and observes the
resulting file, retaining only shape and digest evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from universal_rpa.adapters.tabular.output import AtomicTableWriter, TableOutputSpec
from universal_rpa.domain.conditions import AssertionSpec, ConditionSpec, TableAssertionSpec
from universal_rpa.domain.errors import ErrorCode, RpaError, ValidationIssue
from universal_rpa.domain.results import OutputCommit, TableData
from universal_rpa.domain.targets import RuntimeEnvironment, TargetSpec
from universal_rpa.domain.types import FrozenJsonObject, FrozenJsonValue, FrozenMapping
from universal_rpa.domain.workflow import ActionStep, OutputRelativePath
from universal_rpa.ports.automation import (
    ActionRequest,
    AdapterActionResult,
    AdapterDescriptor,
    AssertionObservation,
    CancellationToken,
    ConditionObservation,
    ExecutionContext,
    TargetCaptureRequest,
    TargetCaptureResult,
    TargetValidationMode,
)

TABULAR_ADAPTER_VERSION = "1.0.0"

SAVE_TABLE = "tabular.save_table"
FILE_EXISTS = "tabular.file_exists"
FILE_STABLE = "tabular.file_stable"


def _issue(code: ErrorCode, path: str, message: str) -> tuple[ValidationIssue, ...]:
    return (ValidationIssue(code=code, path=path, safe_message=message),)


@dataclass(frozen=True, slots=True)
class _FileStamp:
    size: int
    modified_ns: int


def _output_path_of(expected: FrozenJsonValue) -> str:
    if isinstance(expected, FrozenMapping):
        for key in ("output_path", "path"):
            value = expected.get(key)
            if isinstance(value, str):
                return value
    elif isinstance(expected, str):
        return expected
    raise RpaError(ErrorCode.INVALID_SCHEMA, "출력 파일 조건에는 상대 출력 경로가 필요합니다.")


def _spec_from_parameters(parameters: FrozenJsonObject) -> TableOutputSpec:
    output_path = parameters.get("output_path")
    if not isinstance(output_path, str):
        output_path = parameters.get("path")
    if not isinstance(output_path, str):
        raise RpaError(ErrorCode.INVALID_SCHEMA, "표 저장 작업에는 출력 경로가 필요합니다.")
    raw_format = parameters.get("format", "csv")
    output_format: Literal["csv", "xlsx"]
    if raw_format == "csv":
        output_format = "csv"
    elif raw_format == "xlsx":
        output_format = "xlsx"
    else:
        raise RpaError(ErrorCode.INVALID_SCHEMA, "지원하지 않는 출력 형식입니다.")
    sheet_name = parameters.get("sheet_name")
    if sheet_name is not None and not isinstance(sheet_name, str):
        raise RpaError(ErrorCode.INVALID_SCHEMA, "시트 이름은 문자열이어야 합니다.")
    raw_headers = parameters.get("required_headers")
    if raw_headers is None:
        headers: frozenset[str] = frozenset()
    elif isinstance(raw_headers, tuple):
        if any(not isinstance(header, str) for header in raw_headers):
            raise RpaError(ErrorCode.INVALID_SCHEMA, "필수 열 이름은 문자열이어야 합니다.")
        headers = frozenset(header for header in raw_headers if isinstance(header, str))
    else:
        raise RpaError(ErrorCode.INVALID_SCHEMA, "필수 열 목록이 올바르지 않습니다.")
    try:
        return TableOutputSpec(
            format=output_format,
            relative_path=OutputRelativePath(output_path),
            required_headers=headers,
            sheet_name=sheet_name,
        )
    except ValueError:
        raise RpaError(ErrorCode.INVALID_SCHEMA, "표 저장 설정이 올바르지 않습니다.") from None


class TabularAutomationAdapter:
    def __init__(self, writer: AtomicTableWriter | None = None) -> None:
        self._writer = writer or AtomicTableWriter()
        self._committed_saves = 0
        self._stamps: dict[str, _FileStamp] = {}

    @property
    def adapter_id(self) -> str:
        return "tabular"

    @property
    def committed_saves(self) -> int:
        return self._committed_saves

    def descriptor(self) -> AdapterDescriptor:
        return AdapterDescriptor(
            adapter_id=self.adapter_id,
            implementation_version=TABULAR_ADAPTER_VERSION,
            supports_target_capture=False,
            actions=frozenset({SAVE_TABLE}),
            conditions=frozenset({FILE_EXISTS, FILE_STABLE}),
            assertions=frozenset(),
            verification_by_action=FrozenMapping(((SAVE_TABLE, "intrinsic"),)),
            idempotent_actions=frozenset({SAVE_TABLE}),
            retryable_errors_by_action=FrozenMapping(
                ((SAVE_TABLE, frozenset({ErrorCode.OUTPUT_UNAVAILABLE})),)
            ),
            assertions_by_action=FrozenMapping(((SAVE_TABLE, frozenset()),)),
            assertion_input_kind=FrozenMapping.empty(),
        )

    def capture_target(
        self, request: TargetCaptureRequest, cancellation: CancellationToken
    ) -> TargetCaptureResult:
        del request, cancellation
        return TargetCaptureResult(
            target=None,
            candidates=(),
            preview_png=None,
            issues=_issue(
                ErrorCode.ACTION_UNSUPPORTED,
                "target",
                "표 저장은 화면 대상 캡처를 지원하지 않습니다.",
            ),
        )

    def validate_action_spec(self, step: ActionStep) -> tuple[ValidationIssue, ...]:
        if step.action_type != SAVE_TABLE:
            return _issue(
                ErrorCode.ACTION_UNSUPPORTED, "action_type", "지원하지 않는 표 작업입니다."
            )
        if step.target is not None or step.value is not None:
            return _issue(
                ErrorCode.INVALID_SCHEMA,
                "target",
                "표 저장은 화면 대상이나 직접 입력값을 사용하지 않습니다.",
            )
        try:
            _spec_from_parameters(step.parameters)
        except RpaError as error:
            return _issue(error.code, "parameters", error.safe_message)
        return ()

    def validate_condition_spec(self, condition: ConditionSpec) -> tuple[ValidationIssue, ...]:
        if condition.condition_type not in {FILE_EXISTS, FILE_STABLE}:
            return _issue(
                ErrorCode.ACTION_UNSUPPORTED, "condition_type", "지원하지 않는 표 조건입니다."
            )
        if condition.target is not None:
            return _issue(
                ErrorCode.INVALID_SCHEMA, "target", "표 조건은 화면 대상을 사용하지 않습니다."
            )
        try:
            OutputRelativePath(_output_path_of(condition.expected))
        except RpaError as error:
            return _issue(error.code, "expected", error.safe_message)
        except ValueError:
            return _issue(
                ErrorCode.INVALID_SCHEMA,
                "expected",
                "출력 경로가 출력 폴더의 안전한 상대 경로가 아닙니다.",
            )
        return ()

    def validate_assertion_spec(
        self, assertion: AssertionSpec | TableAssertionSpec
    ) -> tuple[ValidationIssue, ...]:
        del assertion
        return _issue(
            ErrorCode.INVALID_SCHEMA,
            "assertion",
            "표 저장은 저장 자체로 검증되며 별도 검증을 지원하지 않습니다.",
        )

    def validate_target(
        self, target: TargetSpec, runtime: RuntimeEnvironment, mode: TargetValidationMode
    ) -> tuple[ValidationIssue, ...]:
        del target, runtime, mode
        return _issue(ErrorCode.INVALID_SCHEMA, "target", "표 저장 작업에는 대상이 없습니다.")

    def execute(
        self, request: ActionRequest, context: ExecutionContext, cancellation: CancellationToken
    ) -> AdapterActionResult:
        try:
            cancellation.raise_if_cancelled()
            if request.action_type != SAVE_TABLE:
                return AdapterActionResult(
                    output=None,
                    evidence=FrozenMapping.empty(),
                    error_code=ErrorCode.ACTION_UNSUPPORTED,
                    safe_message="지원하지 않는 표 작업입니다.",
                )
            table = request.value
            if not isinstance(table, TableData):
                raise RpaError(ErrorCode.INVALID_SCHEMA, "표 저장 작업에는 추출된 표가 필요합니다.")
            spec = _spec_from_parameters(request.parameters)
            commit = self._writer.save(
                table,
                spec,
                context.output_root,
                cancellation,
                context.step_id,
                context.iteration_cursor,
            )
            self._committed_saves += 1
            return AdapterActionResult(
                output=None,
                evidence=self._commit_evidence(commit),
                output_commit=commit,
            )
        except RpaError as error:
            return AdapterActionResult(
                output=None,
                evidence=error.evidence,
                error_code=error.code,
                safe_message=error.safe_message,
            )

    @staticmethod
    def _commit_evidence(commit: OutputCommit) -> FrozenMapping[str, FrozenJsonValue]:
        return FrozenMapping(
            (
                ("format", commit.format),
                ("sheet_name", commit.sheet_name),
                ("row_count", commit.row_count),
                ("sha256", commit.sha256),
                ("headers_sha256", commit.headers_sha256),
                ("committed", commit.committed),
            )
        )

    def evaluate_condition(
        self, condition: ConditionSpec, context: ExecutionContext, cancellation: CancellationToken
    ) -> ConditionObservation:
        cancellation.raise_if_cancelled()
        if condition.condition_type not in {FILE_EXISTS, FILE_STABLE}:
            raise RpaError(ErrorCode.ACTION_UNSUPPORTED, "지원하지 않는 표 조건입니다.")
        destination = self._resolve_condition_path(condition, context)
        if condition.condition_type == FILE_EXISTS:
            exists = destination.is_file()
            return ConditionObservation(
                satisfied=exists,
                observed=exists,
                evidence=FrozenMapping((("exists", exists),)),
            )
        return self._observe_stability(destination)

    @staticmethod
    def _resolve_condition_path(condition: ConditionSpec, context: ExecutionContext) -> Path:
        relative_value = _output_path_of(condition.expected)
        try:
            relative = OutputRelativePath(relative_value)
            return relative.resolve_under(Path(context.output_root))
        except (OSError, ValueError):
            raise RpaError(
                ErrorCode.INVALID_SCHEMA,
                "출력 경로가 출력 폴더의 안전한 상대 경로가 아닙니다.",
            ) from None

    def _observe_stability(self, destination: Path) -> ConditionObservation:
        key = str(destination).casefold()
        try:
            stat_result = destination.stat()
        except OSError:
            self._stamps.pop(key, None)
            return ConditionObservation(
                satisfied=False,
                observed=False,
                evidence=FrozenMapping((("exists", False),)),
            )
        current = _FileStamp(size=stat_result.st_size, modified_ns=stat_result.st_mtime_ns)
        previous = self._stamps.get(key)
        self._stamps[key] = current
        stable = previous == current
        return ConditionObservation(
            satisfied=stable,
            observed=stable,
            evidence=FrozenMapping((("exists", True), ("size", current.size), ("stable", stable))),
        )

    def evaluate_assertion(
        self,
        assertion: AssertionSpec | TableAssertionSpec,
        subject: FrozenJsonValue | TableData | OutputCommit | None,
        target: TargetSpec | None,
        context: ExecutionContext,
        cancellation: CancellationToken,
    ) -> AssertionObservation:
        del assertion, subject, target, context
        cancellation.raise_if_cancelled()
        raise RpaError(
            ErrorCode.INVALID_SCHEMA,
            "표 저장은 저장 자체로 검증되며 별도 검증을 지원하지 않습니다.",
        )


__all__ = ["TABULAR_ADAPTER_VERSION", "TabularAutomationAdapter"]
