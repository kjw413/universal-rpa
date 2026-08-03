from __future__ import annotations

import ctypes
import threading
from typing import Protocol

DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
ERROR_ACCESS_DENIED = 5


class DpiApi(Protocol):
    def set_process_dpi_awareness_context(self, context: int) -> bool: ...

    def get_last_error(self) -> int: ...

    def get_process_dpi_awareness(self) -> int: ...


class _CtypesDpiApi:
    def set_process_dpi_awareness_context(self, context: int) -> bool:
        user32 = ctypes.windll.user32
        return bool(user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(context)))

    def get_last_error(self) -> int:
        return int(ctypes.get_last_error())

    def get_process_dpi_awareness(self) -> int:
        awareness = ctypes.c_int()
        shcore = ctypes.windll.shcore
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        result = int(shcore.GetProcessDpiAwareness(handle, ctypes.byref(awareness)))
        if result != 0:
            raise OSError(result, "unable to query process DPI awareness")
        return int(awareness.value)


_dpi_lock = threading.Lock()
_dpi_awareness_enabled = False


def enable_per_monitor_v2_dpi_awareness(api: DpiApi | None = None) -> None:
    """Enable process-wide per-monitor DPI awareness once, before UI creation."""

    global _dpi_awareness_enabled
    with _dpi_lock:
        if _dpi_awareness_enabled:
            return
        selected_api = api or _CtypesDpiApi()
        if selected_api.set_process_dpi_awareness_context(
            DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        ):
            _dpi_awareness_enabled = True
            return
        error = selected_api.get_last_error()
        if error == ERROR_ACCESS_DENIED and selected_api.get_process_dpi_awareness() == 2:
            _dpi_awareness_enabled = True
            return
        raise OSError(error, "unable to enable per-monitor-v2 DPI awareness")


def _reset_dpi_awareness_for_test() -> None:
    global _dpi_awareness_enabled
    with _dpi_lock:
        _dpi_awareness_enabled = False


__all__ = [
    "DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2",
    "DpiApi",
    "enable_per_monitor_v2_dpi_awareness",
]
