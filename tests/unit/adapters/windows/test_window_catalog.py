from __future__ import annotations

from universal_rpa.adapters.windows.window_catalog import (
    ClientGeometry,
    Win32WindowCatalog,
)


class FakeWin32:
    def __init__(self) -> None:
        self.visible = {10: True, 20: False, 30: True, 40: True, 50: True}
        self.cloaked = {10: False, 20: False, 30: True, 40: False, 50: False}
        self.titles = {10: "Zulu", 20: "Hidden", 30: "Cloaked", 40: "", 50: "Alpha"}

    def enumerate_top_level_windows(self) -> tuple[int, ...]:
        return (10, 20, 30, 40, 50)

    def is_window_visible(self, hwnd: int) -> bool:
        return self.visible[hwnd]

    def is_window_cloaked(self, hwnd: int) -> bool:
        return self.cloaked[hwnd]

    def window_text(self, hwnd: int) -> str:
        return self.titles[hwnd]

    def window_class(self, hwnd: int) -> str:
        return "MisWindow"

    def window_process_id(self, hwnd: int) -> int:
        return hwnd + 100

    def process_executable(self, process_id: int) -> str:
        return "a.exe" if process_id == 150 else "z.exe"

    def client_geometry(self, hwnd: int) -> ClientGeometry:
        return ClientGeometry(hwnd, hwnd, 800, 600)

    def owner_window(self, hwnd: int) -> int | None:
        return None

    def window_dpi(self, hwnd: int) -> tuple[int, int]:
        return (144, 144)

    def monitor_id(self, hwnd: int) -> str:
        return "DISPLAY1"

    def top_level_window(self, hwnd: int) -> int:
        return hwnd

    def is_owned_by(self, hwnd: int, owner_hwnd: int) -> bool:
        return hwnd == owner_hwnd


def test_catalog_filters_nonrecordable_windows_and_sorts_by_app_title() -> None:
    catalog = Win32WindowCatalog(FakeWin32())
    windows = catalog.list_windows()

    assert [window.hwnd for window in windows] == [50, 10]
    assert windows[0].client == ClientGeometry(50, 50, 800, 600)
    assert windows[0].monitor_scale == 1.5


def test_catalog_projects_descriptors_to_recording_targets() -> None:
    targets = Win32WindowCatalog(FakeWin32()).list_recordable_windows()
    assert targets[0].top_level_hwnd == 50
    assert targets[0].window_title == "Alpha"
