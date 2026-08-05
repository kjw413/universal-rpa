from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from universal_rpa.application.normalization import (
    KeyboardNormalizationConfig,
    normalize_keyboard_events,
    suggest_variable_types,
)
from universal_rpa.domain.action_parameters import HotkeyParameters, PressKeyParameters
from universal_rpa.domain.recording import (
    RawEventType,
    RawInputEvent,
    RecordingEnvironmentSnapshot,
    TargetSnapshot,
    WindowContextSnapshot,
)
from universal_rpa.domain.targets import NormalizedRect, UiaSelector, WindowsTarget
from universal_rpa.domain.types import FrozenMapping

SESSION_ID = UUID("00000000-0000-0000-0000-000000000601")
NOW = datetime(2026, 7, 29, 9, 0, tzinfo=UTC)
REGION = NormalizedRect(x=0.35, y=0.25, width=0.1, height=0.1)


def keyboard_event(
    key: str,
    *,
    event_number: int,
    monotonic_ms: int,
    event_type: RawEventType = RawEventType.KEY_DOWN,
    text: str | None = None,
    observed_value: str | None = None,
    runtime_id: tuple[int, ...] = (1, 2, 3),
    editable: bool = True,
    is_password: bool = False,
    ime_active: bool = False,
    executable: str = "mis.exe",
    window_class: str = "MainFrame",
    dpi: tuple[int, int] = (96, 96),
    client_size: tuple[int, int] = (1000, 1000),
) -> RawInputEvent:
    payload: dict[str, object] = {"key": key}
    if text is not None:
        payload["text"] = text
    if ime_active:
        payload["ime_active"] = True
    return RawInputEvent.model_validate(
        {
            "session_id": SESSION_ID,
            "event_id": UUID(int=event_number),
            "monotonic_ns": monotonic_ms * 1_000_000,
            "wall_time_utc": NOW,
            "event_type": event_type,
            "payload": payload,
            "in_scope": True,
            "capture_state": "recording",
            "window_context": WindowContextSnapshot(
                foreground_hwnd=100,
                focused_hwnd=101,
                process_id=200,
                process_executable=executable,
                top_level_hwnd=100,
                window_title="MIS",
                window_class=window_class,
                focused_runtime_id=runtime_id,
                selected_top_level_hwnd=100,
                owned_by_selected_window=True,
                context_confident=True,
            ),
            "target_snapshot": TargetSnapshot(
                selector_candidates=(UiaSelector(automation_id="field"),),
                focused_runtime_id=runtime_id,
                editable=editable,
                is_password=is_password,
                observed_value=observed_value,
                bounds=REGION,
            ),
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


def _redacted_key_up(
    *,
    event_number: int,
    monotonic_ms: int,
    runtime_id: tuple[int, ...] = (1, 2, 3),
) -> RawInputEvent:
    """A key-up whose target could not be confirmed at that instant, exactly
    as capture_context/enrich_and_sanitize_event would mask it -- the
    identity of the released key is unknown, which is what makes this case
    dangerous for modifier bookkeeping."""
    return RawInputEvent.model_validate(
        {
            "session_id": SESSION_ID,
            "event_id": UUID(int=event_number),
            "monotonic_ns": monotonic_ms * 1_000_000,
            "wall_time_utc": NOW,
            "event_type": RawEventType.KEY_UP,
            "payload": {"redacted": True},
            "in_scope": True,
            "capture_state": "recording",
            "window_context": WindowContextSnapshot(
                foreground_hwnd=100,
                focused_hwnd=101,
                process_id=200,
                process_executable="mis.exe",
                top_level_hwnd=100,
                window_title="MIS",
                window_class="MainFrame",
                focused_runtime_id=runtime_id,
                selected_top_level_hwnd=100,
                owned_by_selected_window=True,
                context_confident=False,
            ),
            "target_snapshot": None,
            "environment_snapshot": RecordingEnvironmentSnapshot(
                client_left=0,
                client_top=0,
                client_width=1000,
                client_height=1000,
                dpi_x=96,
                dpi_y=96,
                monitor_scale=1.0,
                monitor_id="DISPLAY1",
                double_click_time_ms=500,
                drag_width_px=4,
                drag_height_px=4,
            ),
        }
    )


def test_a_masked_modifier_release_does_not_leave_ctrl_stuck_forever() -> None:
    """Observed live: the 'a'/'ctrl' key-up events momentarily failed target
    confirmation and were masked, losing their identity. A later, unrelated
    Enter press must not inherit a 'ctrl' modifier that was actually released
    -- otherwise it is wrongly recorded as a Ctrl-hotkey for the rest of the
    session instead of a plain key press."""
    events = (
        keyboard_event("ctrl", event_number=1, monotonic_ms=0, editable=False),
        keyboard_event("a", event_number=2, monotonic_ms=10, text="a", editable=False),
        _redacted_key_up(event_number=3, monotonic_ms=20),
        _redacted_key_up(event_number=4, monotonic_ms=30),
        keyboard_event("enter", event_number=5, monotonic_ms=200),
    )

    result = normalize_keyboard_events(events)

    assert [candidate.action_type for candidate in result.candidates] == [
        "windows.hotkey",
        "windows.press_key",
    ]


def ctrl_a_date_enter() -> tuple[RawInputEvent, ...]:
    events: list[RawInputEvent] = [
        keyboard_event("ctrl", event_number=1, monotonic_ms=0, editable=False),
        keyboard_event("a", event_number=2, monotonic_ms=10, text="a", editable=False),
        keyboard_event(
            "a",
            event_number=3,
            monotonic_ms=20,
            event_type=RawEventType.KEY_UP,
            editable=False,
        ),
        keyboard_event(
            "ctrl",
            event_number=4,
            monotonic_ms=30,
            event_type=RawEventType.KEY_UP,
            editable=False,
        ),
    ]
    for offset, character in enumerate("2026-07-27", start=5):
        events.append(
            keyboard_event(
                character,
                event_number=offset,
                monotonic_ms=offset * 10,
                text=character,
                observed_value="2026-07-27" if character == "7" and offset == 14 else None,
            )
        )
    events.append(keyboard_event("enter", event_number=20, monotonic_ms=200))
    return tuple(events)


def test_ctrl_a_date_enter_becomes_three_actions() -> None:
    result = normalize_keyboard_events(ctrl_a_date_enter())

    assert [candidate.action_type for candidate in result.candidates] == [
        "windows.hotkey",
        "windows.set_text",
        "windows.press_key",
    ]
    hotkey = HotkeyParameters.model_validate(result.candidates[0].parameters)
    press_key = PressKeyParameters.model_validate(result.candidates[2].parameters)
    assert (hotkey.key, hotkey.modifiers) == ("a", ("ctrl",))
    assert press_key.key == "enter"
    assert result.candidates[1].parameters == FrozenMapping.empty()
    assert result.candidates[1].value is not None
    assert result.candidates[1].value.mode == "literal"
    assert result.candidates[1].value.display_value == "2026-07-27"
    assert [suggestion.kind for suggestion in result.candidates[1].suggestions] == ["date_variable"]


def test_korean_uses_committed_uia_value_not_physical_reconstruction() -> None:
    events = tuple(
        keyboard_event(
            key,
            event_number=index,
            monotonic_ms=index * 10,
            text=key,
            observed_value="생산실적" if index == 4 else None,
            ime_active=True,
        )
        for index, key in enumerate(("t", "o", "d", "t"), start=1)
    )
    candidate = normalize_keyboard_events(events).candidates[0]
    assert candidate.value is not None
    assert candidate.value.display_value == "생산실적"


def test_ime_without_committed_value_requires_confirmation() -> None:
    events = (
        keyboard_event(
            "r",
            event_number=1,
            monotonic_ms=0,
            text="r",
            ime_active=True,
        ),
        keyboard_event(
            "k",
            event_number=2,
            monotonic_ms=10,
            text="k",
            ime_active=True,
        ),
    )
    candidate = normalize_keyboard_events(events).candidates[0]
    assert candidate.value is not None
    assert candidate.value.display_value is None
    assert candidate.requires_confirmation is True


def test_keyboard_candidate_preserves_event_time_target_environment() -> None:
    event = keyboard_event(
        "a",
        event_number=1,
        monotonic_ms=0,
        text="a",
        observed_value="abc",
        executable=r"C:\MIS\mis.exe",
        window_class="MainFrame",
        dpi=(144, 144),
        client_size=(1200, 800),
    )
    candidate = normalize_keyboard_events((event,)).candidates[0]
    assert candidate.target is not None
    target = WindowsTarget.model_validate(candidate.target.payload)
    fallback = target.coordinate_fallback
    assert fallback is not None
    assert fallback.recorded_process_executable == "mis.exe"
    assert fallback.recorded_window_class == "MainFrame"
    assert (fallback.recorded_dpi_x, fallback.recorded_dpi_y) == (144, 144)
    assert (fallback.recorded_client_width, fallback.recorded_client_height) == (1200, 800)
    assert (fallback.point.x, fallback.point.y) == (0.4, 0.3)
    assert target.target_region == REGION


def test_focus_change_and_one_second_gap_split_text_groups() -> None:
    events = (
        keyboard_event("a", event_number=1, monotonic_ms=0, text="a", observed_value="a"),
        keyboard_event(
            "b",
            event_number=2,
            monotonic_ms=10,
            text="b",
            observed_value="b",
            runtime_id=(9, 9),
        ),
        keyboard_event(
            "c",
            event_number=3,
            monotonic_ms=1020,
            text="c",
            observed_value="c",
            runtime_id=(9, 9),
        ),
    )
    result = normalize_keyboard_events(
        events,
        config=KeyboardNormalizationConfig(text_commit_gap_ns=1_000_000_000),
    )
    assert [
        candidate.value.display_value for candidate in result.candidates if candidate.value
    ] == [
        "a",
        "b",
        "c",
    ]


def test_recorder_control_chords_never_become_candidates() -> None:
    events = (
        keyboard_event("ctrl", event_number=1, monotonic_ms=0, editable=False),
        keyboard_event("shift", event_number=2, monotonic_ms=10, editable=False),
        keyboard_event("f11", event_number=3, monotonic_ms=20, editable=False),
    )
    assert normalize_keyboard_events(events).candidates == ()


def test_variable_recognizers_are_suggestions_only() -> None:
    assert [item.kind for item in suggest_variable_types("2026-07-27")] == ["date_variable"]
    assert [item.kind for item in suggest_variable_types("1,234.50")] == ["number_variable"]
    assert [item.kind for item in suggest_variable_types(r"C:\Exports\result.xlsx")] == [
        "path_variable"
    ]
