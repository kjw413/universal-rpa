from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from universal_rpa.application.normalization import NormalizationService
from universal_rpa.application.recording import RecordingService, RecordingState
from universal_rpa.domain.recording import RecordingSessionSummary, RecordingTarget
from universal_rpa.ports.repositories import RecordingStorePort


@dataclass(frozen=True, slots=True)
class WorkerFailure:
    safe_message: str


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


__all__ = ["FunctionWorker", "RecordingWorker", "WorkerFailure"]
