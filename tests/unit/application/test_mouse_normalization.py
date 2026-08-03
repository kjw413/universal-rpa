from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from universal_rpa.application.normalization import MouseThresholds, normalize_mouse_events
from universal_rpa.domain.action_parameters import DragParameters, ScrollParameters
from universal_rpa.domain.recording import (
    RawEventType,
    RawInputEvent,
    RecordingEnvironmentSnapshot,
    TargetSnapshot,
    WindowContextSnapshot,
)
from universal_rpa.domain.targets import NormalizedRect, UiaSelector, WindowsTarget

SESSION_ID = UUID("00000000-0000-0000-0000-000000000501")
NOW = datetime(2026, 7, 29, 9, 0, tzinfo=UTC)
REGION = NormalizedRect(x=0.2, y=0.3, width=0.1, height=0.05)


def target_snapshot(*, selectors: int = 1) -> TargetSnapshot:
    return TargetSnapshot(
        selector_candidates=tuple(
            UiaSelector(automation_id=f"button-{index}") for index in range(selectors)
        ),
        focused_runtime_id=(1, 2, 3),
        editable=False,
        is_password=False,
        observed_value=None,
        bounds=REGION,
    )


def raw_mouse_event(
    event_type: RawEventType,
    *,
    event_number: int,
    monotonic_ms: int,
    x: int = 100,
    y: int = 100,
    button: str = "left",
    extra_payload: dict[str, object] | None = None,
    executable: str = "mis.exe",
    window_class: str = "MainFrame",
    dpi: tuple[int, int] = (96, 96),
    client_size: tuple[int, int] = (1000, 1000),
    snapshot: TargetSnapshot | None = None,
    in_scope: bool = True,
    capture_state: str = "recording",
) -> RawInputEvent:
    payload: dict[str, object] = {"x": x, "y": y}
    if event_type in {RawEventType.MOUSE_DOWN, RawEventType.MOUSE_UP}:
        payload["button"] = button
    if extra_payload:
        payload.update(extra_payload)
    return RawInputEvent.model_validate(
        {
            "session_id": SESSION_ID,
            "event_id": UUID(int=event_number),
            "monotonic_ns": monotonic_ms * 1_000_000,
            "wall_time_utc": NOW,
            "event_type": event_type,
            "payload": payload,
            "in_scope": in_scope,
            "capture_state": capture_state,
            "window_context": WindowContextSnapshot(
                foreground_hwnd=100,
                focused_hwnd=101,
                process_id=200,
                process_executable=executable,
                top_level_hwnd=100,
                window_title="MIS",
                window_class=window_class,
                focused_runtime_id=(1, 2, 3),
                selected_top_level_hwnd=100,
                owned_by_selected_window=True,
                context_confident=True,
            ),
            "target_snapshot": snapshot or target_snapshot(),
            "environment_snapshot": RecordingEnvironmentSnapshot(
                client_left=0,
                client_top=0,
                client_width=client_size[0],
                client_height=client_size[1],
                dpi_x=dpi[0],
                dpi_y=dpi[1],
                monitor_scale=dpi[0] / 96,
                monitor_id="DISPLAY1",
                double_click_time_ms=500,
                drag_width_px=4,
                drag_height_px=4,
            ),
        }
    )


def thresholds() -> MouseThresholds:
    return MouseThresholds(
        double_click_time_ms=500,
        double_click_width_px=4,
        double_click_height_px=4,
        drag_width_px=4,
        drag_height_px=4,
    )


def test_two_os_qualified_clicks_become_one_double_click() -> None:
    events = (
        raw_mouse_event(RawEventType.MOUSE_DOWN, event_number=1, monotonic_ms=0),
        raw_mouse_event(RawEventType.MOUSE_UP, event_number=2, monotonic_ms=20),
        raw_mouse_event(
            RawEventType.MOUSE_DOWN,
            event_number=3,
            monotonic_ms=270,
            x=102,
            y=102,
        ),
        raw_mouse_event(
            RawEventType.MOUSE_UP,
            event_number=4,
            monotonic_ms=290,
            x=102,
            y=102,
        ),
    )

    result = normalize_mouse_events(events, thresholds=thresholds())

    assert [item.action_type for item in result.candidates] == ["windows.double_click"]
    assert len(result.candidates[0].source_event_ids) == 4


def test_move_without_drag_creates_no_candidate() -> None:
    events = (
        raw_mouse_event(RawEventType.MOUSE_MOVE, event_number=1, monotonic_ms=0),
        raw_mouse_event(RawEventType.MOUSE_MOVE, event_number=2, monotonic_ms=10),
    )
    assert normalize_mouse_events(events, thresholds=thresholds()).candidates == ()


def test_drag_uses_canonical_typed_end_point() -> None:
    events = (
        raw_mouse_event(
            RawEventType.MOUSE_DOWN,
            event_number=1,
            monotonic_ms=0,
            x=100,
            y=200,
        ),
        raw_mouse_event(
            RawEventType.MOUSE_MOVE,
            event_number=2,
            monotonic_ms=10,
            x=800,
            y=700,
            extra_payload={"buttons": ["left"]},
        ),
        raw_mouse_event(
            RawEventType.MOUSE_UP,
            event_number=3,
            monotonic_ms=20,
            x=800,
            y=700,
        ),
    )

    candidate = normalize_mouse_events(events, thresholds=thresholds()).candidates[0]
    params = DragParameters.model_validate(candidate.parameters)

    assert (params.end_point.x, params.end_point.y, params.button) == (0.8, 0.7, "left")


def test_wheel_preserves_signed_horizontal_and_vertical_deltas() -> None:
    event = raw_mouse_event(
        RawEventType.MOUSE_WHEEL,
        event_number=1,
        monotonic_ms=0,
        extra_payload={"delta_x": -120, "delta_y": 240},
    )
    candidate = normalize_mouse_events((event,), thresholds=thresholds()).candidates[0]
    params = ScrollParameters.model_validate(candidate.parameters)
    assert (params.horizontal_delta, params.vertical_delta) == (-120, 240)


def test_missing_mouse_up_warns_instead_of_guessing_drag() -> None:
    events = (
        raw_mouse_event(RawEventType.MOUSE_DOWN, event_number=1, monotonic_ms=0),
        raw_mouse_event(
            RawEventType.MOUSE_MOVE,
            event_number=2,
            monotonic_ms=10,
            x=500,
            y=500,
            extra_payload={"buttons": ["left"]},
        ),
    )

    result = normalize_mouse_events(events, thresholds=thresholds())

    assert result.candidates == ()
    assert result.warnings[0].code == "incomplete_mouse_gesture"


def test_click_candidate_preserves_environment_and_relative_point() -> None:
    events = (
        raw_mouse_event(
            RawEventType.MOUSE_DOWN,
            event_number=1,
            monotonic_ms=0,
            x=400,
            y=675,
            executable=r"C:\Program Files\MIS\mis.exe",
            window_class="MainFrame",
            dpi=(120, 120),
            client_size=(1600, 900),
        ),
        raw_mouse_event(
            RawEventType.MOUSE_UP,
            event_number=2,
            monotonic_ms=20,
            x=400,
            y=675,
            executable=r"C:\Program Files\MIS\mis.exe",
            window_class="MainFrame",
            dpi=(120, 120),
            client_size=(1600, 900),
        ),
    )

    candidate = normalize_mouse_events(events, thresholds=thresholds()).candidates[0]
    assert candidate.target is not None
    target = WindowsTarget.model_validate(candidate.target.payload)
    fallback = target.coordinate_fallback
    assert fallback is not None
    assert fallback.recorded_process_executable == "mis.exe"
    assert fallback.recorded_window_class == "MainFrame"
    assert (fallback.recorded_dpi_x, fallback.recorded_dpi_y) == (120, 120)
    assert (fallback.recorded_client_width, fallback.recorded_client_height) == (1600, 900)
    assert (fallback.point.x, fallback.point.y) == (0.25, 0.75)
    assert target.target_region == REGION


def test_ambiguous_selector_keeps_fallback_and_requires_confirmation() -> None:
    ambiguous = target_snapshot(selectors=2)
    events = (
        raw_mouse_event(
            RawEventType.MOUSE_DOWN,
            event_number=1,
            monotonic_ms=0,
            snapshot=ambiguous,
        ),
        raw_mouse_event(
            RawEventType.MOUSE_UP,
            event_number=2,
            monotonic_ms=20,
            snapshot=ambiguous,
        ),
    )
    candidate = normalize_mouse_events(events, thresholds=thresholds()).candidates[0]
    assert candidate.target is not None
    assert candidate.requires_confirmation is True
    assert WindowsTarget.model_validate(candidate.target.payload).selector is None


def test_paused_and_out_of_scope_mouse_events_are_ignored() -> None:
    events = (
        raw_mouse_event(
            RawEventType.MOUSE_WHEEL,
            event_number=1,
            monotonic_ms=0,
            extra_payload={"delta_y": 120},
            capture_state="paused",
        ),
        raw_mouse_event(
            RawEventType.MOUSE_WHEEL,
            event_number=2,
            monotonic_ms=10,
            extra_payload={"delta_y": 120},
            in_scope=False,
        ),
    )
    assert normalize_mouse_events(events, thresholds=thresholds()).candidates == ()
