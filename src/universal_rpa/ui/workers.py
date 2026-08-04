from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from universal_rpa.adapters.windows.capture import (
    MODIFIER_ALIASES,
    ListenerFactory,
    ListenerPort,
    normalize_key,
)
from universal_rpa.application.execution import (
    ExecutionService,
    RunActionObserved,
    RunObserver,
    RunStarted,
)
from universal_rpa.application.normalization import NormalizationService
from universal_rpa.application.recording import RecordingService, RecordingState
from universal_rpa.application.reports import ReportProjector, SafeRunReportDocument
from universal_rpa.application.run_control import RunControl
from universal_rpa.domain.errors import ErrorCode, RpaError
from universal_rpa.domain.execution import RunRequest
from universal_rpa.domain.recording import RecordingSessionSummary, RecordingTarget
from universal_rpa.domain.results import RunReport
from universal_rpa.ports.capture import ControlHotkeys
from universal_rpa.ports.repositories import RecordingStorePort

#: Control commands published by :class:`ControlHotkeyListener`.
TOGGLE_PAUSE = "toggle_pause"
CANCEL = "cancel"


@dataclass(frozen=True, slots=True)
class WorkerFailure:
    safe_message: str
    error_code: ErrorCode | None = None


class FunctionWorker(QObject):
    completed = Signal(object)
    failed = Signal(object)
    cancelled = Signal()
    finished = Signal()

    def __init__(self, operation: Callable[[threading.Event], object]) -> None:
        super().__init__()
        self._operation = operation
        self._cancelled = threading.Event()

    @Slot()
    def run(self) -> None:
        try:
            if self._cancelled.is_set():
                self.cancelled.emit()
                return
            result = self._operation(self._cancelled)
            if self._cancelled.is_set():
                self.cancelled.emit()
            else:
                self.completed.emit(result)
        except Exception:
            self.failed.emit(WorkerFailure("작업을 완료하지 못했습니다."))
        finally:
            self.finished.emit()

    @Slot()
    def cancel(self) -> None:
        self._cancelled.set()


class RecordingWorker(QObject):
    state_changed = Signal(str)
    failed = Signal(object)
    completed = Signal(object)

    def __init__(
        self,
        service: RecordingService,
        normalization: NormalizationService,
        store: RecordingStorePort,
    ) -> None:
        super().__init__()
        self._service = service
        self._normalization = normalization
        self._store = store
        self._session_id: UUID | None = None
        self._completed_session_id: UUID | None = None
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(100)
        self._poll_timer.timeout.connect(self._poll_transitions)

    @Slot(object)
    def start(self, target: object) -> None:
        if not isinstance(target, RecordingTarget):
            self.failed.emit(WorkerFailure("기록할 대상 창이 올바르지 않습니다."))
            return
        try:
            session = self._service.start(target)
        except Exception:
            self.failed.emit(WorkerFailure("입력 기록을 시작하지 못했습니다."))
            return
        self._session_id = session.session_id
        self._completed_session_id = None
        self.state_changed.emit(RecordingState.RECORDING.value)
        self._poll_timer.start()

    @Slot()
    def pause(self) -> None:
        try:
            self._service.pause()
        except Exception:
            self.failed.emit(WorkerFailure("입력 기록을 일시정지하지 못했습니다."))
            return
        self.state_changed.emit(RecordingState.PAUSED.value)

    @Slot()
    def resume(self) -> None:
        try:
            self._service.resume()
        except Exception:
            self.failed.emit(WorkerFailure("입력 기록을 계속하지 못했습니다."))
            return
        self.state_changed.emit(RecordingState.RECORDING.value)

    @Slot()
    def stop(self) -> None:
        self.state_changed.emit(RecordingState.STOPPING.value)
        try:
            summary = self._service.stop(keep=False, timeout_seconds=5.0)
        except Exception:
            self._poll_timer.stop()
            self.failed.emit(WorkerFailure("입력 기록을 안전하게 종료하지 못했습니다."))
            return
        self._complete(summary)

    @Slot()
    def _poll_transitions(self) -> None:
        for transition in self._service.drain_transitions():
            self.state_changed.emit(transition.current.value)
        if self._service.state is not RecordingState.STOPPED:
            return
        try:
            summary = self._service.await_stopped(timeout_seconds=0)
        except Exception:
            self._poll_timer.stop()
            self.failed.emit(WorkerFailure("종료된 기록을 확인하지 못했습니다."))
            return
        self._complete(summary)

    def _complete(self, summary: RecordingSessionSummary) -> None:
        if self._completed_session_id == summary.session_id:
            return
        self._poll_timer.stop()
        try:
            result = self._normalization.normalize_session(self._store, summary.session_id)
        except Exception:
            self.failed.emit(WorkerFailure("완전하게 종료된 기록만 편집기로 가져올 수 있습니다."))
            return
        self._completed_session_id = summary.session_id
        self.state_changed.emit(RecordingState.STOPPED.value)
        self.completed.emit(result)


@dataclass(frozen=True, slots=True)
class RunProgress:
    """One safe progress tick: counts and labels only, never values or targets."""

    completed_actions: int
    failed_actions: int
    last_step_label: str
    last_status: str
    paused: bool


class _KeyboardOnlyListener:
    """A pynput keyboard listener without any mouse or event-recording surface."""

    def __init__(self, **callbacks: Callable[..., None]) -> None:
        from pynput import keyboard  # type: ignore[import-untyped]

        self._keyboard = keyboard.Listener(
            on_press=callbacks["on_press"],
            on_release=callbacks["on_release"],
        )

    def start(self) -> None:
        self._keyboard.start()

    def stop(self) -> None:
        self._keyboard.stop()


class ControlHotkeyListener(QObject):
    """Publishes only ``Ctrl+Shift+F11`` and ``Ctrl+Shift+F12`` run commands.

    The listener is deliberately control-only.  It never forwards, buffers, or
    stores a key event: a non-chord press updates the pressed-modifier set and is
    otherwise dropped, so a run can be paused or cancelled without the Studio
    observing anything the user typed into the automated application.
    """

    command = Signal(str)

    def __init__(
        self,
        *,
        hotkeys: ControlHotkeys | None = None,
        listener_factory: ListenerFactory = _KeyboardOnlyListener,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._hotkeys = hotkeys or ControlHotkeys()
        self._listener_factory = listener_factory
        self._listener: ListenerPort | None = None
        self._modifiers: set[str] = set()

    @property
    def is_running(self) -> bool:
        return self._listener is not None

    def start(self) -> None:
        if self._listener is not None:
            return
        self._modifiers.clear()
        listener = self._listener_factory(on_press=self._on_press, on_release=self._on_release)
        self._listener = listener
        listener.start()

    def stop(self) -> None:
        listener = self._listener
        self._listener = None
        self._modifiers.clear()
        if listener is not None:
            listener.stop()

    def _on_press(self, key: object) -> None:
        key_name, _ = normalize_key(key)
        modifier = MODIFIER_ALIASES.get(key_name)
        if modifier is not None:
            self._modifiers.add(modifier)
            return
        command = self._chord_command(key_name)
        if command is not None:
            self.command.emit(command)

    def _on_release(self, key: object) -> None:
        key_name, _ = normalize_key(key)
        modifier = MODIFIER_ALIASES.get(key_name)
        if modifier is not None:
            self._modifiers.discard(modifier)

    def _chord_command(self, key_name: str) -> str | None:
        pressed = frozenset(self._modifiers)
        stop = self._hotkeys.stop
        toggle = self._hotkeys.toggle_pause
        if key_name == stop.key.casefold() and stop.modifiers <= pressed:
            return CANCEL
        if key_name == toggle.key.casefold() and toggle.modifiers <= pressed:
            return TOGGLE_PAUSE
        return None


class ExecutionWorker(QObject):
    """Runs one :class:`ExecutionService` job and reports only safe artifacts.

    ``start`` is meant to be delivered as a queued signal so the run occupies a
    worker thread.  ``pause``/``resume``/``cancel`` are deliberately *not* queued:
    they mutate the thread-safe :class:`RunControl` directly, so an emergency stop
    still arrives while the worker thread is blocked inside a long action and its
    own event loop cannot run.
    """

    progress = Signal(object)
    completed = Signal(object)
    failed = Signal(object)
    finished = Signal()

    def __init__(
        self,
        service: ExecutionService,
        *,
        projector: ReportProjector | None = None,
        observers: tuple[RunObserver, ...] = (),
        output_root: Path | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._projector = projector or ReportProjector()
        self._extra_observers = tuple(observers)
        self._output_root = output_root
        self._lock = threading.Lock()
        self._control: RunControl | None = None
        self._started: RunStarted | None = None
        self._completed_actions = 0
        self._failed_actions = 0

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._control is not None

    @property
    def is_paused(self) -> bool:
        with self._lock:
            control = self._control
        return control is not None and control.is_paused

    @Slot(object)
    def start(self, request: object) -> None:
        if not isinstance(request, RunRequest):
            self.failed.emit(
                WorkerFailure("실행 요청이 올바르지 않습니다.", ErrorCode.INVALID_SCHEMA)
            )
            self.finished.emit()
            return
        with self._lock:
            if self._control is not None:
                return
            control = RunControl()
            self._control = control
        self._started = None
        self._completed_actions = 0
        self._failed_actions = 0
        try:
            report = self._service.run(request, control, (self, *self._extra_observers))
        except RpaError as error:
            self.failed.emit(WorkerFailure(error.safe_message, error.code))
        except Exception:
            self.failed.emit(
                WorkerFailure("실행 중 내부 오류가 발생했습니다.", ErrorCode.INTERNAL_ERROR)
            )
        else:
            self._publish(report)
        finally:
            with self._lock:
                self._control = None
            self.finished.emit()

    @Slot()
    def pause(self) -> None:
        with self._lock:
            control = self._control
        if control is not None:
            control.pause()

    @Slot()
    def resume(self) -> None:
        with self._lock:
            control = self._control
        if control is not None:
            control.resume()

    @Slot()
    def toggle_pause(self) -> None:
        self.resume() if self.is_paused else self.pause()

    @Slot()
    def cancel(self) -> None:
        with self._lock:
            control = self._control
        if control is not None:
            control.cancel()

    # -- RunObserver -----------------------------------------------------
    def on_run_started(self, event: RunStarted) -> None:
        self._started = event

    def on_action_result(self, event: RunActionObserved) -> None:
        if event.result.status in {"failed", "cancelled"}:
            self._failed_actions += 1
        else:
            self._completed_actions += 1
        label = ""
        started = self._started
        if started is not None:
            label = started.step_labels.get(event.result.step_id, "")
        self.progress.emit(
            RunProgress(
                completed_actions=self._completed_actions,
                failed_actions=self._failed_actions,
                last_step_label=label,
                last_status=event.result.status,
                paused=self.is_paused,
            )
        )

    def on_run_finished(self, report: RunReport) -> None:
        del report

    def _publish(self, report: RunReport) -> None:
        started = self._started
        if started is None:
            self.failed.emit(
                WorkerFailure("실행 결과를 확인할 수 없습니다.", ErrorCode.INTERNAL_ERROR)
            )
            return
        try:
            document = self._projector.project(started, report, self._output_root)
        except Exception:
            self.failed.emit(
                WorkerFailure("실행 보고서를 만들 수 없습니다.", ErrorCode.INTERNAL_ERROR)
            )
            return
        self.completed.emit(document)


__all__ = [
    "CANCEL",
    "TOGGLE_PAUSE",
    "ControlHotkeyListener",
    "ExecutionWorker",
    "FunctionWorker",
    "RecordingWorker",
    "RunProgress",
    "SafeRunReportDocument",
    "WorkerFailure",
]
