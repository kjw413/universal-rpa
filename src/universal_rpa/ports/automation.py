from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from math import isfinite
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID

from universal_rpa.domain.conditions import (
    AssertionSpec,
    ConditionSpec,
    TableAssertionSpec,
)
from universal_rpa.domain.errors import ErrorCode, RpaError, ValidationIssue
from universal_rpa.domain.results import LoopCursor, OutputCommit, TableData
from universal_rpa.domain.targets import DateContext, RuntimeEnvironment, TargetSpec
from universal_rpa.domain.types import (
    DataCell,
    FrozenJsonObject,
    FrozenJsonValue,
    FrozenMapping,
    deep_freeze_json,
)
from universal_rpa.domain.workflow import ActionStep
from universal_rpa.infrastructure.redaction import sanitize_evidence
from universal_rpa.ports.credentials import SecretValue
from universal_rpa.ports.data_sources import DataPreview

type PreparedValue = str | int | Decimal | date | Path
type ResolvedValue = FrozenJsonValue | PreparedValue | TableData | OutputCommit | SecretValue
type VerificationMode = Literal["postcondition_or_assertion", "intrinsic", "none"]
type AssertionInputKind = Literal["json", "table", "output_commit"]
type TargetValidationMode = Literal["must_exist_now", "may_be_absent_now", "deferred"]


def _freeze_json_value(value: object, *, label: str) -> FrozenJsonValue:
    try:
        return deep_freeze_json(value)  # type: ignore[arg-type]
    except TypeError as error:
        raise ValueError(f"{label} must contain finite JSON values") from error


def _freeze_json_object(value: object, *, label: str) -> FrozenJsonObject:
    frozen = _freeze_json_value(value, label=label)
    if not isinstance(frozen, FrozenMapping):
        raise ValueError(f"{label} must be a JSON object")
    return frozen


def _freeze_evidence(value: object) -> FrozenJsonObject:
    if not isinstance(value, Mapping):
        raise ValueError("evidence must be a JSON object")
    try:
        return sanitize_evidence(dict(value))
    except (TypeError, ValueError) as error:
        raise ValueError("evidence must contain finite JSON values") from error


def _freeze_prepared_mapping(
    value: Mapping[str, PreparedValue],
) -> FrozenMapping[str, PreparedValue]:
    copied: list[tuple[str, PreparedValue]] = []
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError("prepared value keys must be strings")
        if isinstance(item, bool) or not isinstance(item, (str, int, Decimal, date, Path)):
            raise ValueError("unsupported prepared value")
        if isinstance(item, Decimal) and not item.is_finite():
            raise ValueError("prepared decimal must be finite")
        copied.append((key, item))
    return FrozenMapping(tuple(copied))


def _freeze_string_mapping(
    value: Mapping[str, str],
) -> FrozenMapping[str, str]:
    copied: list[tuple[str, str]] = []
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise ValueError("credential references must map strings to strings")
        copied.append((key, item))
    return FrozenMapping(tuple(copied))


def _freeze_row(
    value: Mapping[str, DataCell],
) -> FrozenMapping[str, DataCell]:
    copied: list[tuple[str, DataCell]] = []
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError("row keys must be strings")
        if item is not None and not isinstance(item, (bool, int, float, str)):
            raise ValueError("row values must be scalar")
        if isinstance(item, float) and not isfinite(item):
            raise ValueError("row numbers must be finite")
        copied.append((key, item))
    return FrozenMapping(tuple(copied))


def _freeze_action_outputs(
    value: Mapping[UUID, FrozenJsonValue | TableData],
) -> FrozenMapping[UUID, FrozenJsonValue | TableData]:
    copied: list[tuple[UUID, FrozenJsonValue | TableData]] = []
    for key, item in value.items():
        if not isinstance(key, UUID):
            raise ValueError("action output keys must be UUIDs")
        frozen = (
            item if isinstance(item, TableData) else _freeze_json_value(item, label="action output")
        )
        copied.append((key, frozen))
    return FrozenMapping(tuple(copied))


class CancellationToken:
    def __init__(self) -> None:
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled():
            raise RpaError(ErrorCode.CANCELLED, "실행이 취소되었습니다")


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    run_id: UUID
    step_id: UUID
    iteration_path: tuple[int, ...]
    variables: FrozenMapping[str, PreparedValue]
    credential_refs: FrozenMapping[str, str]
    date_context: DateContext
    output_root: Path
    row_stack: tuple[FrozenMapping[str, DataCell], ...]
    action_outputs: FrozenMapping[UUID, FrozenJsonValue | TableData]
    iteration_cursor: tuple[LoopCursor, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "iteration_path", tuple(self.iteration_path))
        object.__setattr__(self, "iteration_cursor", tuple(self.iteration_cursor))
        object.__setattr__(self, "variables", _freeze_prepared_mapping(self.variables))
        object.__setattr__(self, "credential_refs", _freeze_string_mapping(self.credential_refs))
        object.__setattr__(self, "row_stack", tuple(_freeze_row(row) for row in self.row_stack))
        object.__setattr__(self, "action_outputs", _freeze_action_outputs(self.action_outputs))


@dataclass(frozen=True, slots=True)
class ActionRequest:
    action_type: str
    target: TargetSpec | None
    parameters: FrozenJsonObject
    value: ResolvedValue | None
    has_postcondition_or_assertion: bool

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "parameters",
            _freeze_json_object(self.parameters, label="action parameters"),
        )
        value = self.value
        if value is not None and not isinstance(
            value,
            (str, int, Decimal, date, Path, TableData, OutputCommit, SecretValue),
        ):
            object.__setattr__(self, "value", _freeze_json_value(value, label="action value"))


@dataclass(frozen=True, slots=True)
class ConditionObservation:
    satisfied: bool
    observed: ResolvedValue | None
    evidence: FrozenJsonObject

    def __post_init__(self) -> None:
        observed = self.observed
        if observed is not None and not isinstance(
            observed,
            (str, int, Decimal, date, Path, TableData, OutputCommit, SecretValue),
        ):
            object.__setattr__(
                self,
                "observed",
                _freeze_json_value(observed, label="condition observation"),
            )
        object.__setattr__(self, "evidence", _freeze_evidence(self.evidence))


@dataclass(frozen=True, slots=True)
class AssertionObservation:
    passed: bool
    evidence: FrozenJsonObject

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence", _freeze_evidence(self.evidence))


@dataclass(frozen=True, slots=True)
class TargetCaptureRequest:
    runtime: RuntimeEnvironment
    screen_x: int
    screen_y: int
    focused_runtime_id: tuple[int, ...] | None

    def __post_init__(self) -> None:
        if self.focused_runtime_id is not None:
            object.__setattr__(self, "focused_runtime_id", tuple(self.focused_runtime_id))


@dataclass(frozen=True, slots=True)
class TargetCaptureResult:
    target: TargetSpec | None
    candidates: tuple[TargetSpec, ...]
    preview_png: bytes | None
    issues: tuple[ValidationIssue, ...] = ()

    def __post_init__(self) -> None:
        candidates = tuple(self.candidates)
        issues = tuple(self.issues)
        if self.target is not None and self.target not in candidates:
            raise ValueError("selected target must be one of candidates")
        object.__setattr__(self, "candidates", candidates)
        object.__setattr__(self, "issues", issues)
        if self.preview_png is not None:
            object.__setattr__(self, "preview_png", bytes(self.preview_png))


def _unique_string_items[V](
    value: Mapping[str, V],
    *,
    field_name: str,
) -> tuple[tuple[str, V], ...]:
    items: list[tuple[str, V]] = []
    seen: set[str] = set()
    for key in value:
        if not isinstance(key, str):
            raise ValueError(f"{field_name} keys must be hashable strings")
        if key in seen:
            raise ValueError(f"{field_name} contains duplicate key")
        seen.add(key)
        items.append((key, value[key]))
    return tuple(items)


def _sorted_mapping(
    value: Mapping[str, VerificationMode],
) -> FrozenMapping[str, VerificationMode]:
    items = _unique_string_items(
        value,
        field_name="verification_by_action",
    )
    return FrozenMapping(tuple(sorted(items)))


def _sorted_error_mapping(
    value: Mapping[str, frozenset[ErrorCode]],
) -> FrozenMapping[str, frozenset[ErrorCode]]:
    items = _unique_string_items(
        value,
        field_name="retryable_errors_by_action",
    )
    return FrozenMapping(tuple((key, frozenset(errors)) for key, errors in sorted(items)))


def _sorted_assertion_mapping(
    value: Mapping[str, frozenset[str]],
) -> FrozenMapping[str, frozenset[str]]:
    items = _unique_string_items(
        value,
        field_name="assertions_by_action",
    )
    return FrozenMapping(tuple((key, frozenset(assertions)) for key, assertions in sorted(items)))


def _sorted_input_mapping(
    value: Mapping[str, AssertionInputKind],
) -> FrozenMapping[str, AssertionInputKind]:
    items = _unique_string_items(
        value,
        field_name="assertion_input_kind",
    )
    return FrozenMapping(tuple(sorted(items)))


@dataclass(frozen=True, slots=True)
class AdapterDescriptor:
    adapter_id: str
    implementation_version: str
    supports_target_capture: bool
    actions: frozenset[str]
    conditions: frozenset[str]
    assertions: frozenset[str]
    verification_by_action: FrozenMapping[str, VerificationMode]
    idempotent_actions: frozenset[str]
    retryable_errors_by_action: FrozenMapping[str, frozenset[ErrorCode]]
    assertions_by_action: FrozenMapping[str, frozenset[str]]
    assertion_input_kind: FrozenMapping[str, AssertionInputKind]

    def __post_init__(self) -> None:
        object.__setattr__(self, "actions", frozenset(self.actions))
        object.__setattr__(self, "conditions", frozenset(self.conditions))
        object.__setattr__(self, "assertions", frozenset(self.assertions))
        object.__setattr__(
            self,
            "verification_by_action",
            _sorted_mapping(self.verification_by_action),
        )
        object.__setattr__(self, "idempotent_actions", frozenset(self.idempotent_actions))
        object.__setattr__(
            self,
            "retryable_errors_by_action",
            _sorted_error_mapping(self.retryable_errors_by_action),
        )
        object.__setattr__(
            self,
            "assertions_by_action",
            _sorted_assertion_mapping(self.assertions_by_action),
        )
        object.__setattr__(
            self,
            "assertion_input_kind",
            _sorted_input_mapping(self.assertion_input_kind),
        )


@dataclass(frozen=True, slots=True)
class AdapterActionResult:
    output: FrozenJsonValue | TableData | None
    evidence: FrozenJsonObject
    error_code: ErrorCode | None = None
    safe_message: str = ""
    output_commit: OutputCommit | None = None
    runtime: RuntimeEnvironment | None = None

    def __post_init__(self) -> None:
        if self.output is not None and not isinstance(self.output, TableData):
            object.__setattr__(
                self, "output", _freeze_json_value(self.output, label="adapter output")
            )
        object.__setattr__(self, "evidence", _freeze_evidence(self.evidence))
        if self.error_code is None and self.safe_message:
            raise ValueError("successful adapter result cannot have an error message")
        if self.error_code is not None and not self.safe_message.strip():
            raise ValueError("failed adapter result requires a safe message")


class TargetCapturePort(Protocol):
    def capture_target(
        self,
        request: TargetCaptureRequest,
        cancellation: CancellationToken,
    ) -> TargetCaptureResult: ...


class AutomationAdapter(TargetCapturePort, Protocol):
    @property
    def adapter_id(self) -> str: ...

    def descriptor(self) -> AdapterDescriptor: ...

    def capture_target(
        self,
        request: TargetCaptureRequest,
        cancellation: CancellationToken,
    ) -> TargetCaptureResult: ...

    def validate_action_spec(
        self,
        step: ActionStep,
    ) -> tuple[ValidationIssue, ...]: ...

    def validate_condition_spec(
        self,
        condition: ConditionSpec,
    ) -> tuple[ValidationIssue, ...]: ...

    def validate_assertion_spec(
        self,
        assertion: AssertionSpec | TableAssertionSpec,
    ) -> tuple[ValidationIssue, ...]: ...

    def validate_target(
        self,
        target: TargetSpec,
        runtime: RuntimeEnvironment,
        mode: TargetValidationMode,
    ) -> tuple[ValidationIssue, ...]: ...

    def execute(
        self,
        request: ActionRequest,
        context: ExecutionContext,
        cancellation: CancellationToken,
    ) -> AdapterActionResult: ...

    def evaluate_condition(
        self,
        condition: ConditionSpec,
        context: ExecutionContext,
        cancellation: CancellationToken,
    ) -> ConditionObservation: ...

    def evaluate_assertion(
        self,
        assertion: AssertionSpec | TableAssertionSpec,
        subject: FrozenJsonValue | TableData | OutputCommit | None,
        target: TargetSpec | None,
        context: ExecutionContext,
        cancellation: CancellationToken,
    ) -> AssertionObservation: ...


__all__ = [
    "ActionRequest",
    "AdapterActionResult",
    "AdapterDescriptor",
    "AssertionInputKind",
    "AssertionObservation",
    "AutomationAdapter",
    "CancellationToken",
    "ConditionObservation",
    "DataPreview",
    "ExecutionContext",
    "PreparedValue",
    "ResolvedValue",
    "SecretValue",
    "TargetCapturePort",
    "TargetCaptureRequest",
    "TargetCaptureResult",
    "TargetValidationMode",
    "VerificationMode",
]
