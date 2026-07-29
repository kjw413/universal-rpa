from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Literal

from universal_rpa.domain.conditions import (
    AssertionSpec,
    ConditionSpec,
    TableAssertionSpec,
)
from universal_rpa.domain.errors import ErrorCode, RpaError, ValidationIssue
from universal_rpa.domain.results import OutputCommit, TableData
from universal_rpa.domain.targets import RuntimeEnvironment, TargetSpec
from universal_rpa.domain.types import (
    FrozenJsonObject,
    FrozenJsonValue,
    FrozenMapping,
)
from universal_rpa.domain.workflow import ActionStep
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
    VerificationMode,
)


@dataclass(frozen=True, slots=True)
class FakeCall:
    operation: str
    payload: object


def _issue(code: ErrorCode, path: str, safe_message: str) -> ValidationIssue:
    return ValidationIssue(code=code, path=path, safe_message=safe_message)


def _empty_evidence() -> FrozenJsonObject:
    return FrozenMapping.empty()


def _default_descriptor(adapter_id: str, supports_target_capture: bool) -> AdapterDescriptor:
    action = f"{adapter_id}.read"
    assertion = f"{adapter_id}.equals"
    verification: FrozenMapping[str, VerificationMode] = FrozenMapping(
        ((action, "postcondition_or_assertion"),)
    )
    retryable: FrozenMapping[str, frozenset[ErrorCode]] = FrozenMapping(
        ((action, frozenset({ErrorCode.ACTION_FAILED})),)
    )
    compatible: FrozenMapping[str, frozenset[str]] = FrozenMapping(
        ((action, frozenset({assertion})),)
    )
    input_kinds: FrozenMapping[str, Literal["json", "table", "output_commit"]] = FrozenMapping(
        ((assertion, "json"),)
    )
    return AdapterDescriptor(
        adapter_id=adapter_id,
        implementation_version="1.0",
        supports_target_capture=supports_target_capture,
        actions=frozenset({action}),
        conditions=frozenset({f"{adapter_id}.ready"}),
        assertions=frozenset({assertion}),
        verification_by_action=verification,
        idempotent_actions=frozenset({action}),
        retryable_errors_by_action=retryable,
        assertions_by_action=compatible,
        assertion_input_kind=input_kinds,
    )


class FakeAutomationAdapter:
    """Deterministic in-memory adapter for contract and runner tests."""

    def __init__(
        self,
        adapter_id: str = "fake",
        *,
        descriptor: AdapterDescriptor | None = None,
        supports_target_capture: bool = True,
    ) -> None:
        self._adapter_id = adapter_id
        self._descriptor = (
            _default_descriptor(adapter_id, supports_target_capture)
            if descriptor is None
            else descriptor
        )
        self.script: deque[object] = deque()
        self._calls: list[FakeCall] = []

    @property
    def adapter_id(self) -> str:
        return self._adapter_id

    @property
    def calls(self) -> tuple[FakeCall, ...]:
        return tuple(self._calls)

    def descriptor(self) -> AdapterDescriptor:
        return self._descriptor

    def reset(self) -> None:
        self.script.clear()
        self._calls.clear()

    def _take_scripted(self, default: object) -> object:
        return self.script.popleft() if self.script else default

    def _result_from_error(self, error: RpaError) -> AdapterActionResult:
        return AdapterActionResult(
            output=None,
            evidence=error.evidence,
            error_code=error.code,
            safe_message=error.safe_message,
        )

    def _unsupported_result(self) -> AdapterActionResult:
        return AdapterActionResult(
            output=None,
            evidence=_empty_evidence(),
            error_code=ErrorCode.ACTION_UNSUPPORTED,
            safe_message="지원하지 않는 자동화 작업입니다",
        )

    def capture_target(
        self,
        request: TargetCaptureRequest,
        cancellation: CancellationToken,
    ) -> TargetCaptureResult:
        if cancellation.is_cancelled():
            return TargetCaptureResult(
                target=None,
                candidates=(),
                preview_png=None,
                issues=(_issue(ErrorCode.CANCELLED, "target", "대상 캡처가 취소되었습니다"),),
            )
        if not self._descriptor.supports_target_capture:
            return TargetCaptureResult(
                target=None,
                candidates=(),
                preview_png=None,
                issues=(
                    _issue(
                        ErrorCode.ACTION_UNSUPPORTED,
                        "target",
                        "이 어댑터는 대상 캡처를 지원하지 않습니다",
                    ),
                ),
            )

        scripted = self._take_scripted(1)
        if isinstance(scripted, TargetCaptureResult):
            self._calls.append(FakeCall("capture_target", request))
            return scripted
        if isinstance(scripted, RpaError):
            return TargetCaptureResult(
                target=None,
                candidates=(),
                preview_png=None,
                issues=(_issue(scripted.code, "target", scripted.safe_message),),
            )
        if isinstance(scripted, Exception):
            return TargetCaptureResult(
                target=None,
                candidates=(),
                preview_png=None,
                issues=(
                    _issue(
                        ErrorCode.INTERNAL_ERROR,
                        "target",
                        "대상 캡처 중 내부 오류가 발생했습니다",
                    ),
                ),
            )
        if not isinstance(scripted, int) or isinstance(scripted, bool):
            return TargetCaptureResult(
                target=None,
                candidates=(),
                preview_png=None,
                issues=(
                    _issue(
                        ErrorCode.INVALID_SCHEMA,
                        "target",
                        "가짜 대상 스크립트가 올바르지 않습니다",
                    ),
                ),
            )
        count = max(0, min(scripted, 2))
        candidates = tuple(
            TargetSpec(
                adapter_id=self.adapter_id,
                payload=FrozenMapping.from_mapping({"candidate": index + 1}),
            )
            for index in range(count)
        )
        issues = (
            (
                _issue(
                    ErrorCode.TARGET_AMBIGUOUS,
                    "target",
                    "대상이 여러 개 발견되었습니다",
                ),
            )
            if count == 2
            else ()
        )
        self._calls.append(FakeCall("capture_target", request))
        return TargetCaptureResult(
            target=candidates[0] if count == 1 else None,
            candidates=candidates,
            preview_png=None,
            issues=issues,
        )

    def validate_action_spec(
        self,
        step: ActionStep,
    ) -> tuple[ValidationIssue, ...]:
        if step.action_type not in self._descriptor.actions:
            return (
                _issue(
                    ErrorCode.ACTION_UNSUPPORTED,
                    "action_type",
                    "지원하지 않는 자동화 작업입니다",
                ),
            )
        if step.target is not None and step.target.adapter_id != self.adapter_id:
            return (
                _issue(
                    ErrorCode.INVALID_SCHEMA,
                    "target.adapter_id",
                    "대상 어댑터가 작업과 일치하지 않습니다",
                ),
            )
        if step.parameters.get("invalid") is True:
            return (
                _issue(
                    ErrorCode.INVALID_SCHEMA,
                    "parameters",
                    "작업 매개변수가 올바르지 않습니다",
                ),
            )
        return ()

    def validate_condition_spec(
        self,
        condition: ConditionSpec,
    ) -> tuple[ValidationIssue, ...]:
        if condition.condition_type not in self._descriptor.conditions:
            return (
                _issue(
                    ErrorCode.ACTION_UNSUPPORTED,
                    "condition_type",
                    "지원하지 않는 조건입니다",
                ),
            )
        return ()

    def validate_assertion_spec(
        self,
        assertion: AssertionSpec | TableAssertionSpec,
    ) -> tuple[ValidationIssue, ...]:
        if assertion.assertion_type not in self._descriptor.assertions:
            return (
                _issue(
                    ErrorCode.ACTION_UNSUPPORTED,
                    "assertion_type",
                    "지원하지 않는 검증입니다",
                ),
            )
        return ()

    def validate_target(
        self,
        target: TargetSpec,
        runtime: RuntimeEnvironment,
        mode: TargetValidationMode,
    ) -> tuple[ValidationIssue, ...]:
        if target.adapter_id != self.adapter_id:
            return (
                _issue(
                    ErrorCode.INVALID_SCHEMA,
                    "target.adapter_id",
                    "대상 어댑터가 일치하지 않습니다",
                ),
            )
        if not runtime.interactive_desktop:
            return (
                _issue(
                    ErrorCode.ENVIRONMENT_MISMATCH,
                    "runtime.interactive_desktop",
                    "대화형 데스크톱이 필요합니다",
                ),
            )
        raw_match_count = target.payload.get("match_count", 1)
        if (
            not isinstance(raw_match_count, int)
            or isinstance(raw_match_count, bool)
            or raw_match_count < 0
            or raw_match_count > 2
        ):
            return (
                _issue(
                    ErrorCode.INVALID_SCHEMA,
                    "target.payload.match_count",
                    "대상 일치 수가 올바르지 않습니다",
                ),
            )
        if mode == "deferred":
            return ()
        if raw_match_count == 2:
            return (
                _issue(
                    ErrorCode.TARGET_AMBIGUOUS,
                    "target",
                    "대상이 여러 개 발견되었습니다",
                ),
            )
        if raw_match_count == 0 and mode == "must_exist_now":
            return (
                _issue(
                    ErrorCode.TARGET_NOT_FOUND,
                    "target",
                    "대상을 찾을 수 없습니다",
                ),
            )
        return ()

    def execute(
        self,
        request: ActionRequest,
        context: ExecutionContext,
        cancellation: CancellationToken,
    ) -> AdapterActionResult:
        if cancellation.is_cancelled():
            return AdapterActionResult(
                output=None,
                evidence=_empty_evidence(),
                error_code=ErrorCode.CANCELLED,
                safe_message="실행이 취소되었습니다",
            )
        if request.action_type not in self._descriptor.actions:
            return self._unsupported_result()
        if request.target is not None and request.target.adapter_id != self.adapter_id:
            return AdapterActionResult(
                output=None,
                evidence=_empty_evidence(),
                error_code=ErrorCode.INVALID_SCHEMA,
                safe_message="대상 어댑터가 작업과 일치하지 않습니다",
            )
        if request.parameters.get("invalid") is True:
            return AdapterActionResult(
                output=None,
                evidence=_empty_evidence(),
                error_code=ErrorCode.INVALID_SCHEMA,
                safe_message="작업 매개변수가 올바르지 않습니다",
            )
        match_count = request.parameters.get("match_count", 1)
        if match_count == 0:
            return AdapterActionResult(
                output=None,
                evidence=_empty_evidence(),
                error_code=ErrorCode.TARGET_NOT_FOUND,
                safe_message="대상을 찾을 수 없습니다",
            )
        if match_count == 2:
            return AdapterActionResult(
                output=None,
                evidence=_empty_evidence(),
                error_code=ErrorCode.TARGET_AMBIGUOUS,
                safe_message="대상이 여러 개 발견되었습니다",
            )

        scripted = self._take_scripted(AdapterActionResult(output=None, evidence=_empty_evidence()))
        if isinstance(scripted, RpaError):
            return self._result_from_error(scripted)
        if isinstance(scripted, Exception):
            return AdapterActionResult(
                output=None,
                evidence=_empty_evidence(),
                error_code=ErrorCode.INTERNAL_ERROR,
                safe_message="자동화 작업 중 내부 오류가 발생했습니다",
            )
        if isinstance(scripted, OutputCommit):
            result = AdapterActionResult(
                output=None, evidence=_empty_evidence(), output_commit=scripted
            )
        elif isinstance(scripted, AdapterActionResult):
            result = scripted
        else:
            result = AdapterActionResult(
                output=scripted,  # type: ignore[arg-type]
                evidence=_empty_evidence(),
            )
        self._calls.append(FakeCall("execute", (request, context)))
        return result

    def evaluate_condition(
        self,
        condition: ConditionSpec,
        context: ExecutionContext,
        cancellation: CancellationToken,
    ) -> ConditionObservation:
        cancellation.raise_if_cancelled()
        if condition.condition_type not in self._descriptor.conditions:
            raise RpaError(ErrorCode.ACTION_UNSUPPORTED, "지원하지 않는 조건입니다")
        scripted = self._take_scripted(
            ConditionObservation(satisfied=False, observed=None, evidence=_empty_evidence())
        )
        if isinstance(scripted, RpaError):
            raise scripted
        if isinstance(scripted, Exception):
            raise RpaError(
                ErrorCode.INTERNAL_ERROR, "조건 평가 중 내부 오류가 발생했습니다"
            ) from None
        if not isinstance(scripted, ConditionObservation):
            raise RpaError(ErrorCode.INTERNAL_ERROR, "조건 스크립트가 올바르지 않습니다")
        self._calls.append(FakeCall("evaluate_condition", (condition, context)))
        return scripted

    def evaluate_assertion(
        self,
        assertion: AssertionSpec | TableAssertionSpec,
        subject: FrozenJsonValue | TableData | OutputCommit | None,
        target: TargetSpec | None,
        context: ExecutionContext,
        cancellation: CancellationToken,
    ) -> AssertionObservation:
        cancellation.raise_if_cancelled()
        if assertion.assertion_type not in self._descriptor.assertions:
            raise RpaError(ErrorCode.ACTION_UNSUPPORTED, "지원하지 않는 검증입니다")
        scripted = self._take_scripted(
            AssertionObservation(passed=True, evidence=_empty_evidence())
        )
        if isinstance(scripted, RpaError):
            raise scripted
        if isinstance(scripted, Exception):
            raise RpaError(ErrorCode.INTERNAL_ERROR, "검증 중 내부 오류가 발생했습니다") from None
        if not isinstance(scripted, AssertionObservation):
            raise RpaError(ErrorCode.INTERNAL_ERROR, "검증 스크립트가 올바르지 않습니다")
        self._calls.append(FakeCall("evaluate_assertion", (assertion, subject, target, context)))
        return scripted


__all__ = ["FakeAutomationAdapter", "FakeCall"]
