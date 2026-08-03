from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID

from universal_rpa.domain.recording import (
    EventFocusSnapshot,
    NativeInputEvent,
    RawEventType,
    RawInputEvent,
    RecordingEnvironmentSnapshot,
    RecordingSession,
    RecordingSessionSummary,
    RecordingTarget,
    SensitiveKeyToken,
    TargetSnapshot,
    WindowContextSnapshot,
)
from universal_rpa.domain.targets import UiaSelector
from universal_rpa.ports.automation import (
    CancellationToken,
    TargetCaptureRequest,
    TargetCaptureResult,
)
from universal_rpa.ports.capture import ControlSink, InputEventSink
from universal_rpa.ports.context import CapturedEventContext

NOW = datetime(2026, 7, 29, 9, 0, tzinfo=UTC)
SESSION_ID = UUID("00000000-0000-0000-0000-000000000301")


def recording_target() -> RecordingTarget:
    return RecordingTarget(
        process_id=200,
        process_executable="mis.exe",
        top_level_hwnd=100,
        window_title="MIS",
        window_class="MisWindow",
    )


def native_key_event(
    *,
    key: str = "a",
    text: str | None = "a",
    key_token: SensitiveKeyToken | None = None,
) -> NativeInputEvent:
    return NativeInputEvent(
        monotonic_ns=10,
        wall_time_utc=NOW,
        hook_time_ms=300,
        event_type=RawEventType.KEY_DOWN,
        focus=EventFocusSnapshot(
            foreground_hwnd=100,
            focused_hwnd=101,
            foreground_process_id=200,
            cached_uia_runtime_id=(1, 2, 3),
            focus_event_time_ms=250,
            cache_generation=1,
            cache_confirmed=True,
        ),
        payload={"scan_code": 30},
        key_token=key_token or SensitiveKeyToken.create(key=key, text=text),
    )


def captured_event_context(
    *,
    confident: bool = True,
    in_scope: bool = True,
    password: bool = False,
) -> CapturedEventContext:
    return CapturedEventContext(
        window_context=WindowContextSnapshot(
            foreground_hwnd=100,
            focused_hwnd=101,
            process_id=200,
            process_executable="mis.exe",
            top_level_hwnd=100,
            window_title="MIS",
            window_class="MisWindow",
            focused_runtime_id=(1, 2, 3) if confident else None,
            selected_top_level_hwnd=100,
            owned_by_selected_window=in_scope,
            context_confident=confident,
        ),
        target_snapshot=(
            TargetSnapshot(
                selector_candidates=(UiaSelector(automation_id="field"),),
                focused_runtime_id=(1, 2, 3),
                editable=True,
                is_password=password,
                observed_value=None if password else "value",
                bounds=None,
            )
            if confident
            else None
        ),
        environment_snapshot=RecordingEnvironmentSnapshot(
            client_left=0,
            client_top=0,
            client_width=800,
            client_height=600,
            dpi_x=96,
            dpi_y=96,
            monitor_scale=1.0,
            monitor_id="DISPLAY1",
            double_click_time_ms=500,
            drag_width_px=4,
            drag_height_px=4,
        ),
        in_scope=in_scope,
    )


class FakeInputCapture:
    def __init__(self) -> None:
        self.event_sink: InputEventSink | None = None
        self.control_sink: ControlSink | None = None
        self.started = False
        self.stopped = False

    def start(self, event_sink: InputEventSink, control_sink: ControlSink) -> None:
        self.event_sink = event_sink
        self.control_sink = control_sink
        self.started = True

    def stop(self) -> None:
        self.stopped = True


class StaticWindowContext:
    def __init__(self, captured: CapturedEventContext | None = None) -> None:
        self.captured = captured or captured_event_context()

    def list_recordable_windows(self) -> tuple[RecordingTarget, ...]:
        return (recording_target(),)

    def capture_context(
        self,
        event: NativeInputEvent,
        selected: RecordingTarget,
    ) -> CapturedEventContext:
        del event, selected
        return self.captured

    def capture_target(
        self,
        request: TargetCaptureRequest,
        cancellation: CancellationToken,
    ) -> TargetCaptureResult:
        del request, cancellation
        raise NotImplementedError


class BlockingWindowContext(StaticWindowContext):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def capture_context(
        self,
        event: NativeInputEvent,
        selected: RecordingTarget,
    ) -> CapturedEventContext:
        del event, selected
        self.entered.set()
        self.release.wait(5.0)
        return self.captured


class InMemoryRecordingStore:
    def __init__(self, *, fail_append: bool = False) -> None:
        self.session: RecordingSession | None = None
        self.events: list[RawInputEvent] = []
        self.summary: RecordingSessionSummary | None = None
        self.fail_append = fail_append
        self._lock = threading.Lock()

    def create_session(self, session: RecordingSession) -> None:
        self.session = session

    def append(self, event: RawInputEvent) -> None:
        if self.fail_append:
            raise OSError("simulated append failure")
        with self._lock:
            self.events.append(event)

    def finalize(
        self,
        session_id: UUID,
        *,
        retained: bool,
        incomplete: bool,
        dropped_event_count: int = 0,
    ) -> RecordingSessionSummary:
        if self.summary is not None:
            return self.summary
        if self.session is None or self.session.session_id != session_id:
            raise RuntimeError("unknown session")
        self.summary = RecordingSessionSummary(
            session_id=session_id,
            finalized=True,
            incomplete=incomplete,
            retained=retained,
            event_count=len(self.events),
            dropped_event_count=dropped_event_count,
            started_at=self.session.started_at,
            finished_at=max(NOW, self.session.started_at),
        )
        return self.summary

    def load_summary(self, session_id: UUID) -> RecordingSessionSummary:
        if self.summary is None or self.summary.session_id != session_id:
            raise RuntimeError("summary unavailable")
        return self.summary

    def iter_events(self, session_id: UUID) -> Iterator[RawInputEvent]:
        if self.session is None or self.session.session_id != session_id:
            raise RuntimeError("unknown session")
        yield from tuple(self.events)

    def delete_session(self, session_id: UUID, *, reason: str) -> None:
        del reason
        if self.session is not None and self.session.session_id == session_id:
            self.session = None
            self.events.clear()
            self.summary = None

    def serialized_bytes(self) -> bytes:
        return "\n".join(event.model_dump_json() for event in self.events).encode("utf-8")


__all__ = [
    "NOW",
    "SESSION_ID",
    "BlockingWindowContext",
    "FakeInputCapture",
    "InMemoryRecordingStore",
    "StaticWindowContext",
    "captured_event_context",
    "native_key_event",
    "recording_target",
]
