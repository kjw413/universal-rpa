"""Clipboard actions that retain only safe shape/hash evidence."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from importlib import import_module
from typing import Any, Protocol, cast

from universal_rpa.domain.conditions import AssertionSpec, ConditionSpec, TableAssertionSpec
from universal_rpa.domain.errors import ErrorCode, RpaError, ValidationIssue
from universal_rpa.domain.results import OutputCommit, TableData
from universal_rpa.domain.targets import RuntimeEnvironment, TargetSpec
from universal_rpa.domain.types import FrozenJsonValue, FrozenMapping
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
)

from .table_parser import parse_clipboard_table

CLIPBOARD_ADAPTER_VERSION = "1.0.0"


class ClipboardPort(Protocol):
    def sequence_number(self) -> int: ...

    def text(self) -> str: ...

    def formats(self) -> tuple[str, ...]: ...


class Win32Clipboard:
    def sequence_number(self) -> int:
        import ctypes

        return int(ctypes.windll.user32.GetClipboardSequenceNumber())

    def text(self) -> str:
        win32clipboard = cast(Any, import_module("win32clipboard"))

        win32clipboard.OpenClipboard()
        try:
            if not win32clipboard.IsClipboardFormatAvailable(13):
                raise ValueError("unicode text missing")
            return str(win32clipboard.GetClipboardData(13))
        finally:
            win32clipboard.CloseClipboard()

    def formats(self) -> tuple[str, ...]:
        return ("CF_UNICODETEXT",)


@dataclass(frozen=True, slots=True)
class ClipboardSnapshot:
    sequence_number: int
    text_length: int
    sha256: str
    formats: tuple[str, ...]

    @classmethod
    def from_text(
        cls, sequence_number: int, text: str, formats: tuple[str, ...]
    ) -> ClipboardSnapshot:
        return cls(
            sequence_number=sequence_number,
            text_length=len(text),
            sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            formats=tuple(formats),
        )

    def evidence(self) -> FrozenMapping[str, FrozenJsonValue]:
        return FrozenMapping(
            (
                ("sequence_number", self.sequence_number),
                ("text_length", self.text_length),
                ("sha256", self.sha256),
                ("formats", self.formats),
            )
        )


def _issue(code: ErrorCode, path: str, message: str) -> tuple[ValidationIssue, ...]:
    return (ValidationIssue(code=code, path=path, safe_message=message),)


class ClipboardAutomationAdapter:
    def __init__(self, clipboard: ClipboardPort | None = None) -> None:
        self._clipboard = clipboard or Win32Clipboard()

    @property
    def adapter_id(self) -> str:
        return "clipboard"

    def descriptor(self) -> AdapterDescriptor:
        return AdapterDescriptor(
            adapter_id=self.adapter_id,
            implementation_version=CLIPBOARD_ADAPTER_VERSION,
            supports_target_capture=False,
            actions=frozenset({"clipboard.read_clipboard", "clipboard.extract_table"}),
            conditions=frozenset({"clipboard.clipboard_changed"}),
            assertions=frozenset({"clipboard.table"}),
            verification_by_action=FrozenMapping(
                (
                    ("clipboard.extract_table", "postcondition_or_assertion"),
                    ("clipboard.read_clipboard", "postcondition_or_assertion"),
                )
            ),
            idempotent_actions=frozenset({"clipboard.read_clipboard", "clipboard.extract_table"}),
            retryable_errors_by_action=FrozenMapping(
                (
                    ("clipboard.extract_table", frozenset({ErrorCode.ACTION_FAILED})),
                    ("clipboard.read_clipboard", frozenset({ErrorCode.ACTION_FAILED})),
                )
            ),
            assertions_by_action=FrozenMapping(
                (
                    ("clipboard.extract_table", frozenset({"clipboard.table"})),
                    ("clipboard.read_clipboard", frozenset()),
                )
            ),
            assertion_input_kind=FrozenMapping((("clipboard.table", "table"),)),
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
                "클립보드는 화면 대상 캡처를 지원하지 않습니다.",
            ),
        )

    def validate_action_spec(self, step: ActionStep) -> tuple[ValidationIssue, ...]:
        if step.action_type not in self.descriptor().actions:
            return _issue(
                ErrorCode.ACTION_UNSUPPORTED, "action_type", "지원하지 않는 클립보드 작업입니다."
            )
        if step.target is not None or step.value is not None:
            return _issue(
                ErrorCode.INVALID_SCHEMA,
                "target",
                "클립보드 작업은 화면 대상이나 직접 입력값을 사용하지 않습니다.",
            )
        return ()

    def validate_condition_spec(self, condition: ConditionSpec) -> tuple[ValidationIssue, ...]:
        if condition.condition_type != "clipboard.clipboard_changed":
            return _issue(
                ErrorCode.ACTION_UNSUPPORTED, "condition_type", "지원하지 않는 클립보드 조건입니다."
            )
        return ()

    def validate_assertion_spec(
        self, assertion: AssertionSpec | TableAssertionSpec
    ) -> tuple[ValidationIssue, ...]:
        if not isinstance(assertion, TableAssertionSpec):
            return _issue(ErrorCode.INVALID_SCHEMA, "assertion", "클립보드 표 검증만 지원합니다.")
        return ()

    def validate_target(
        self, target: TargetSpec, runtime: RuntimeEnvironment, mode: TargetValidationMode
    ) -> tuple[ValidationIssue, ...]:
        del target, runtime, mode
        return _issue(ErrorCode.INVALID_SCHEMA, "target", "클립보드 작업에는 대상이 없습니다.")

    def _read(self) -> tuple[ClipboardSnapshot, str]:
        try:
            before = self._clipboard.sequence_number()
            text = self._clipboard.text()
            after = self._clipboard.sequence_number()
            if before != after:
                raise ValueError("clipboard changed while reading")
            snapshot = ClipboardSnapshot.from_text(after, text, self._clipboard.formats())
            return snapshot, text
        except Exception:
            raise RpaError(ErrorCode.ACTION_FAILED, "클립보드 내용을 읽을 수 없습니다.") from None

    def execute(
        self, request: ActionRequest, context: ExecutionContext, cancellation: CancellationToken
    ) -> AdapterActionResult:
        del context
        cancellation.raise_if_cancelled()
        try:
            snapshot, text = self._read()
            evidence = snapshot.evidence()
            if request.action_type == "clipboard.read_clipboard":
                return AdapterActionResult(output=None, evidence=evidence)
            if request.action_type == "clipboard.extract_table":
                table = parse_clipboard_table(text)
                return AdapterActionResult(
                    output=table,
                    evidence=FrozenMapping(
                        (
                            *evidence.items(),
                            ("headers", table.headers),
                            ("row_count", len(table.rows)),
                        )
                    ),
                )
            return AdapterActionResult(
                output=None,
                evidence=FrozenMapping.empty(),
                error_code=ErrorCode.ACTION_UNSUPPORTED,
                safe_message="지원하지 않는 클립보드 작업입니다.",
            )
        except RpaError as error:
            return AdapterActionResult(
                output=None,
                evidence=error.evidence,
                error_code=error.code,
                safe_message=error.safe_message,
            )

    def evaluate_condition(
        self, condition: ConditionSpec, context: ExecutionContext, cancellation: CancellationToken
    ) -> ConditionObservation:
        del context
        cancellation.raise_if_cancelled()
        previous = condition.expected
        if isinstance(previous, FrozenMapping):
            previous = previous.get("sequence_number")
        if not isinstance(previous, int) or isinstance(previous, bool):
            raise RpaError(
                ErrorCode.INVALID_SCHEMA, "클립보드 변경 조건에는 이전 sequence 번호가 필요합니다."
            )
        sequence = self._clipboard.sequence_number()
        return ConditionObservation(
            satisfied=sequence != previous,
            observed=sequence,
            evidence=FrozenMapping((("sequence_number", sequence),)),
        )

    def evaluate_assertion(
        self,
        assertion: AssertionSpec | TableAssertionSpec,
        subject: FrozenJsonValue | TableData | OutputCommit | None,
        target: TargetSpec | None,
        context: ExecutionContext,
        cancellation: CancellationToken,
    ) -> AssertionObservation:
        del target, context
        cancellation.raise_if_cancelled()
        if not isinstance(assertion, TableAssertionSpec) or not isinstance(subject, TableData):
            raise RpaError(ErrorCode.INVALID_SCHEMA, "클립보드 표 검증의 입력이 올바르지 않습니다.")
        headers_ok = assertion.required_headers <= set(subject.headers)
        rows_ok = (assertion.min_rows is None or len(subject.rows) >= assertion.min_rows) and (
            assertion.max_rows is None or len(subject.rows) <= assertion.max_rows
        )
        tokens_ok = all(
            any(token in str(cell) for row in subject.rows for cell in row)
            for token in assertion.required_tokens
        )
        nonempty_ok = assertion.allow_empty or bool(subject.rows)
        return AssertionObservation(
            passed=headers_ok and rows_ok and tokens_ok and nonempty_ok,
            evidence=FrozenMapping(
                (("headers", subject.headers), ("row_count", len(subject.rows)))
            ),
        )


__all__ = ["CLIPBOARD_ADAPTER_VERSION", "ClipboardAutomationAdapter", "ClipboardSnapshot"]
