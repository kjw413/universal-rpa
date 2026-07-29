from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from universal_rpa.domain.recording import (
    EventFocusSnapshot,
    NativeInputEvent,
    RawEventType,
    RawInputEvent,
    RecordingEnvironmentSnapshot,
    SensitiveKeyToken,
    TargetSnapshot,
    WindowContextSnapshot,
    enrich_and_sanitize_event,
)
from universal_rpa.domain.targets import UiaSelector
from universal_rpa.domain.types import FrozenMapping, thaw_json

SESSION_ID = UUID("00000000-0000-0000-0000-000000000101")
EVENT_ID = UUID("00000000-0000-0000-0000-000000000102")
WALL_TIME = datetime(2026, 7, 29, 9, 0, tzinfo=UTC)


def focus_snapshot(
    *,
    runtime_id: tuple[int, ...] | None = (1, 2, 3),
    cache_confirmed: bool = True,
) -> EventFocusSnapshot:
    return EventFocusSnapshot(
        foreground_hwnd=100,
        focused_hwnd=101,
        foreground_process_id=200,
        cached_uia_runtime_id=runtime_id,
        focus_event_time_ms=300,
        cache_generation=1,
        cache_confirmed=cache_confirmed,
    )


def window_context(
    *,
    runtime_id: tuple[int, ...] | None = (1, 2, 3),
    confident: bool = True,
) -> WindowContextSnapshot:
    return WindowContextSnapshot(
        foreground_hwnd=100,
        focused_hwnd=101,
        process_id=200,
        process_executable="mis.exe",
        top_level_hwnd=100,
        window_title="MIS",
        window_class="MisWindow",
        focused_runtime_id=runtime_id,
        selected_top_level_hwnd=100,
        owned_by_selected_window=True,
        context_confident=confident,
    )


def target_snapshot(
    *,
    runtime_id: tuple[int, ...] | None = (1, 2, 3),
    is_password: bool = False,
    observed_value: str | None = "value",
) -> TargetSnapshot:
    return TargetSnapshot(
        selector_candidates=(UiaSelector(automation_id="field"),),
        focused_runtime_id=runtime_id,
        editable=True,
        is_password=is_password,
        observed_value=observed_value,
        bounds=None,
    )


def environment_snapshot() -> RecordingEnvironmentSnapshot:
    return RecordingEnvironmentSnapshot(
        client_left=10,
        client_top=20,
        client_width=800,
        client_height=600,
        dpi_x=96,
        dpi_y=96,
        monitor_scale=1.0,
        monitor_id="DISPLAY1",
        double_click_time_ms=500,
        drag_width_px=4,
        drag_height_px=4,
    )


def raw_event(**changes: object) -> RawInputEvent:
    payload: dict[str, object] = {
        "session_id": SESSION_ID,
        "event_id": EVENT_ID,
        "monotonic_ns": 10,
        "wall_time_utc": WALL_TIME,
        "event_type": RawEventType.MOUSE_WHEEL,
        "payload": {"delta": 120},
        "in_scope": True,
        "capture_state": "recording",
        "window_context": window_context(),
        "target_snapshot": target_snapshot(),
        "environment_snapshot": environment_snapshot(),
    }
    payload.update(changes)
    return RawInputEvent.model_validate(payload)


def native_key_event(
    *,
    key: str = "a",
    text: str | None = "a",
    focus: EventFocusSnapshot | None = None,
) -> NativeInputEvent:
    return NativeInputEvent(
        monotonic_ns=10,
        wall_time_utc=WALL_TIME,
        hook_time_ms=20,
        event_type=RawEventType.KEY_DOWN,
        focus=focus or focus_snapshot(),
        payload=FrozenMapping.empty(),
        key_token=SensitiveKeyToken.create(key=key, text=text),
    )


def test_raw_event_requires_utc_wall_time() -> None:
    with pytest.raises(ValidationError):
        raw_event(wall_time_utc=datetime(2026, 7, 29, 9, 0))


def test_out_of_scope_event_keeps_audit_metadata_but_redacts_key_payload() -> None:
    event = raw_event(
        event_type=RawEventType.KEY_DOWN,
        payload={"key": "SENTINEL_OUTSIDE"},
        in_scope=False,
    )

    assert event.in_scope is False
    assert event.event_id == EVENT_ID
    assert thaw_json(event.payload) == {"redacted": True}
    assert "SENTINEL_OUTSIDE" not in event.model_dump_json()


def test_paused_key_payload_is_always_redacted() -> None:
    event = raw_event(
        event_type=RawEventType.KEY_DOWN,
        payload={"key": "SENTINEL_PAUSED"},
        capture_state="paused",
    )

    assert thaw_json(event.payload) == {"redacted": True}
    assert "SENTINEL_PAUSED" not in event.model_dump_json()


def test_uncertain_context_direct_construction_redacts_payload_and_observed_value() -> None:
    event = raw_event(
        event_type=RawEventType.KEY_DOWN,
        payload={"text": "SENTINEL_UNCERTAIN"},
        window_context=window_context(confident=False),
        target_snapshot=target_snapshot(observed_value="SENTINEL_UNCERTAIN"),
    )

    assert thaw_json(event.payload) == {"redacted": True}
    assert event.target_snapshot is not None
    assert event.target_snapshot.observed_value is None
    assert "SENTINEL_UNCERTAIN" not in event.model_dump_json()


def test_raw_payload_is_defensively_copied_before_it_can_cross_threads() -> None:
    source = {"delta": {"axes": [0, 120]}}
    event = raw_event(event_type=RawEventType.MOUSE_WHEEL, payload=source)
    source["delta"]["axes"][1] = 999

    assert thaw_json(event.payload) == {"delta": {"axes": [0, 120]}}
    with pytest.raises(TypeError):
        event.payload["delta"]["axes"][1] = 999  # type: ignore[index, assignment]


def test_native_payload_is_defensively_copied() -> None:
    source = {"point": {"values": [1, 2]}}
    event = NativeInputEvent(
        monotonic_ns=1,
        wall_time_utc=WALL_TIME,
        hook_time_ms=2,
        event_type=RawEventType.MOUSE_MOVE,
        focus=focus_snapshot(),
        payload=source,  # type: ignore[arg-type]
    )
    source["point"]["values"][0] = 9

    assert thaw_json(event.payload) == {"point": {"values": [1, 2]}}


def test_password_target_removes_key_and_value_payload() -> None:
    event = enrich_and_sanitize_event(
        native_key_event(key="s", text="secret-letter"),
        session_id=uuid4(),
        context=window_context(),
        target=target_snapshot(is_password=True, observed_value="secret-letter"),
        environment=environment_snapshot(),
        in_scope=True,
    )

    encoded = event.model_dump_json()
    assert "secret-letter" not in encoded
    assert thaw_json(event.payload) == {"redacted": True}
    assert event.target_snapshot is not None
    assert event.target_snapshot.observed_value is None


def test_matching_event_time_identity_reveals_sensitive_token_once() -> None:
    native = native_key_event(key="enter", text=None)

    event = enrich_and_sanitize_event(
        native,
        session_id=SESSION_ID,
        context=window_context(),
        target=target_snapshot(observed_value=None),
        environment=environment_snapshot(),
        in_scope=True,
    )

    assert thaw_json(event.payload) == {"key": "enter"}
    with pytest.raises(RuntimeError):
        assert native.key_token is not None
        native.key_token.reveal_once()


def test_late_context_cannot_relabel_an_earlier_key() -> None:
    native = native_key_event(key="a", text="SENTINEL_RACE")

    event = enrich_and_sanitize_event(
        native,
        session_id=SESSION_ID,
        context=window_context(runtime_id=(9, 9, 9)),
        target=target_snapshot(runtime_id=(9, 9, 9), observed_value="SENTINEL_RACE"),
        environment=environment_snapshot(),
        in_scope=True,
    )

    assert thaw_json(event.payload) == {"redacted": True}
    assert "SENTINEL_RACE" not in event.model_dump_json()
    with pytest.raises(RuntimeError):
        assert native.key_token is not None
        native.key_token.reveal_once()


def test_sensitive_token_representation_never_contains_plaintext() -> None:
    token = SensitiveKeyToken.create(key="x", text="SENTINEL_TOKEN")

    assert repr(token) == "SensitiveKeyToken(<redacted>)"
    assert "SENTINEL_TOKEN" not in repr(token)
    token.discard()
    with pytest.raises(RuntimeError):
        token.reveal_once()
