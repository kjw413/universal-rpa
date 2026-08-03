from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from universal_rpa.domain.recording import (
    NativeInputEvent,
    RecordingSession,
    RecordingSessionSummary,
    RecordingTarget,
    enrich_and_sanitize_event,
)
from universal_rpa.ports.capture import ControlCommand, InputCapturePort
from universal_rpa.ports.context import WindowContextPort
from universal_rpa.ports.repositories import RecordingStorePort


class RecordingState(StrEnum):
    IDLE = "idle"
    RECORDING = "recording"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"


class RecordingStateError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class _QueuedEvent:
    event: NativeInputEvent
    capture_state: Literal["recording", "paused"]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RecordingService:
    def __init__(
        self,
        *,
        capture: InputCapturePort,
        context: WindowContextPort,
        store: RecordingStorePort,
        queue_size: int = 2048,
        worker_join_timeout: float = 5.0,
        clock: Callable[[], datetime] = _utc_now,
        session_id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if queue_size <= 0:
            raise ValueError("queue_size must be positive")
        if worker_join_timeout <= 0:
            raise ValueError("worker_join_timeout must be positive")
        self._capture = capture
        self._context = context
        self._store = store
        self._queue_size = queue_size
        self._worker_join_timeout = worker_join_timeout
        self._clock = clock
        self._session_id_factory = session_id_factory

        self._state = RecordingState.IDLE
        self._state_lock = threading.Lock()
        self._finalize_lock = threading.Lock()
        self._stop_requested = threading.Event()
        self._worker_stop = threading.Event()
        self._stopped = threading.Event()
        self._event_queue: queue.Queue[_QueuedEvent] = queue.Queue(maxsize=queue_size)
        self._event_capture_state: Literal["recording", "paused"] = "recording"
        self._session: RecordingSession | None = None
        self._target: RecordingTarget | None = None
        self._summary: RecordingSessionSummary | None = None
        self._worker: threading.Thread | None = None
        self._coordinator: threading.Thread | None = None
        self._incomplete = False
        self._dropped_event_count = 0
        self._keep_on_stop = False

    @property
    def state(self) -> RecordingState:
        with self._state_lock:
            return self._state

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested.is_set()

    def start(self, target: RecordingTarget) -> RecordingSession:
        with self._state_lock:
            if self._state not in {RecordingState.IDLE, RecordingState.STOPPED}:
                raise RecordingStateError("recording is already active")
            session = RecordingSession(
                session_id=self._session_id_factory(),
                target=target,
                started_at=self._clock(),
            )
            self._reset_session_state(session, target)
            self._state = RecordingState.RECORDING

        try:
            self._store.create_session(session)
            self._start_threads()
            self._capture.start(self.submit_native_event, self.submit_control)
        except Exception:
            self._mark_incomplete()
            self._request_stop()
            self._worker_stop.set()
            self._finalize_once()
            raise
        return session

    def pause(self) -> None:
        with self._state_lock:
            if self._state != RecordingState.RECORDING:
                raise RecordingStateError("only an active recording can be paused")
            self._state = RecordingState.PAUSED
            self._event_capture_state = "paused"

    def resume(self) -> None:
        with self._state_lock:
            if self._state != RecordingState.PAUSED:
                raise RecordingStateError("only a paused recording can be resumed")
            self._state = RecordingState.RECORDING
            self._event_capture_state = "recording"

    def stop(
        self,
        *,
        keep: bool = False,
        timeout_seconds: float = 5.0,
    ) -> RecordingSessionSummary:
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds must be nonnegative")
        with self._state_lock:
            if self._state == RecordingState.IDLE:
                raise RecordingStateError("recording has not started")
            if self._state == RecordingState.STOPPED and self._summary is not None:
                return self._summary
            self._keep_on_stop = self._keep_on_stop or keep
        self._request_stop()
        if self._stopped.wait(timeout_seconds):
            return self._require_summary()

        self._mark_incomplete()
        self._worker_stop.set()
        self._discard_queued_events()
        return self._finalize_once()

    def await_stopped(self, *, timeout_seconds: float = 5.0) -> RecordingSessionSummary:
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds must be nonnegative")
        if not self._stopped.wait(timeout_seconds):
            raise TimeoutError("recording did not stop before the timeout")
        return self._require_summary()

    def submit_native_event(self, event: NativeInputEvent) -> None:
        state = self._state
        if state not in {RecordingState.RECORDING, RecordingState.PAUSED}:
            self._discard_key_token(event)
            return
        try:
            self._event_queue.put_nowait(_QueuedEvent(event, self._event_capture_state))
        except queue.Full:
            self._discard_key_token(event)
            self._dropped_event_count += 1
            self._incomplete = True

    def submit_control(self, command: ControlCommand) -> None:
        command = ControlCommand(command)
        if command is ControlCommand.STOP:
            self._request_stop()
            return
        with self._state_lock:
            if self._state == RecordingState.RECORDING:
                self._state = RecordingState.PAUSED
                self._event_capture_state = "paused"
            elif self._state == RecordingState.PAUSED:
                self._state = RecordingState.RECORDING
                self._event_capture_state = "recording"

    def _reset_session_state(
        self,
        session: RecordingSession,
        target: RecordingTarget,
    ) -> None:
        self._stop_requested.clear()
        self._worker_stop.clear()
        self._stopped.clear()
        self._event_queue = queue.Queue(maxsize=self._queue_size)
        self._event_capture_state = "recording"
        self._session = session
        self._target = target
        self._summary = None
        self._worker = None
        self._coordinator = None
        self._incomplete = False
        self._dropped_event_count = 0
        self._keep_on_stop = False

    def _start_threads(self) -> None:
        self._worker = threading.Thread(
            target=self._run_worker,
            name="universal-rpa-recording-worker",
            daemon=True,
        )
        self._coordinator = threading.Thread(
            target=self._coordinate_stop,
            name="universal-rpa-recording-coordinator",
            daemon=True,
        )
        self._worker.start()
        self._coordinator.start()

    def _run_worker(self) -> None:
        while not self._worker_stop.is_set() or not self._event_queue.empty():
            try:
                queued = self._event_queue.get(timeout=0.05)
            except queue.Empty:
                continue
            try:
                self._process_event(queued)
            except Exception:
                self._discard_key_token(queued.event)
                self._mark_dropped()
            finally:
                self._event_queue.task_done()

    def _process_event(self, queued: _QueuedEvent) -> None:
        session = self._session
        target = self._target
        if session is None or target is None:
            self._discard_key_token(queued.event)
            self._mark_dropped()
            return
        captured = self._context.capture_context(queued.event, target)
        raw = enrich_and_sanitize_event(
            queued.event,
            session_id=session.session_id,
            context=captured.window_context,
            target=captured.target_snapshot,
            environment=captured.environment_snapshot,
            in_scope=captured.in_scope,
            capture_state=queued.capture_state,
        )
        self._store.append(raw)

    def _coordinate_stop(self) -> None:
        self._stop_requested.wait()
        try:
            self._capture.stop()
        except Exception:
            self._mark_incomplete()
        self._worker_stop.set()
        worker = self._worker
        if worker is not None:
            worker.join(self._worker_join_timeout)
            if worker.is_alive():
                self._mark_incomplete()
                self._discard_queued_events()
        self._finalize_once()

    def _request_stop(self) -> None:
        with self._state_lock:
            if self._state in {RecordingState.RECORDING, RecordingState.PAUSED}:
                self._state = RecordingState.STOPPING
        self._stop_requested.set()

    def _finalize_once(self) -> RecordingSessionSummary:
        with self._finalize_lock:
            if self._summary is not None:
                return self._summary
            session = self._session
            if session is None:
                raise RecordingStateError("recording has not started")
            summary = self._store.finalize(
                session.session_id,
                retained=self._keep_on_stop,
                incomplete=self._incomplete,
                dropped_event_count=self._dropped_event_count,
            )
            self._summary = summary
            with self._state_lock:
                self._state = RecordingState.STOPPED
            self._stopped.set()
            return summary

    def _require_summary(self) -> RecordingSessionSummary:
        summary = self._summary
        if summary is None:
            raise RecordingStateError("recording has no final summary")
        return summary

    def _mark_incomplete(self) -> None:
        self._incomplete = True

    def _mark_dropped(self) -> None:
        self._dropped_event_count += 1
        self._incomplete = True

    def _discard_queued_events(self) -> None:
        while True:
            try:
                queued = self._event_queue.get_nowait()
            except queue.Empty:
                return
            self._discard_key_token(queued.event)
            self._mark_dropped()
            self._event_queue.task_done()

    @staticmethod
    def _discard_key_token(event: NativeInputEvent) -> None:
        if event.key_token is not None:
            event.key_token.discard()


__all__ = ["RecordingService", "RecordingState", "RecordingStateError"]
