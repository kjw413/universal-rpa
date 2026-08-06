"""Turn "where the mouse is right now" into one target-capture request.

The retarget dialog is modal, so the window the user points at is never the
foreground window -- that is the Studio itself.  The request therefore has to
describe the window *under the pointer*, resolved up to its top level, which is
the frame :class:`WindowsWindowContext` measures its client geometry against.

Every Win32 call arrives as an injected callable so the rule this enforces --
top level, not the raw child, and never the foreground -- stays testable
without a desktop.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from universal_rpa.domain.targets import RuntimeEnvironment
from universal_rpa.ports.automation import TargetCaptureRequest


class EnvironmentSnapshotPort(Protocol):
    def snapshot(self, hwnd: int) -> RuntimeEnvironment: ...


class CursorTargetRequestFactory:
    def __init__(
        self,
        *,
        probe: EnvironmentSnapshotPort,
        cursor_position: Callable[[], tuple[int, int]],
        window_from_point: Callable[[int, int], int],
        top_level_window: Callable[[int], int],
        focused_runtime_id: Callable[[], tuple[int, ...] | None] | None = None,
    ) -> None:
        self._probe = probe
        self._cursor_position = cursor_position
        self._window_from_point = window_from_point
        self._top_level_window = top_level_window
        self._focused_runtime_id = focused_runtime_id

    def __call__(self) -> TargetCaptureRequest:
        screen_x, screen_y = self._cursor_position()
        hwnd = self._window_from_point(screen_x, screen_y)
        runtime = self._probe.snapshot(self._top_level_window(hwnd))
        return TargetCaptureRequest(
            runtime=runtime,
            screen_x=screen_x,
            screen_y=screen_y,
            focused_runtime_id=self._focused_runtime_id() if self._focused_runtime_id else None,
        )


__all__ = ["CursorTargetRequestFactory", "EnvironmentSnapshotPort"]
