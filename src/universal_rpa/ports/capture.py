from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from universal_rpa.domain.recording import KeyChord, NativeInputEvent


class ControlCommand(StrEnum):
    TOGGLE_PAUSE = "toggle_pause"
    STOP = "stop"


InputEventSink = Callable[[NativeInputEvent], None]
ControlSink = Callable[[ControlCommand], None]


@dataclass(frozen=True, slots=True)
class ControlHotkeys:
    toggle_pause: KeyChord = field(
        default_factory=lambda: KeyChord("f11", frozenset({"ctrl", "shift"}))
    )
    stop: KeyChord = field(default_factory=lambda: KeyChord("f12", frozenset({"ctrl", "shift"})))


class InputCapturePort(Protocol):
    def start(self, event_sink: InputEventSink, control_sink: ControlSink) -> None: ...

    def stop(self) -> None: ...


__all__ = [
    "ControlCommand",
    "ControlHotkeys",
    "ControlSink",
    "InputCapturePort",
    "InputEventSink",
]
