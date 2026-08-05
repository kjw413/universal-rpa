"""The M6 plan's core regression guard.

Both (A) a working UiaFacade and (B) a focus snapshot carrying a runtime id
have to be true at once, or a keyboard event's target never resolves and the
key stays masked forever -- fixing only one half reproduces the exact defect
this plan started from (six recorded key events, all `{"redacted": true}`,
zero normalized candidates). No live window is involved: the facade and the
win32 boundary are both fakes, standing in for a *working* wiring rather than
the shipped `_CoordinateOnlyUia` stub that always returned nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from universal_rpa.adapters.windows.context import UiaFocusCache, WindowsWindowContext
from universal_rpa.adapters.windows.window_catalog import ClientGeometry
from universal_rpa.domain.recording import (
    EventFocusSnapshot,
    NativeInputEvent,
    RawEventType,
    RawInputEvent,
    RecordingTarget,
    SensitiveKeyToken,
    enrich_and_sanitize_event,
)
from universal_rpa.domain.types import thaw_json
from universal_rpa.ports.context import CapturedEventContext

NOW = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)


@dataclass
class _FakeElement:
    runtime_id: tuple[int, ...]
    automation_id: str = "field"
    control_type: str = "Edit"
    name: str = "Field"
    class_name: str = "TextBox"
    bounds: tuple[int, int, int, int] = (100, 100, 300, 140)
    editable: bool = True
    is_password: bool = False
    value: str | None = None


class _WorkingFakeUiaFacade:
    """A facade that can actually resolve elements -- unlike the shipped
    stub, which always returned None/()."""

    def __init__(self, elements: dict[tuple[int, ...], _FakeElement]) -> None:
        self._elements = elements

    def element_from_runtime_id(self, runtime_id: tuple[int, ...]) -> object | None:
        return self._elements.get(runtime_id)

    def elements_from_point(self, screen_x: int, screen_y: int) -> tuple[object, ...]:
        del screen_x, screen_y
        return ()

    def password_elements(self, top_level_hwnd: int) -> tuple[object, ...]:
        del top_level_hwnd
        return ()


class _FakeWin32:
    def client_geometry(self, hwnd: int) -> ClientGeometry:
        del hwnd
        return ClientGeometry(0, 0, 1200, 800)

    def window_dpi(self, hwnd: int) -> tuple[int, int]:
        del hwnd
        return (96, 96)

    def top_level_window(self, hwnd: int) -> int:
        return hwnd

    def is_owned_by(self, hwnd: int, owner_hwnd: int) -> bool:
        return hwnd == owner_hwnd

    def window_process_id(self, hwnd: int) -> int:
        del hwnd
        return 4242

    def process_executable(self, process_id: int) -> str:
        del process_id
        return "notepad.exe"

    def window_text(self, hwnd: int) -> str:
        del hwnd
        return "Untitled"

    def window_class(self, hwnd: int) -> str:
        del hwnd
        return "Notepad"

    def monitor_id(self, hwnd: int) -> str:
        del hwnd
        return "DISPLAY1"


_RUNTIME_ID = (11, 22, 33)


def _resolve_one_keystroke(
    element: _FakeElement,
    *,
    key: str = "a",
) -> tuple[CapturedEventContext, RawInputEvent]:
    """One key_down through the shipped capture -> sanitize pipeline, with both
    halves working: (A) a facade that resolves elements and (B) a focus
    snapshot carrying the runtime id to resolve."""

    published_focus = EventFocusSnapshot(
        foreground_hwnd=101,
        focused_hwnd=101,
        foreground_process_id=4242,
        cached_uia_runtime_id=_RUNTIME_ID,
        focus_event_time_ms=500,
        cache_generation=1,
        cache_confirmed=True,
    )
    cache = UiaFocusCache(
        EventFocusSnapshot(
            foreground_hwnd=0,
            focused_hwnd=None,
            foreground_process_id=1,
            cached_uia_runtime_id=None,
            focus_event_time_ms=0,
            cache_generation=0,
            cache_confirmed=False,
        )
    )
    cache.publish(published_focus)
    context = WindowsWindowContext(
        win32=_FakeWin32(),
        uia=_WorkingFakeUiaFacade({_RUNTIME_ID: element}),
        focus_cache=cache,
        settle_timeout_seconds=0,
    )
    event = NativeInputEvent(
        monotonic_ns=10,
        wall_time_utc=NOW,
        hook_time_ms=500,
        event_type=RawEventType.KEY_DOWN,
        focus=published_focus,
        payload={"key": key},
        key_token=SensitiveKeyToken.create(key=key, text=key),
    )
    captured = context.capture_context(
        event,
        RecordingTarget(
            process_id=4242,
            process_executable="notepad.exe",
            top_level_hwnd=101,
            window_title="Untitled",
            window_class="Notepad",
        ),
    )
    raw = enrich_and_sanitize_event(
        event,
        session_id=uuid4(),
        context=captured.window_context,
        target=captured.target_snapshot,
        environment=captured.environment_snapshot,
        in_scope=captured.in_scope,
    )
    return captured, raw


def test_a_keyboard_event_with_a_resolved_target_is_not_redacted() -> None:
    """(A)와 (B)를 함께 덮는다: 둘 중 하나만 고치면 이 테스트는 실패한다."""

    captured, raw = _resolve_one_keystroke(_FakeElement(runtime_id=_RUNTIME_ID))

    assert captured.target_snapshot is not None
    assert thaw_json(raw.payload) == {"key": "a", "text": "a"}


def test_a_password_field_keystroke_stays_redacted_even_though_it_resolves() -> None:
    """M6 made this check load-bearing for the first time.

    Before live resolution every keystroke was masked, so a typed password was
    safe by accident. Now that resolving a target reveals the key, is_password
    is the only thing between a password and the stored recording.
    """

    captured, raw = _resolve_one_keystroke(
        _FakeElement(runtime_id=_RUNTIME_ID, is_password=True),
        key="s",
    )

    assert captured.target_snapshot is not None
    assert captured.target_snapshot.is_password is True
    assert thaw_json(raw.payload) == {"redacted": True}


def test_a_password_fields_value_is_never_read_into_the_snapshot() -> None:
    secret, _ = _resolve_one_keystroke(
        _FakeElement(runtime_id=_RUNTIME_ID, is_password=True, value="hunter2")
    )
    ordinary, _ = _resolve_one_keystroke(_FakeElement(runtime_id=_RUNTIME_ID, value="hunter2"))

    # The ordinary field proves the value really is readable here, so the
    # password field's None is the masking working rather than a fake that
    # never had a value to leak.
    assert ordinary.target_snapshot is not None
    assert ordinary.target_snapshot.observed_value == "hunter2"
    assert secret.target_snapshot is not None
    assert secret.target_snapshot.observed_value is None


def test_a_missing_runtime_id_still_redacts_even_with_a_working_facade() -> None:
    """(A) alone is not enough: without (B), capture_context never even calls
    the facade, so the key stays masked."""
    focus_without_runtime_id = EventFocusSnapshot(
        foreground_hwnd=101,
        focused_hwnd=101,
        foreground_process_id=4242,
        cached_uia_runtime_id=None,
        focus_event_time_ms=500,
        cache_generation=1,
        cache_confirmed=True,
    )
    cache = UiaFocusCache(focus_without_runtime_id)
    facade = _WorkingFakeUiaFacade({(11, 22, 33): _FakeElement(runtime_id=(11, 22, 33))})
    context = WindowsWindowContext(
        win32=_FakeWin32(),
        uia=facade,
        focus_cache=cache,
        settle_timeout_seconds=0,
    )
    target = RecordingTarget(
        process_id=4242,
        process_executable="notepad.exe",
        top_level_hwnd=101,
        window_title="Untitled",
        window_class="Notepad",
    )
    key_token = SensitiveKeyToken.create(key="a", text="a")
    event = NativeInputEvent(
        monotonic_ns=10,
        wall_time_utc=NOW,
        hook_time_ms=500,
        event_type=RawEventType.KEY_DOWN,
        focus=focus_without_runtime_id,
        payload={"key": "a"},
        key_token=key_token,
    )

    captured = context.capture_context(event, target)
    raw = enrich_and_sanitize_event(
        event,
        session_id=uuid4(),
        context=captured.window_context,
        target=captured.target_snapshot,
        environment=captured.environment_snapshot,
        in_scope=captured.in_scope,
    )

    assert captured.target_snapshot is None
    assert thaw_json(raw.payload) == {"redacted": True}
