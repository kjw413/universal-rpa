from __future__ import annotations

from pathlib import Path

from tests.helpers.validation_fakes import ValidationSpyAdapter
from tests.unit.application.test_execution import _request
from tests.unit.application.test_step_test import _service
from tests.unit.application.test_validation import action_step
from universal_rpa.application.execution import RunActionObserved, RunStarted
from universal_rpa.application.run_control import RunControl
from universal_rpa.domain.errors import ErrorCode
from universal_rpa.domain.results import RunReport


class _Observer:
    def __init__(self, *, fail_start: bool = False) -> None:
        self.fail_start = fail_start
        self.trace: list[str] = []
        self.action: RunActionObserved | None = None

    def on_run_started(self, event: RunStarted) -> None:
        self.trace.append("start")
        if self.fail_start:
            raise RuntimeError("observer failed")
        assert event.runtime.process_executable == "fake.exe"

    def on_action_result(self, event: RunActionObserved) -> None:
        self.trace.append("action")
        self.action = event

    def on_run_finished(self, report: RunReport) -> None:
        self.trace.append(f"finish:{report.status}")


def test_observer_receives_start_action_finish_in_order(tmp_path: Path) -> None:
    adapter = ValidationSpyAdapter()
    observer = _Observer()

    report = _service(tmp_path, adapter).run(
        _request(tmp_path, action_step()), RunControl(), (observer,)
    )

    assert report.status == "success"
    assert observer.trace == ["start", "action", "finish:success"]
    assert observer.action is not None
    assert observer.action.target == action_step().target
    assert observer.action.runtime.top_level_hwnd == 200


def test_start_observer_failure_stops_before_adapter_and_still_finishes(tmp_path: Path) -> None:
    adapter = ValidationSpyAdapter()
    observer = _Observer(fail_start=True)

    report = _service(tmp_path, adapter).run(
        _request(tmp_path, action_step()), RunControl(), (observer,)
    )

    assert report.status == "failed"
    assert report.error_code is ErrorCode.INTERNAL_ERROR
    assert observer.trace == ["start", "finish:failed"]
    assert not any(call.operation == "execute" for call in adapter.calls)
