"""Deterministic state waits, assertion dispatch and bounded retries."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from universal_rpa.adapters.registry import AdapterRegistry
from universal_rpa.application.run_control import RunControl
from universal_rpa.domain.conditions import AssertionSpec, TableAssertionSpec, WaitSpec
from universal_rpa.domain.errors import ErrorCode, RpaError
from universal_rpa.domain.results import OutputCommit, TableData
from universal_rpa.domain.targets import TargetSpec
from universal_rpa.domain.types import FrozenJsonObject, FrozenJsonValue, FrozenMapping
from universal_rpa.domain.workflow import FailurePolicy
from universal_rpa.ports.automation import (
    AdapterActionResult,
    CancellationToken,
    ConditionObservation,
    ExecutionContext,
)


class Clock(Protocol):
    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


@dataclass(frozen=True, slots=True)
class AssertionOutcome:
    passed: bool
    error_code: ErrorCode | None
    safe_message: str
    evidence: FrozenJsonObject


@dataclass(frozen=True, slots=True)
class AttemptOutcome:
    result: AdapterActionResult
    attempt_count: int


@dataclass(frozen=True, slots=True)
class AdapterActionCapability:
    action_type: str
    idempotent: bool
    retryable_errors: frozenset[ErrorCode]


class ConditionPoller:
    def __init__(self, registry: AdapterRegistry, clock: Clock | None = None) -> None:
        self._registry = registry
        self._clock = clock or SystemClock()

    def wait(
        self,
        wait: WaitSpec,
        context: ExecutionContext,
        control: RunControl,
    ) -> ConditionObservation:
        """Poll the namespaced condition until satisfied or its finite deadline."""

        if wait.condition.condition_type == "windows.fixed_delay":
            self._sleep_cancellable(wait.timeout_ms / 1_000, control)
            return ConditionObservation(
                satisfied=True,
                observed=None,
                evidence=FrozenMapping.empty(),
            )
        deadline = self._clock.monotonic() + (wait.timeout_ms / 1_000)
        adapter_id, _, _ = wait.condition.condition_type.partition(".")
        adapter = self._registry.require(adapter_id)
        while True:
            control.wait_if_paused()
            control.raise_if_cancelled()
            observation = adapter.evaluate_condition(wait.condition, context, control)
            if observation.satisfied:
                return observation
            remaining = deadline - self._clock.monotonic()
            if remaining <= 0:
                raise RpaError(
                    ErrorCode.CONDITION_TIMEOUT,
                    "정해진 시간 안에 다음 단계의 상태 조건이 충족되지 않았습니다.",
                    observation.evidence,
                )
            self._sleep_cancellable(min(remaining, wait.poll_interval_ms / 1_000), control)

    def _sleep_cancellable(self, seconds: float, control: RunControl) -> None:
        remaining = seconds
        while remaining > 0:
            control.wait_if_paused()
            control.raise_if_cancelled()
            part = min(remaining, 0.1)
            before = self._clock.monotonic()
            self._clock.sleep(part)
            elapsed = self._clock.monotonic() - before
            # Injected deterministic clocks may legitimately advance by zero;
            # consume the requested slice in that case to guarantee progress.
            remaining -= elapsed if elapsed > 0 else part


class AssertionEvaluator:
    def __init__(self, registry: AdapterRegistry) -> None:
        self._registry = registry

    def evaluate(
        self,
        action_type: str,
        assertion: AssertionSpec | TableAssertionSpec,
        subject: FrozenJsonValue | TableData | OutputCommit | None,
        target: TargetSpec | None,
        context: ExecutionContext,
        control: CancellationToken,
    ) -> AssertionOutcome:
        action_adapter_id, _, _ = action_type.partition(".")
        assertion_adapter_id, _, _ = assertion.assertion_type.partition(".")
        action_descriptor = self._registry.require(action_adapter_id).descriptor()
        compatible = action_descriptor.assertions_by_action.get(action_type, frozenset())
        if assertion.assertion_type not in compatible:
            raise RpaError(ErrorCode.INVALID_SCHEMA, "작업에 허용되지 않은 검증 조건입니다.")
        owner = self._registry.require(assertion_adapter_id)
        expected_kind = owner.descriptor().assertion_input_kind.get(assertion.assertion_type)
        actual_kind = self._subject_kind(subject)
        if expected_kind != actual_kind:
            raise RpaError(
                ErrorCode.INVALID_SCHEMA, "검증 조건의 입력 형식이 작업 결과와 맞지 않습니다."
            )
        control.raise_if_cancelled()
        observation = owner.evaluate_assertion(assertion, subject, target, context, control)
        if observation.passed:
            return AssertionOutcome(True, None, "", observation.evidence)
        return AssertionOutcome(
            False,
            ErrorCode.ASSERTION_FAILED,
            "작업 결과가 지정한 검증 조건과 일치하지 않습니다.",
            observation.evidence,
        )

    @staticmethod
    def _subject_kind(subject: FrozenJsonValue | TableData | OutputCommit | None) -> str:
        if isinstance(subject, TableData):
            return "table"
        if isinstance(subject, OutputCommit):
            return "output_commit"
        return "json"


class RetryExecutor:
    def __init__(self, clock: Clock | None = None) -> None:
        self._clock = clock or SystemClock()

    def execute(
        self,
        policy: FailurePolicy,
        capability: AdapterActionCapability,
        operation: Callable[[], AdapterActionResult],
        control: RunControl,
    ) -> AttemptOutcome:
        if policy.mode != "retry":
            control.wait_if_paused()
            return AttemptOutcome(operation(), 1)
        if not capability.idempotent:
            raise RpaError(
                ErrorCode.INVALID_SCHEMA, "재시도는 멱등성이 확인된 작업에만 사용할 수 있습니다."
            )

        attempts = 0
        while True:
            control.wait_if_paused()
            control.raise_if_cancelled()
            attempts += 1
            result = operation()
            if (
                result.error_code not in capability.retryable_errors
                or attempts > policy.retry_count
            ):
                return AttemptOutcome(result, attempts)
            self._backoff(policy.backoff_ms / 1_000, control)

    def _backoff(self, seconds: float, control: RunControl) -> None:
        remaining = seconds
        while remaining > 0:
            control.wait_if_paused()
            control.raise_if_cancelled()
            part = min(remaining, 0.1)
            before = self._clock.monotonic()
            self._clock.sleep(part)
            elapsed = self._clock.monotonic() - before
            remaining -= elapsed if elapsed > 0 else part


__all__ = [
    "AdapterActionCapability",
    "AssertionEvaluator",
    "AssertionOutcome",
    "AttemptOutcome",
    "Clock",
    "ConditionPoller",
    "RetryExecutor",
    "SystemClock",
]
