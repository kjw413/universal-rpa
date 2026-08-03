from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from uuid import UUID

import pytest

from tests.helpers.recording_fakes import NOW, recording_target
from tests.unit.application.test_keyboard_normalization import keyboard_event
from tests.unit.application.test_mouse_normalization import raw_mouse_event
from universal_rpa.application.normalization import (
    MouseThresholds,
    NormalizationService,
    RecordingNotNormalizable,
    normalize_mouse_events,
)
from universal_rpa.domain.recording import (
    RawEventType,
    RawInputEvent,
    RecordingSessionSummary,
)

SESSION_ID = UUID("00000000-0000-0000-0000-000000000701")


class MemorySessionStore:
    def __init__(
        self,
        events: tuple[RawInputEvent, ...],
        *,
        finalized: bool = True,
        incomplete: bool = False,
    ) -> None:
        self.events = events
        self.summary = RecordingSessionSummary(
            session_id=SESSION_ID,
            finalized=finalized,
            incomplete=incomplete,
            retained=False,
            event_count=len(events),
            dropped_event_count=0,
            started_at=NOW,
            finished_at=NOW + timedelta(seconds=20) if finalized else None,
        )

    def load_summary(self, session_id: UUID) -> RecordingSessionSummary:
        assert session_id == SESSION_ID
        return self.summary

    def iter_events(self, session_id: UUID) -> Iterator[RawInputEvent]:
        assert session_id == SESSION_ID
        yield from self.events

    def create_session(self, session: object) -> None:
        del session

    def append(self, event: RawInputEvent) -> None:
        del event

    def finalize(self, session_id: UUID, **kwargs: object) -> RecordingSessionSummary:
        del session_id, kwargs
        return self.summary

    def delete_session(self, session_id: UUID, *, reason: str) -> None:
        del session_id, reason


def normalized_event(
    event: RawInputEvent,
    *,
    event_id: int,
    monotonic_ms: int | None = None,
) -> RawInputEvent:
    updates: dict[str, object] = {
        "session_id": SESSION_ID,
        "event_id": UUID(int=event_id),
    }
    if monotonic_ms is not None:
        updates["monotonic_ns"] = monotonic_ms * 1_000_000
    return event.model_copy(update=updates)


def click_pair(
    *,
    first_event_id: int,
    start_ms: int,
    x: int = 100,
) -> tuple[RawInputEvent, RawInputEvent]:
    return (
        normalized_event(
            raw_mouse_event(
                RawEventType.MOUSE_DOWN,
                event_number=first_event_id,
                monotonic_ms=start_ms,
                x=x,
            ),
            event_id=first_event_id,
            monotonic_ms=start_ms,
        ),
        normalized_event(
            raw_mouse_event(
                RawEventType.MOUSE_UP,
                event_number=first_event_id + 1,
                monotonic_ms=start_ms + 20,
                x=x,
            ),
            event_id=first_event_id + 1,
            monotonic_ms=start_ms + 20,
        ),
    )


def test_mixed_candidates_are_sorted_by_first_monotonic_time() -> None:
    enter = normalized_event(
        keyboard_event(
            "enter",
            event_number=1,
            monotonic_ms=100,
            editable=False,
        ),
        event_id=100,
        monotonic_ms=100,
    )
    events = (*click_pair(first_event_id=1, start_ms=300), enter)
    result = NormalizationService().normalize_session(MemorySessionStore(events), SESSION_ID)
    times = [item.first_monotonic_ns for item in result.candidates]
    assert times == sorted(times)
    assert [item.action_type for item in result.candidates] == [
        "windows.press_key",
        "windows.click",
    ]


def test_long_gap_is_only_a_wait_suggestion() -> None:
    events = (
        *click_pair(first_event_id=1, start_ms=0, x=100),
        *click_pair(first_event_id=3, start_ms=12_000, x=500),
    )
    result = NormalizationService().normalize_session(MemorySessionStore(events), SESSION_ID)
    assert "windows.wait" not in [item.action_type for item in result.candidates]
    assert result.suggestions[0].kind == "wait_candidate"


@pytest.mark.parametrize("finalized,incomplete", [(False, False), (True, True)])
def test_unfinalized_or_incomplete_session_is_rejected(
    finalized: bool,
    incomplete: bool,
) -> None:
    events = click_pair(first_event_id=1, start_ms=0)
    store = MemorySessionStore(events, finalized=finalized, incomplete=incomplete)
    with pytest.raises(RecordingNotNormalizable):
        NormalizationService().normalize_session(store, SESSION_ID)


def test_repeated_normalization_is_byte_identical() -> None:
    events = click_pair(first_event_id=1, start_ms=0)
    store = MemorySessionStore(events)
    service = NormalizationService()
    first = service.normalize_session(store, SESSION_ID).model_dump_json()
    second = service.normalize_session(store, SESSION_ID).model_dump_json()
    assert first == second


def test_adjacent_clicks_can_merge_and_split_at_complete_click_boundary() -> None:
    events = (
        *click_pair(first_event_id=1, start_ms=0, x=100),
        *click_pair(first_event_id=3, start_ms=1_000, x=100),
    )
    clicks = normalize_mouse_events(
        events,
        thresholds=MouseThresholds(double_click_time_ms=500),
    ).candidates
    service = NormalizationService()

    merged = service.merge(clicks, (0, 1))
    left, right = service.split(merged, merged.source_event_ids[2])

    assert merged.action_type == "windows.double_click"
    assert (left.action_type, right.action_type) == ("windows.click", "windows.click")
    assert left.source_event_ids == merged.source_event_ids[:2]
    assert right.source_event_ids == merged.source_event_ids[2:]


def test_merge_rejects_nonadjacent_indices() -> None:
    events = (
        *click_pair(first_event_id=1, start_ms=0, x=100),
        *click_pair(first_event_id=3, start_ms=1_000, x=100),
        *click_pair(first_event_id=5, start_ms=2_000, x=100),
    )
    clicks = normalize_mouse_events(
        events,
        thresholds=MouseThresholds(double_click_time_ms=500),
    ).candidates
    with pytest.raises(ValueError, match="adjacent"):
        NormalizationService().merge(clicks, (0, 2))


def test_control_transition_telemetry_contains_states_not_key_labels() -> None:
    from tests.unit.application.test_recording import recording_service
    from universal_rpa.ports.capture import ControlCommand

    service, _, _ = recording_service()
    service.start(recording_target())
    service.submit_control(ControlCommand.TOGGLE_PAUSE)
    service.submit_control(ControlCommand.TOGGLE_PAUSE)
    service.submit_control(ControlCommand.STOP)
    service.await_stopped(timeout_seconds=1.0)

    encoded = repr(service.drain_transitions()).casefold()
    assert "paused" in encoded
    assert "resumed" in encoded
    assert "f11" not in encoded and "f12" not in encoded
