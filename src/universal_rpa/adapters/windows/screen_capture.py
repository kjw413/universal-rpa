"""Production Win32 boundary for exact-window capture and live password bounds.

Both classes are thin adapters over native calls.  They are exercised by the
interactive harness in ``tests/integration/windows`` rather than by headless
unit tests; every failure path returns nothing so the caller fails closed.
"""

from __future__ import annotations

import ctypes
from collections.abc import Iterable
from importlib import import_module
from typing import Any, cast

from PySide6.QtGui import QImage

from universal_rpa.infrastructure.screenshots import ClientCapture

from .window_catalog import ClientGeometry, PyWin32WindowFacade, Win32WindowFacade

PW_CLIENTONLY = 1


class Win32ExactWindowCapture:
    """Captures the client area of exactly one already-identified window."""

    def __init__(self, win32: Win32WindowFacade | None = None) -> None:
        self._win32 = win32 or PyWin32WindowFacade()

    def capture_client(self, process_id: int, hwnd: int) -> ClientCapture | None:
        try:
            if hwnd <= 0 or self._win32.window_process_id(hwnd) != process_id:
                return None
            geometry = self._win32.client_geometry(hwnd)
            image = self._grab_client(hwnd, geometry)
        except Exception:
            return None
        if image is None or image.isNull():
            return None
        return ClientCapture(
            process_id=process_id,
            hwnd=hwnd,
            client_screen_x=geometry.left,
            client_screen_y=geometry.top,
            width=geometry.width,
            height=geometry.height,
            image=image,
        )

    @staticmethod
    def _grab_client(hwnd: int, geometry: ClientGeometry) -> QImage | None:
        win32gui = cast(Any, import_module("win32gui"))
        win32ui = cast(Any, import_module("win32ui"))

        window_dc = win32gui.GetWindowDC(hwnd)
        source_dc = None
        memory_dc = None
        bitmap = None
        try:
            source_dc = win32ui.CreateDCFromHandle(window_dc)
            memory_dc = source_dc.CreateCompatibleDC()
            bitmap = win32ui.CreateBitmap()
            bitmap.CreateCompatibleBitmap(source_dc, geometry.width, geometry.height)
            memory_dc.SelectObject(bitmap)
            printed = ctypes.windll.user32.PrintWindow(
                hwnd, memory_dc.GetSafeHdc(), PW_CLIENTONLY
            )
            if not printed:
                return None
            info = bitmap.GetInfo()
            if (info["bmWidth"], info["bmHeight"]) != (geometry.width, geometry.height):
                return None
            payload = bitmap.GetBitmapBits(True)
            return QImage(
                payload,
                geometry.width,
                geometry.height,
                QImage.Format.Format_RGB32,
            ).copy()
        finally:
            if bitmap is not None:
                win32gui.DeleteObject(bitmap.GetHandle())
            if memory_dc is not None:
                memory_dc.DeleteDC()
            if source_dc is not None:
                source_dc.DeleteDC()
            win32gui.ReleaseDC(hwnd, window_dc)


class UiaPasswordRegionProbe:
    """Reports the screen bounds of every live ``IsPassword`` descendant."""

    def password_screen_rects(self, hwnd: int) -> Iterable[tuple[int, int, int, int]]:
        element_info = cast(Any, import_module("pywinauto.uia_element_info"))
        root = element_info.UIAElementInfo(hwnd)
        found: list[tuple[int, int, int, int]] = []
        for descendant in root.descendants():
            try:
                if not bool(descendant.element.CurrentIsPassword):
                    continue
                rectangle = descendant.rectangle
                bounds = (
                    int(rectangle.left),
                    int(rectangle.top),
                    int(rectangle.width()),
                    int(rectangle.height()),
                )
            except Exception:
                continue
            if bounds not in found:
                found.append(bounds)
        return tuple(found)


__all__ = ["PW_CLIENTONLY", "UiaPasswordRegionProbe", "Win32ExactWindowCapture"]
