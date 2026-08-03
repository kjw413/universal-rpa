from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from unittest.mock import Mock

from universal_rpa.adapters.windows.capture import PynputInputCapture
from universal_rpa.adapters.windows.context import UiaFocusCache
from universal_rpa.domain.recording import EventFocusSnapshot, RawEventType
from universal_rpa.ports.capture import ControlCommand

NOW = datetime(2026, 7, 29, 9, 0, tzinfo=UTC)


@dataclass(frozen=True)
class FakeKey:
    name: str | None = None
    char: str | None = None

    def __str__(self) -> str:
        return self.char or self.name or "unknown"


@dataclass(frozen=True)
class FakeButton:
    name: str


class FakeListener:
    def __init__(self, callbacks: dict[str, object]) -> None:
        self.callbacks = callbacks
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


class FakeListenerFactory:
    def __init__(self) -> None:
        self.listener: FakeListener | None = None

    def __call__(self, **callbacks: object) -> FakeListener:
        self.listener = FakeListener(callbacks)
        return self.listener


def focus_cache() -> UiaFocusCache:
    return UiaFocusCache(
        EventFocusSnapshot(
            foreground_hwnd=100,
            focused_hwnd=101,
            foreground_process_id=200,
            cached_uia_runtime_id=(42, 7),
            focus_event_time_ms=250,
            cache_generation=1,
            cache_confirmed=True,
        )
    )


def make_capture(
    *,
    listener_factory: FakeListenerFactory | None = None,
    forbidden: tuple[object, ...] = (),
) -> PynputInputCapture:
    return PynputInputCapture(
        listener_factory=listener_factory or FakeListenerFactory(),
        context_cache=focus_cache(),
        clock=lambda: NOW,
        monotonic_ns=lambda: 10,
        hook_time_ms=lambda: 300,
        forbidden_native_dependencies=forbidden,
    )


def test_capture_callback_only_copies_cached_context_and_memory_sinks() -> None:
    event_sink = Mock()
    control_sink = Mock()
    win32 = Mock()
    ui_automation = Mock()
    capture = make_capture(forbidden=(win32, ui_automation))
    capture.start(event_sink, control_sink)

    capture._on_press(FakeKey(char="SENTINEL_KEY"))

    event = event_sink.call_args.args[0]
    assert event.focus.cached_uia_runtime_id == (42, 7)
    assert "SENTINEL_KEY" not in repr(event)
    control_sink.assert_not_called()
    win32.assert_not_called()
    ui_automation.assert_not_called()


def test_f12_uses_priority_control_sink_and_never_event_sink() -> None:
    event_sink = Mock()
    control_sink = Mock()
    capture = make_capture()
    capture.start(event_sink, control_sink)
    capture._on_press(FakeKey(name="ctrl_l"))
    capture._on_press(FakeKey(name="shift_l"))
    event_sink.reset_mock()

    capture._on_press(FakeKey(name="f12"))

    control_sink.assert_called_once_with(ControlCommand.STOP)
    event_sink.assert_not_called()


def test_enter_is_kept_as_fixed_command_token_without_text() -> None:
    event_sink = Mock()
    capture = make_capture()
    capture.start(event_sink, Mock())
    capture._on_press(FakeKey(name="enter"))

    event = event_sink.call_args.args[0]
    assert event.event_type is RawEventType.KEY_DOWN
    assert event.key_token is not None
    assert event.key_token.reveal_once() == ("enter", None)


def test_mouse_move_is_emitted_only_while_drag_button_is_pressed() -> None:
    event_sink = Mock()
    capture = make_capture()
    capture.start(event_sink, Mock())
    capture._on_move(10, 20)
    event_sink.assert_not_called()

    capture._on_click(10, 20, FakeButton("left"), True)
    capture._on_move(30, 40)
    capture._on_click(30, 40, FakeButton("left"), False)

    assert [call.args[0].event_type for call in event_sink.call_args_list] == [
        RawEventType.MOUSE_DOWN,
        RawEventType.MOUSE_MOVE,
        RawEventType.MOUSE_UP,
    ]


def test_stop_deactivates_listener() -> None:
    factory = FakeListenerFactory()
    capture = make_capture(listener_factory=factory)
    capture.start(Mock(), Mock())
    assert factory.listener is not None and factory.listener.started
    capture.stop()
    assert factory.listener.stopped
