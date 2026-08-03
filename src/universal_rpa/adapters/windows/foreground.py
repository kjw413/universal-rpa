"""Foreground verification immediately before every native input."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from universal_rpa.domain.errors import ErrorCode, RpaError

from .environment import WindowsEnvironmentProbe


@dataclass(frozen=True, slots=True)
class WindowIdentity:
    process_id: int
    process_executable: str
    top_level_hwnd: int
    window_class: str


class ForegroundGuard:
    def __init__(self, probe: WindowsEnvironmentProbe) -> None:
        self._probe = probe

    def verify(self, expected: WindowIdentity) -> None:
        runtime = self._probe.snapshot(expected.top_level_hwnd)
        if (
            runtime.foreground_hwnd != expected.top_level_hwnd
            or runtime.window_class != expected.window_class
            or Path(runtime.process_executable).name.casefold()
            != Path(expected.process_executable).name.casefold()
        ):
            raise RpaError(
                ErrorCode.FOREGROUND_MISMATCH,
                "입력 직전에 대상 프로그램 창이 전면에 있지 않습니다.",
            )


__all__ = ["ForegroundGuard", "WindowIdentity"]
