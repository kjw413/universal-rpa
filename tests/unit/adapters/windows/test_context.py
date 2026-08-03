from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from universal_rpa.adapters.windows.context import (
    UiaFocusCache,
    WindowsWindowContext,
    capture_target_snapshot,
)
from universal_rpa.adapters.windows.window_catalog import ClientGeometry
from universal_rpa.domain.recording import (
    EventFocusSnapshot,
    NativeInputEvent,
    RawEventType,
    RecordingTarget,
)
from universal_rpa.domain.targets import RuntimeEnvironment, WindowsTarget
from universal_rpa.ports.automation import CancellationToken, TargetCaptureRequest

NOW = datetime(2026, 7, 29, 9, 0, tzinfo=UTC)


@dataclass
class FakeElement:
    runtime_id: tuple[int, ...]
    automation_id: str = "field"
    control_type: str = "Edit"
    name: str = "Field"
    class_name: str = "TextBox"
    bounds: tuple[int, int, int, int] = (100, 100, 300, 140)
    editable: bool = True
    is_password: bool = False
    value_pattern: object | None = None


class FakeUia:
    def __init__(
        self,
        *,
        captured: dict[tuple[int, ...], FakeElement] | None = None,
        point: tuple[FakeElement, ...] = (),
        passwords: tuple[FakeElement, ...] = (),
    ) -> None:
        self.captured = captured or {}
        self.point = point
        self.passwords = passwords
        self.runtime_requests: list[tuple[int, ...]] = []

    def element_from_runtime_id(self, runtime_id: tuple[int, ...]) -> object | None:
        self.runtime_requests.append(runtime_id)
        return self.captured.get(runtime_id)

    def elements_from_point(self, screen_x: int, screen_y: int) -> tuple[object, ...]:
        del screen_x, screen_y
        return self.point

    def password_elements(self, top_level_hwnd: int) -> tuple[object, ...]:
        del top_level_hwnd
        return self.passwords


class FakeWin32:
    def __init__(self, *, owners: dict[int, int] | None = None) -> None:
        self.owners = owners or {}

    def enumerate_top_level_windows(self) -> tuple[int, ...]:
        return (101,)

    def is_window_visible(self, hwnd: int) -> bool:
        return True

    def is_window_cloaked(self, hwnd: int) -> bool:
        return False

    def window_text(self, hwnd: int) -> str:
        return "MIS"

    def window_class(self, hwnd: int) -> str:
        return "MisWindow"

    def window_process_id(self, hwnd: int) -> int:
        return 200

    def process_executable(self, process_id: int) -> str:
        return "mis.exe"

    def client_geometry(self, hwnd: int) -> ClientGeometry:
        return ClientGeometry(0, 0, 1200, 800)

    def owner_window(self, hwnd: int) -> int | None:
        return self.owners.get(hwnd)

    def window_dpi(self, hwnd: int) -> tuple[int, int]:
        return (96, 96)

    def monitor_id(self, hwnd: int) -> str:
        return "DISPLAY1"

    def top_level_window(self, hwnd: int) -> int:
        return hwnd

    def is_owned_by(self, hwnd: int, owner_hwnd: int) -> bool:
        current: int | None = hwnd
        while current is not None:
            if current == owner_hwnd:
                return True
            current = self.owners.get(current)
        return False

    def double_click_time_ms(self) -> int:
        return 500

    def drag_width_px(self) -> int:
        return 4

    def drag_height_px(self) -> int:
        return 4


class FakeScreenshot:
    def __init__(self, *, width: int = 1200, height: int = 800) -> None:
        self.width = width
        self.height = height

    def capture_client_png(self, hwnd: int, width: int, height: int) -> bytes:
        del hwnd, width, height
        return (
            b"\x89PNG\r\n\x1a\n"
            + b"\x00\x00\x00\rIHDR"
            + struct.pack(">II", self.width, self.height)
        )


def focus(
    *, runtime_id: tuple[int, ...] | None = (42, 7), confirmed: bool = True
) -> EventFocusSnapshot:
    return EventFocusSnapshot(
        foreground_hwnd=101,
        focused_hwnd=102,
        foreground_process_id=200,
        cached_uia_runtime_id=runtime_id,
        focus_event_time_ms=250,
        cache_generation=1,
        cache_confirmed=confirmed,
    )


def native_event(
    *, event_focus: EventFocusSnapshot | None = None, hwnd: int = 101
) -> NativeInputEvent:
    selected_focus = event_focus or focus()
    if hwnd != selected_focus.foreground_hwnd:
        selected_focus = EventFocusSnapshot(
            foreground_hwnd=hwnd,
            focused_hwnd=selected_focus.focused_hwnd,
            foreground_process_id=selected_focus.foreground_process_id,
            cached_uia_runtime_id=selected_focus.cached_uia_runtime_id,
            focus_event_time_ms=selected_focus.focus_event_time_ms,
            cache_generation=selected_focus.cache_generation,
            cache_confirmed=selected_focus.cache_confirmed,
        )
    return NativeInputEvent(
        monotonic_ns=10,
        wall_time_utc=NOW,
        hook_time_ms=300,
        event_type=RawEventType.MOUSE_DOWN,
        focus=selected_focus,
        payload={"x": 100, "y": 100, "button": "left"},
    )


def selected_window() -> RecordingTarget:
    return RecordingTarget(
        process_id=200,
        process_executable="mis.exe",
        top_level_hwnd=101,
        window_title="MIS",
        window_class="MisWindow",
    )


def runtime_environment() -> RuntimeEnvironment:
    return RuntimeEnvironment(
        interactive_desktop=True,
        process_id=200,
        process_executable="mis.exe",
        top_level_hwnd=101,
        window_title="MIS",
        window_class="MisWindow",
        foreground_hwnd=101,
        dpi_x=96,
        dpi_y=96,
        client_width=1200,
        client_height=800,
        monitor_scale=1.0,
    )


def test_worker_resolves_captured_runtime_id_not_later_live_focus() -> None:
    captured = FakeElement(runtime_id=(42, 7), is_password=False)
    uia = FakeUia(captured={(42, 7): captured})
    context = WindowsWindowContext(win32=FakeWin32(), uia=uia, settle_timeout_seconds=0)

    result = context.capture_context(native_event(), selected_window())

    assert result.target_snapshot is not None
    assert result.target_snapshot.is_password is False
    assert result.window_context.focused_runtime_id == (42, 7)
    assert uia.runtime_requests == [(42, 7)]


def test_missing_captured_runtime_id_is_uncertain_not_live_substitution() -> None:
    uia = FakeUia(point=(FakeElement(runtime_id=(9, 9)),))
    context = WindowsWindowContext(win32=FakeWin32(), uia=uia, settle_timeout_seconds=0)

    result = context.capture_context(
        native_event(event_focus=focus(runtime_id=None, confirmed=False)),
        selected_window(),
    )

    assert result.window_context.context_confident is False
    assert result.target_snapshot is None
    assert uia.runtime_requests == []


def test_owned_modal_is_inside_selected_window_scope() -> None:
    element = FakeElement(runtime_id=(42, 7))
    context = WindowsWindowContext(
        win32=FakeWin32(owners={202: 101}),
        uia=FakeUia(captured={(42, 7): element}),
        settle_timeout_seconds=0,
    )
    result = context.capture_context(native_event(hwnd=202), selected_window())
    assert result.in_scope is True


def test_password_control_value_is_never_read() -> None:
    value_pattern = Mock()
    element = FakeElement(
        runtime_id=(42, 7),
        is_password=True,
        value_pattern=value_pattern,
    )

    snapshot = capture_target_snapshot(element)

    value_pattern.get_value.assert_not_called()
    assert snapshot.observed_value is None


def test_late_focus_transition_at_input_time_invalidates_cached_identity() -> None:
    initial = focus()
    cache = UiaFocusCache(initial)
    cache.publish(
        EventFocusSnapshot(
            foreground_hwnd=101,
            focused_hwnd=103,
            foreground_process_id=200,
            cached_uia_runtime_id=(8, 8),
            focus_event_time_ms=290,
            cache_generation=2,
            cache_confirmed=True,
        )
    )
    context = WindowsWindowContext(
        win32=FakeWin32(),
        uia=FakeUia(captured={(42, 7): FakeElement(runtime_id=(42, 7))}),
        focus_cache=cache,
        settle_timeout_seconds=0,
    )
    result = context.capture_context(native_event(event_focus=initial), selected_window())
    assert result.window_context.context_confident is False
    assert result.target_snapshot is None


def test_explicit_target_capture_keeps_region_metadata_in_each_candidate() -> None:
    password = FakeElement(
        runtime_id=(42, 7),
        is_password=True,
        bounds=(120, 160, 360, 200),
    )
    context = WindowsWindowContext(
        win32=FakeWin32(),
        uia=FakeUia(point=(password,), passwords=(password,)),
        screenshot=FakeScreenshot(),
        settle_timeout_seconds=0,
    )
    result = context.capture_target(
        TargetCaptureRequest(
            runtime=runtime_environment(),
            screen_x=200,
            screen_y=180,
            focused_runtime_id=(42, 7),
        ),
        CancellationToken(),
    )

    assert result.target is not None
    assert result.target.adapter_id == "windows"
    assert result.target in result.candidates
    target = WindowsTarget.model_validate(result.target.payload)
    assert target.target_region is not None
    assert target.mandatory_sensitive_regions
    assert target.user_sensitive_regions == ()
    assert result.preview_png is not None and result.preview_png.startswith(b"\x89PNG")
    assert struct.unpack(">II", result.preview_png[16:24]) == (1200, 800)


def test_capture_dimension_mismatch_fails_closed() -> None:
    context = WindowsWindowContext(
        win32=FakeWin32(),
        uia=FakeUia(point=(FakeElement(runtime_id=(42, 7)),)),
        screenshot=FakeScreenshot(width=100, height=100),
        settle_timeout_seconds=0,
    )
    with pytest.raises(ValueError, match="dimensions"):
        context.capture_target(
            TargetCaptureRequest(
                runtime=runtime_environment(),
                screen_x=200,
                screen_y=180,
                focused_runtime_id=(42, 7),
            ),
            CancellationToken(),
        )
