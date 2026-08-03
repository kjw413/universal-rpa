from __future__ import annotations

import time

import pytest

from tests.helpers.recording_fakes import (
    NOW,
    SESSION_ID,
    BlockingWindowContext,
    FakeInputCapture,
    InMemoryRecordingStore,
    StaticWindowContext,
    captured_event_context,
    native_key_event,
    recording_target,
)
from universal_rpa.application.recording import RecordingService, RecordingStateError
from universal_rpa.domain.recording import SensitiveKeyToken
from universal_rpa.domain.types import thaw_json
from universal_rpa.ports.capture import ControlCommand


def recording_service(
    *,
    context: StaticWindowContext | None = None,
    store: InMemoryRecordingStore | None = None,
    queue_size: int = 4,
    worker_join_timeout: float = 0.2,
) -> tuple[RecordingService, FakeInputCapture, InMemoryRecordingStore]:
    capture = FakeInputCapture()
    selected_store = store or InMemoryRecordingStore()
    service = RecordingService(
        capture=capture,
        context=context or StaticWindowContext(),
        store=selected_store,
        queue_size=queue_size,
        worker_join_timeout=worker_join_timeout,
        clock=lambda: NOW,
        session_id_factory=lambda: SESSION_ID,
    )
    return service, capture, selected_store


def test_listener_submission_never_waits_for_slow_context() -> None:
    context = BlockingWindowContext()
    service, _, _ = recording_service(context=context)
    service.start(recording_target())

    started = time.perf_counter()
    service.submit_native_event(native_key_event(key="a"))
    elapsed = time.perf_counter() - started

    assert elapsed < 0.02
    assert context.entered.wait(1.0)
    context.release.set()
    service.stop()


def test_queue_overflow_marks_session_incomplete() -> None:
    context = BlockingWindowContext()
    service, _, _ = recording_service(context=context, queue_size=1)
    service.start(recording_target())
    service.submit_native_event(native_key_event(key="a"))
    assert context.entered.wait(1.0)
    service.submit_native_event(native_key_event(key="b"))
    service.submit_native_event(native_key_event(key="c"))

    summary = service.stop(timeout_seconds=0.05)

    assert summary.incomplete is True
    assert summary.dropped_event_count > 0
    context.release.set()


def test_priority_stop_is_not_blocked_or_dropped_when_event_queue_is_full() -> None:
    context = BlockingWindowContext()
    service, _, _ = recording_service(context=context, queue_size=1)
    service.start(recording_target())
    service.submit_native_event(native_key_event(key="a"))
    assert context.entered.wait(1.0)
    service.submit_native_event(native_key_event(key="b"))

    started = time.perf_counter()
    service.submit_control(ControlCommand.STOP)

    assert service.stop_requested is True
    assert time.perf_counter() - started < 0.02
    context.release.set()
    service.await_stopped(timeout_seconds=1.0)


def test_control_hotkeys_never_reach_raw_store() -> None:
    service, _, store = recording_service()
    service.start(recording_target())
    service.submit_control(ControlCommand.TOGGLE_PAUSE)
    service.submit_control(ControlCommand.TOGGLE_PAUSE)
    service.submit_control(ControlCommand.STOP)

    service.await_stopped(timeout_seconds=1.0)

    assert store.events == []
    assert b"f11" not in store.serialized_bytes().lower()
    assert b"f12" not in store.serialized_bytes().lower()


def test_uncertain_event_context_discards_memory_key_token() -> None:
    context = StaticWindowContext(captured_event_context(confident=False))
    service, _, store = recording_service(context=context)
    token = SensitiveKeyToken.create(key="x", text="SENTINEL_RACE")
    service.start(recording_target())
    service.submit_native_event(native_key_event(key_token=token))

    service.stop()

    assert b"SENTINEL_RACE" not in store.serialized_bytes()
    assert thaw_json(store.events[0].payload) == {"redacted": True}
    with pytest.raises(RuntimeError):
        token.reveal_once()


def test_pause_state_is_captured_at_submission_and_redacts_keyboard() -> None:
    service, _, store = recording_service()
    service.start(recording_target())
    service.pause()
    service.submit_native_event(native_key_event(key="x", text="PAUSED_SECRET"))
    service.resume()

    service.stop()

    assert store.events[0].capture_state == "paused"
    assert thaw_json(store.events[0].payload) == {"redacted": True}
    assert b"PAUSED_SECRET" not in store.serialized_bytes()


def test_worker_append_failure_marks_session_incomplete() -> None:
    store = InMemoryRecordingStore(fail_append=True)
    service, _, _ = recording_service(store=store)
    service.start(recording_target())
    service.submit_native_event(native_key_event())

    summary = service.stop()

    assert summary.incomplete is True
    assert summary.dropped_event_count == 1


def test_invalid_state_transition_is_rejected() -> None:
    service, _, _ = recording_service()
    with pytest.raises(RecordingStateError):
        service.resume()
