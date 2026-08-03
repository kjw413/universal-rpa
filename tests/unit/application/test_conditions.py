from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import UUID

import pytest

from universal_rpa.adapters.registry import AdapterRegistry
from universal_rpa.application.conditions import (
    AdapterActionCapability,
    ConditionPoller,
    RetryExecutor,
)
from universal_rpa.application.run_control import RunControl
from universal_rpa.domain.conditions import ConditionSpec, WaitSpec
from universal_rpa.domain.errors import ErrorCode, RpaError
from universal_rpa.domain.targets import DateContext
from universal_rpa.domain.types import FrozenMapping
from universal_rpa.domain.workflow import FailurePolicy
from universal_rpa.ports.automation import AdapterActionResult, ExecutionContext


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


def _context(tmp_path: Path) -> ExecutionContext:
    return ExecutionContext(
        run_id=UUID("00000000-0000-0000-0000-000000000906"),
        step_id=UUID("00000000-0000-0000-0000-000000000907"),
        iteration_path=(),
        variables=FrozenMapping.empty(),
        credential_refs=FrozenMapping.empty(),
        date_context=DateContext(today=date(2026, 8, 3), run_date=date(2026, 8, 3)),
        output_root=tmp_path,
        row_stack=(),
        action_outputs=FrozenMapping.empty(),
    )


def test_fixed_delay_waits_for_exact_bounded_duration(tmp_path: Path) -> None:
    clock = _Clock()
    poller = ConditionPoller(AdapterRegistry(), clock)

    observation = poller.wait(
        WaitSpec(
            condition=ConditionSpec(condition_type="windows.fixed_delay"),
            timeout_ms=250,
            poll_interval_ms=25,
        ),
        _context(tmp_path),
        RunControl(),
    )

    assert observation.satisfied is True
    assert clock.value == pytest.approx(0.25)


def test_retry_rejects_non_idempotent_action_before_operation() -> None:
    calls = 0

    def operation() -> AdapterActionResult:
        nonlocal calls
        calls += 1
        return AdapterActionResult(output=None, evidence=FrozenMapping.empty())

    with pytest.raises(RpaError) as caught:
        RetryExecutor(_Clock()).execute(
            FailurePolicy(mode="retry", retry_count=1, backoff_ms=0),
            AdapterActionCapability("windows.click", False, frozenset()),
            operation,
            RunControl(),
        )

    assert caught.value.code is ErrorCode.INVALID_SCHEMA
    assert calls == 0
