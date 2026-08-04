from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar
from uuid import UUID

import pytest
from pytestqt.qtbot import QtBot

from tests.helpers.validation_fakes import runtime_environment
from universal_rpa.application.execution import RunActionObserved, RunStarted
from universal_rpa.application.reports import SafeRunReportDocument
from universal_rpa.application.run_control import RunControl
from universal_rpa.domain.errors import ErrorCode
from universal_rpa.domain.execution import RunInputs, RunRequest
from universal_rpa.domain.results import ActionResult, RunReport
from universal_rpa.domain.types import FrozenMapping
from universal_rpa.ui.workers import (
    ControlHotkeyListener,
    ExecutionWorker,
    RunProgress,
    WorkerFailure,
)

WORKFLOW_ID = UUID("00000000-0000-0000-0000-000000000931")
RUN_ID = UUID("00000000-0000-0000-0000-000000000932")
STEP_ID = UUID("00000000-0000-0000-0000-000000000933")
NOW = datetime(2026, 8, 3, tzinfo=UTC)


class _FakeKey:
    def __init__(self, name: str) -> None:
        self.name = name
        self.char: str | None = None

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"Key.{self.name}"


class Key:
    f11 = _FakeKey("f11")
    f12 = _FakeKey("f12")
    ctrl_l = _FakeKey("ctrl_l")
    shift = _FakeKey("shift")
    alt_l = _FakeKey("alt_l")


class _RecordingListener:
    instances: ClassVar[list[_RecordingListener]] = []

    def __init__(self, **callbacks: Any) -> None:
        self.on_press = callbacks["on_press"]
        self.on_release = callbacks["on_release"]
        self.started = False
        self.stopped = False
        _RecordingListener.instances.append(self)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


def feed_control_keys(keys: tuple[_FakeKey, ...]) -> str | None:
    _RecordingListener.instances.clear()
    observed: list[str] = []
    listener = ControlHotkeyListener(listener_factory=_RecordingListener)
    listener.command.connect(observed.append)
    listener.start()
    native = _RecordingListener.instances[-1]
    for key in keys:
        native.on_press(key)
    for key in reversed(keys):
        native.on_release(key)
    listener.stop()
    return observed[0] if observed else None


@pytest.mark.parametrize(
    ("keys", "expected"),
    [
        ((Key.f11,), None),
        ((Key.f12,), None),
        ((Key.ctrl_l, Key.f11), None),
        ((Key.shift, Key.f12), None),
        ((Key.alt_l, Key.shift, Key.f12), None),
        ((Key.ctrl_l, Key.shift, Key.f11), "toggle_pause"),
        ((Key.ctrl_l, Key.shift, Key.f12), "cancel"),
    ],
)
def test_control_listener_requires_ctrl_shift_chord(
    qtbot: QtBot, keys: tuple[_FakeKey, ...], expected: str | None
) -> None:
    del qtbot
    assert feed_control_keys(keys) == expected


def test_control_listener_retains_no_raw_event(qtbot: QtBot) -> None:
    del qtbot
    _RecordingListener.instances.clear()
    listener = ControlHotkeyListener(listener_factory=_RecordingListener)
    listener.start()
    native = _RecordingListener.instances[-1]
    native.on_press(_FakeKey("a"))
    native.on_release(_FakeKey("a"))
    listener.stop()

    assert native.stopped is True
    assert not [
        value
        for value in vars(listener).values()
        if isinstance(value, (list, tuple)) and any(isinstance(item, _FakeKey) for item in value)
    ]


def _run_request(tmp_path: Path) -> RunRequest:
    from tests.unit.application.test_validation import action_step, workflow

    project = tmp_path / "project"
    output = tmp_path / "output"
    project.mkdir(exist_ok=True)
    output.mkdir(exist_ok=True)
    return RunRequest(
        workflow=workflow(action_step().model_copy(update={"step_id": STEP_ID})),
        project_dir=project,
        inputs=RunInputs(output_directory=output),
    )


def _started() -> RunStarted:
    return RunStarted(
        run_id=RUN_ID,
        workflow_id=WORKFLOW_ID,
        workflow_name="테스트 업무",
        workflow_revision=1,
        step_labels=FrozenMapping(((STEP_ID, "조회"),)),
        started_at=NOW,
        runtime=runtime_environment(),
    )


CANCELLED_MESSAGE = "실행이 취소되었습니다."


def _result(status: str = "success") -> ActionResult:
    cancelled = status == "cancelled"
    return ActionResult(
        run_id=RUN_ID,
        step_id=STEP_ID,
        iteration_path=(),
        iteration_cursor=(),
        status=status,  # type: ignore[arg-type]
        started_at=NOW,
        error_code=ErrorCode.CANCELLED if cancelled else None,
        safe_message=CANCELLED_MESSAGE if cancelled else "",
    )


def _report(status: str = "success") -> RunReport:
    cancelled = status == "cancelled"
    return RunReport(
        run_id=RUN_ID,
        workflow_id=WORKFLOW_ID,
        workflow_revision=1,
        status=status,  # type: ignore[arg-type]
        started_at=NOW,
        finished_at=NOW,
        error_code=ErrorCode.CANCELLED if cancelled else None,
        safe_message=CANCELLED_MESSAGE if cancelled else "",
        results=(_result(status),),
        completed_iterations=1,
    )


class _StubExecutionService:
    """Drives the worker deterministically without touching real automation."""

    def __init__(self, *, block: bool = False, raises: bool = False) -> None:
        self.block = block
        self.raises = raises
        self.entered = threading.Event()
        self.controls: list[RunControl] = []

    def run(self, request: RunRequest, control: RunControl, observers: tuple[Any, ...] = ()) -> Any:
        del request
        self.controls.append(control)
        self.entered.set()
        if self.raises:
            raise RuntimeError("internal detail that must never surface")
        started = _started()
        for observer in observers:
            observer.on_run_started(started)
        for observer in observers:
            observer.on_action_result(
                RunActionObserved(result=_result(), target=None, runtime=runtime_environment())
            )
        if self.block:
            # Mirrors the real service, which converts a cancellation into a
            # terminal report instead of letting the exception escape ``run``.
            idle = threading.Event()
            while not control.is_cancelled():
                idle.wait(0.01)
            report = _report("cancelled")
        else:
            report = _report()
        for observer in observers:
            observer.on_run_finished(report)
        return report


def test_worker_emits_progress_then_a_safe_report(qtbot: QtBot, tmp_path: Path) -> None:
    worker = ExecutionWorker(_StubExecutionService())
    progress: list[RunProgress] = []
    worker.progress.connect(progress.append)
    completed: list[SafeRunReportDocument] = []
    worker.completed.connect(completed.append)

    with qtbot.waitSignal(worker.finished, timeout=5_000):
        worker.start(_run_request(tmp_path))

    assert [item.completed_actions for item in progress] == [1]
    assert progress[0].last_step_label == "조회"
    assert len(completed) == 1
    assert isinstance(completed[0], SafeRunReportDocument)
    assert completed[0].run_id == RUN_ID
    assert completed[0].status == "success"


def test_cancel_reaches_a_blocked_run_without_the_worker_event_loop(
    qtbot: QtBot, tmp_path: Path
) -> None:
    service = _StubExecutionService(block=True)
    worker = ExecutionWorker(service)
    completed: list[SafeRunReportDocument] = []
    worker.completed.connect(completed.append)

    thread = threading.Thread(target=lambda: worker.start(_run_request(tmp_path)), daemon=True)
    thread.start()
    assert service.entered.wait(5.0)
    worker.cancel()
    thread.join(5.0)
    qtbot.wait(10)

    assert not thread.is_alive()
    assert service.controls[0].is_cancelled()
    assert completed[0].status == "cancelled"


def test_pause_and_resume_toggle_the_shared_run_control(qtbot: QtBot, tmp_path: Path) -> None:
    service = _StubExecutionService(block=True)
    worker = ExecutionWorker(service)

    thread = threading.Thread(target=lambda: worker.start(_run_request(tmp_path)), daemon=True)
    thread.start()
    assert service.entered.wait(5.0)
    worker.pause()
    assert service.controls[0].is_paused is True
    assert worker.is_paused is True
    worker.resume()
    assert service.controls[0].is_paused is False
    worker.cancel()
    thread.join(5.0)
    qtbot.wait(10)

    assert not thread.is_alive()


def test_unexpected_failure_emits_only_a_safe_message(qtbot: QtBot, tmp_path: Path) -> None:
    worker = ExecutionWorker(_StubExecutionService(raises=True))
    failures: list[WorkerFailure] = []
    worker.failed.connect(failures.append)

    with qtbot.waitSignal(worker.finished, timeout=5_000):
        worker.start(_run_request(tmp_path))

    assert len(failures) == 1
    assert "internal detail" not in failures[0].safe_message
    assert failures[0].error_code is ErrorCode.INTERNAL_ERROR


def test_worker_refuses_a_second_concurrent_run(qtbot: QtBot, tmp_path: Path) -> None:
    service = _StubExecutionService(block=True)
    worker = ExecutionWorker(service)

    thread = threading.Thread(target=lambda: worker.start(_run_request(tmp_path)), daemon=True)
    thread.start()
    assert service.entered.wait(5.0)
    worker.start(_run_request(tmp_path))
    worker.cancel()
    thread.join(5.0)
    qtbot.wait(10)

    assert len(service.controls) == 1
