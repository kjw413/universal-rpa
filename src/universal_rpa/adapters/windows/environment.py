"""Interactive Windows environment snapshots for guarded execution."""

from __future__ import annotations

import ctypes
from collections.abc import Callable

from universal_rpa.domain.errors import ErrorCode, RpaError
from universal_rpa.domain.targets import RuntimeEnvironment

from .window_catalog import PyWin32WindowFacade, Win32WindowFacade


class WindowsEnvironmentProbe:
    def __init__(
        self,
        win32: Win32WindowFacade | None = None,
        *,
        foreground_getter: Callable[[], int] | None = None,
    ) -> None:
        self._win32 = win32 or PyWin32WindowFacade()
        self._foreground_getter = foreground_getter or self._default_foreground

    @staticmethod
    def _default_foreground() -> int:
        return int(ctypes.windll.user32.GetForegroundWindow())

    def foreground_hwnd(self) -> int:
        """Read the current foreground HWND without exposing native globals."""

        return int(self._foreground_getter())

    def require_interactive_desktop(self) -> None:
        try:
            if self.foreground_hwnd() <= 0:
                raise OSError
        except (AttributeError, OSError):
            raise RpaError(
                ErrorCode.ENVIRONMENT_MISMATCH,
                "잠금·보안 데스크톱이 아닌 대화형 Windows 세션이 필요합니다.",
            ) from None

    def snapshot(self, hwnd: int) -> RuntimeEnvironment:
        self.require_interactive_desktop()
        try:
            process_id = self._win32.window_process_id(hwnd)
            executable = self._win32.process_executable(process_id)
            client = self._win32.client_geometry(hwnd)
            dpi_x, dpi_y = self._win32.window_dpi(hwnd)
            return RuntimeEnvironment(
                interactive_desktop=True,
                process_id=process_id,
                process_executable=executable,
                top_level_hwnd=hwnd,
                window_title=self._win32.window_text(hwnd),
                window_class=self._win32.window_class(hwnd),
                foreground_hwnd=self.foreground_hwnd(),
                dpi_x=dpi_x,
                dpi_y=dpi_y,
                client_width=client.width,
                client_height=client.height,
                monitor_scale=1.0,
            )
        except RpaError:
            raise
        except Exception:
            raise RpaError(
                ErrorCode.ENVIRONMENT_MISMATCH, "대상 창의 실행 환경을 확인할 수 없습니다."
            ) from None


__all__ = ["WindowsEnvironmentProbe"]
