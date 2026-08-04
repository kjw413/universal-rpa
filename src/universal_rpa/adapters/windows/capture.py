from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any, Protocol

from universal_rpa.domain.recording import NativeInputEvent, RawEventType, SensitiveKeyToken
from universal_rpa.domain.types import FrozenMapping, deep_freeze_json
from universal_rpa.ports.capture import (
    ControlCommand,
    ControlHotkeys,
    ControlSink,
    InputEventSink,
)

from .context import UiaFocusCache


class ListenerPort(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...


type ListenerFactory = Callable[..., ListenerPort]


class _CompositePynputListener:
    def __init__(self, **callbacks: Callable[..., None]) -> None:
        from pynput import keyboard, mouse  # type: ignore[import-untyped]

        self._keyboard = keyboard.Listener(
            on_press=callbacks["on_press"],
            on_release=callbacks["on_release"],
        )
        self._mouse = mouse.Listener(
            on_click=callbacks["on_click"],
            on_move=callbacks["on_move"],
            on_scroll=callbacks["on_scroll"],
        )

    def start(self) -> None:
        self._keyboard.start()
        self._mouse.start()

    def stop(self) -> None:
        self._keyboard.stop()
        self._mouse.stop()


def _default_listener_factory(**callbacks: Callable[..., None]) -> ListenerPort:
    return _CompositePynputListener(**callbacks)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _hook_time_ms() -> int:
    return int(time.monotonic() * 1000)


#: Maps every pynput modifier spelling onto the canonical chord modifier name.
MODIFIER_ALIASES = {
    "alt": "alt",
    "alt_l": "alt",
    "alt_r": "alt",
    "ctrl": "ctrl",
    "ctrl_l": "ctrl",
    "ctrl_r": "ctrl",
    "shift": "shift",
    "shift_l": "shift",
    "shift_r": "shift",
    "cmd": "win",
    "cmd_l": "win",
    "cmd_r": "win",
}


def normalize_key(key: object) -> tuple[str, str | None]:
    char = getattr(key, "char", None)
    if isinstance(char, str) and char:
        return char.casefold(), char
    name = getattr(key, "name", None)
    if isinstance(name, str) and name:
        return name.casefold(), None
    rendered = str(key).strip("'").casefold()
    if rendered.startswith("key."):
        rendered = rendered[4:]
    return rendered, rendered if len(rendered) == 1 else None


def _button_name(button: object) -> str:
    name = getattr(button, "name", None)
    if isinstance(name, str) and name:
        return name.casefold()
    rendered = str(button).casefold()
    return rendered.removeprefix("button.")


class PynputInputCapture:
    def __init__(
        self,
        *,
        context_cache: UiaFocusCache,
        listener_factory: ListenerFactory = _default_listener_factory,
        hotkeys: ControlHotkeys | None = None,
        clock: Callable[[], datetime] = _utc_now,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        hook_time_ms: Callable[[], int] = _hook_time_ms,
        forbidden_native_dependencies: Iterable[object] = (),
    ) -> None:
        self._context_cache = context_cache
        self._listener_factory = listener_factory
        self._hotkeys = hotkeys or ControlHotkeys()
        self._clock = clock
        self._monotonic_ns = monotonic_ns
        self._hook_time_ms = hook_time_ms
        self._forbidden_native_dependencies = tuple(forbidden_native_dependencies)
        self._event_sink: InputEventSink | None = None
        self._control_sink: ControlSink | None = None
        self._listener: ListenerPort | None = None
        self._pressed_modifiers: set[str] = set()
        self._pressed_buttons: set[str] = set()
        self._suppressed_keys: set[str] = set()

    def start(self, event_sink: InputEventSink, control_sink: ControlSink) -> None:
        if self._listener is not None:
            raise RuntimeError("input capture is already running")
        self._event_sink = event_sink
        self._control_sink = control_sink
        listener = self._listener_factory(
            on_press=self._on_press,
            on_release=self._on_release,
            on_click=self._on_click,
            on_move=self._on_move,
            on_scroll=self._on_scroll,
        )
        self._listener = listener
        listener.start()

    def stop(self) -> None:
        listener = self._listener
        self._listener = None
        if listener is not None:
            listener.stop()
        self._event_sink = None
        self._control_sink = None
        self._pressed_modifiers.clear()
        self._pressed_buttons.clear()
        self._suppressed_keys.clear()

    def _on_press(self, key: object) -> None:
        key_name, text = normalize_key(key)
        modifier = MODIFIER_ALIASES.get(key_name)
        if modifier is not None:
            self._pressed_modifiers.add(modifier)

        command = self._hotkey_command(key_name)
        if command is not None:
            self._suppressed_keys.add(key_name)
            sink = self._control_sink
            if sink is not None:
                sink(command)
            return
        self._emit_keyboard(RawEventType.KEY_DOWN, key_name, text)

    def _on_release(self, key: object) -> None:
        key_name, text = normalize_key(key)
        if key_name in self._suppressed_keys:
            self._suppressed_keys.discard(key_name)
        else:
            self._emit_keyboard(RawEventType.KEY_UP, key_name, text)
        modifier = MODIFIER_ALIASES.get(key_name)
        if modifier is not None:
            self._pressed_modifiers.discard(modifier)

    def _on_click(self, x: int, y: int, button: object, pressed: bool) -> None:
        button_name = _button_name(button)
        if pressed:
            self._pressed_buttons.add(button_name)
        event_type = RawEventType.MOUSE_DOWN if pressed else RawEventType.MOUSE_UP
        self._emit_mouse(event_type, {"x": x, "y": y, "button": button_name})
        if not pressed:
            self._pressed_buttons.discard(button_name)

    def _on_move(self, x: int, y: int) -> None:
        if not self._pressed_buttons:
            return
        self._emit_mouse(
            RawEventType.MOUSE_MOVE,
            {"x": x, "y": y, "buttons": sorted(self._pressed_buttons)},
        )

    def _on_scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        self._emit_mouse(
            RawEventType.MOUSE_WHEEL,
            {"x": x, "y": y, "delta_x": dx, "delta_y": dy},
        )

    def _hotkey_command(self, key_name: str) -> ControlCommand | None:
        modifiers = frozenset(self._pressed_modifiers)
        if key_name == self._hotkeys.stop.key.casefold() and self._hotkeys.stop.modifiers.issubset(
            modifiers
        ):
            return ControlCommand.STOP
        if (
            key_name == self._hotkeys.toggle_pause.key.casefold()
            and self._hotkeys.toggle_pause.modifiers.issubset(modifiers)
        ):
            return ControlCommand.TOGGLE_PAUSE
        return None

    def _emit_keyboard(
        self,
        event_type: RawEventType,
        key_name: str,
        text: str | None,
    ) -> None:
        sink = self._event_sink
        if sink is None:
            return
        sink(
            self._native_event(
                event_type,
                payload={"kind": "keyboard"},
                key_token=SensitiveKeyToken.create(key=key_name, text=text),
            )
        )

    def _emit_mouse(self, event_type: RawEventType, payload: dict[str, Any]) -> None:
        sink = self._event_sink
        if sink is None:
            return
        sink(self._native_event(event_type, payload=payload))

    def _native_event(
        self,
        event_type: RawEventType,
        *,
        payload: dict[str, Any],
        key_token: SensitiveKeyToken | None = None,
    ) -> NativeInputEvent:
        frozen_payload = deep_freeze_json(payload)
        if not isinstance(frozen_payload, FrozenMapping):
            raise TypeError("native event payload must be an object")
        return NativeInputEvent(
            monotonic_ns=self._monotonic_ns(),
            wall_time_utc=self._clock(),
            hook_time_ms=self._hook_time_ms(),
            event_type=event_type,
            focus=self._context_cache.snapshot(),
            payload=frozen_payload,
            key_token=key_token,
        )


__all__ = [
    "MODIFIER_ALIASES",
    "ListenerFactory",
    "ListenerPort",
    "PynputInputCapture",
    "normalize_key",
]
