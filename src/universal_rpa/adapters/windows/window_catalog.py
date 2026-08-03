from __future__ import annotations

import ctypes
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from universal_rpa.domain.recording import RecordingTarget


@dataclass(frozen=True, slots=True)
class ClientGeometry:
    left: int
    top: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("client dimensions must be positive")


@dataclass(frozen=True, slots=True)
class WindowDescriptor:
    hwnd: int
    process_id: int
    process_executable: str
    title: str
    window_class: str
    client: ClientGeometry
    owner_hwnd: int | None
    dpi_x: int
    dpi_y: int
    monitor_scale: float
    monitor_id: str

    def to_recording_target(self) -> RecordingTarget:
        return RecordingTarget(
            process_id=self.process_id,
            process_executable=self.process_executable,
            top_level_hwnd=self.hwnd,
            window_title=self.title,
            window_class=self.window_class,
        )


class Win32WindowFacade(Protocol):
    def enumerate_top_level_windows(self) -> Iterable[int]: ...

    def is_window_visible(self, hwnd: int) -> bool: ...

    def is_window_cloaked(self, hwnd: int) -> bool: ...

    def window_text(self, hwnd: int) -> str: ...

    def window_class(self, hwnd: int) -> str: ...

    def window_process_id(self, hwnd: int) -> int: ...

    def process_executable(self, process_id: int) -> str: ...

    def client_geometry(self, hwnd: int) -> ClientGeometry: ...

    def owner_window(self, hwnd: int) -> int | None: ...

    def window_dpi(self, hwnd: int) -> tuple[int, int]: ...

    def monitor_id(self, hwnd: int) -> str: ...

    def top_level_window(self, hwnd: int) -> int: ...

    def is_owned_by(self, hwnd: int, owner_hwnd: int) -> bool: ...


class PyWin32WindowFacade:
    """Small pywin32/ctypes facade kept outside recording callbacks."""

    def __init__(self) -> None:
        import win32api  # type: ignore[import-untyped]
        import win32gui  # type: ignore[import-untyped]
        import win32process  # type: ignore[import-untyped]

        self._win32api = win32api
        self._win32gui = win32gui
        self._win32process = win32process

    def enumerate_top_level_windows(self) -> tuple[int, ...]:
        handles: list[int] = []

        def collect(hwnd: int, _: object) -> bool:
            handles.append(int(hwnd))
            return True

        self._win32gui.EnumWindows(collect, None)
        return tuple(handles)

    def is_window_visible(self, hwnd: int) -> bool:
        return bool(self._win32gui.IsWindowVisible(hwnd))

    def is_window_cloaked(self, hwnd: int) -> bool:
        cloaked = ctypes.c_int()
        result = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            hwnd,
            14,
            ctypes.byref(cloaked),
            ctypes.sizeof(cloaked),
        )
        return result == 0 and bool(cloaked.value)

    def window_text(self, hwnd: int) -> str:
        return str(self._win32gui.GetWindowText(hwnd))

    def window_class(self, hwnd: int) -> str:
        return str(self._win32gui.GetClassName(hwnd))

    def window_process_id(self, hwnd: int) -> int:
        _, process_id = self._win32process.GetWindowThreadProcessId(hwnd)
        return int(process_id)

    def process_executable(self, process_id: int) -> str:
        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            process_query_limited_information,
            False,
            process_id,
        )
        if not handle:
            raise OSError("unable to open window process")
        try:
            capacity = ctypes.c_ulong(32768)
            buffer = ctypes.create_unicode_buffer(capacity.value)
            succeeded = ctypes.windll.kernel32.QueryFullProcessImageNameW(
                handle,
                0,
                buffer,
                ctypes.byref(capacity),
            )
            if not succeeded:
                raise OSError("unable to query process executable")
            return str(Path(buffer.value))
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)

    def client_geometry(self, hwnd: int) -> ClientGeometry:
        left, top, right, bottom = self._win32gui.GetClientRect(hwnd)
        screen_left, screen_top = self._win32gui.ClientToScreen(hwnd, (left, top))
        return ClientGeometry(
            int(screen_left),
            int(screen_top),
            int(right - left),
            int(bottom - top),
        )

    def owner_window(self, hwnd: int) -> int | None:
        owner = int(self._win32gui.GetWindow(hwnd, 4))
        return owner or None

    def window_dpi(self, hwnd: int) -> tuple[int, int]:
        user32 = ctypes.windll.user32
        get_dpi = getattr(user32, "GetDpiForWindow", None)
        dpi = int(get_dpi(hwnd)) if get_dpi is not None else 96
        return dpi, dpi

    def monitor_id(self, hwnd: int) -> str:
        monitor = self._win32api.MonitorFromWindow(hwnd, 2)
        info = self._win32api.GetMonitorInfo(monitor)
        return str(info.get("Device", monitor))

    def top_level_window(self, hwnd: int) -> int:
        root = int(self._win32gui.GetAncestor(hwnd, 2))
        return root or hwnd

    def is_owned_by(self, hwnd: int, owner_hwnd: int) -> bool:
        current: int | None = hwnd
        visited: set[int] = set()
        while current is not None and current not in visited:
            if current == owner_hwnd:
                return True
            visited.add(current)
            current = self.owner_window(current)
        return False


class Win32WindowCatalog:
    def __init__(self, win32: Win32WindowFacade | None = None) -> None:
        self._win32 = win32 or PyWin32WindowFacade()

    def list_windows(self) -> tuple[WindowDescriptor, ...]:
        windows: list[WindowDescriptor] = []
        for hwnd in self._win32.enumerate_top_level_windows():
            try:
                if not self._win32.is_window_visible(hwnd):
                    continue
                if self._win32.is_window_cloaked(hwnd):
                    continue
                title = self._win32.window_text(hwnd).strip()
                if not title:
                    continue
                process_id = self._win32.window_process_id(hwnd)
                executable = self._win32.process_executable(process_id)
                window_class = self._win32.window_class(hwnd)
                client = self._win32.client_geometry(hwnd)
                dpi_x, dpi_y = self._win32.window_dpi(hwnd)
                windows.append(
                    WindowDescriptor(
                        hwnd=hwnd,
                        process_id=process_id,
                        process_executable=executable,
                        title=title,
                        window_class=window_class,
                        client=client,
                        owner_hwnd=self._win32.owner_window(hwnd),
                        dpi_x=dpi_x,
                        dpi_y=dpi_y,
                        monitor_scale=dpi_x / 96.0,
                        monitor_id=self._win32.monitor_id(hwnd),
                    )
                )
            except (OSError, RuntimeError, ValueError):
                continue
        return tuple(
            sorted(
                windows,
                key=lambda item: (
                    item.process_executable.casefold(),
                    item.title.casefold(),
                    item.hwnd,
                ),
            )
        )

    def list_recordable_windows(self) -> tuple[RecordingTarget, ...]:
        return tuple(window.to_recording_target() for window in self.list_windows())


__all__ = [
    "ClientGeometry",
    "PyWin32WindowFacade",
    "Win32WindowCatalog",
    "Win32WindowFacade",
    "WindowDescriptor",
]
