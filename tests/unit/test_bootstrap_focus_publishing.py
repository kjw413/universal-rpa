"""(B) from the M6 plan: the focus poller must publish the focused element's
UIA runtime id, or capture_context never has one to resolve against and
every keyboard event stays masked -- even once the UIA facade itself works.
"""

from __future__ import annotations

import time

import pytest

from universal_rpa.adapters.windows.context import UiaFocusCache
from universal_rpa.adapters.windows.uia_facade import PywinautoUiaFacade
from universal_rpa.bootstrap import _FocusPollingCapture
from universal_rpa.domain.recording import EventFocusSnapshot


class _FakeDelegate:
    def start(self, event_sink: object, control_sink: object) -> None:
        del event_sink, control_sink

    def stop(self) -> None:
        pass


class _FakeWin32:
    def __init__(self, *, process_id: int = 4242) -> None:
        self._process_id = process_id

    def window_process_id(self, hwnd: int) -> int:
        del hwnd
        return self._process_id


def _initial_snapshot() -> EventFocusSnapshot:
    return EventFocusSnapshot(
        foreground_hwnd=1,
        focused_hwnd=None,
        foreground_process_id=1,
        cached_uia_runtime_id=None,
        focus_event_time_ms=0,
        cache_generation=0,
        cache_confirmed=False,
    )


def _capture(**kwargs: object) -> tuple[_FocusPollingCapture, UiaFocusCache]:
    cache = UiaFocusCache(_initial_snapshot())
    capture = _FocusPollingCapture(_FakeDelegate(), cache, _FakeWin32(), **kwargs)  # type: ignore[arg-type]
    return capture, cache


def test_focus_snapshot_carries_the_focused_element_runtime_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "universal_rpa.bootstrap.ctypes.windll.user32.GetForegroundWindow",
        lambda: 777,
    )
    capture, cache = _capture(focused_runtime_id=lambda: (9, 9, 9))

    capture._publish_focus()

    assert cache.snapshot().cached_uia_runtime_id == (9, 9, 9)


def test_a_uia_failure_publishes_a_snapshot_without_a_runtime_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "universal_rpa.bootstrap.ctypes.windll.user32.GetForegroundWindow",
        lambda: 777,
    )

    def _raise() -> tuple[int, ...] | None:
        raise RuntimeError("uia unavailable")

    capture, cache = _capture(focused_runtime_id=_raise)

    capture._publish_focus()

    published = cache.snapshot()
    assert published.cached_uia_runtime_id is None
    # The rest of the focus snapshot must still make it through: a UIA hiccup
    # must not also blind the recorder to which window/process is foreground.
    assert published.foreground_hwnd == 777
    assert published.foreground_process_id == 4242


def test_focus_polling_never_blocks_on_uia_longer_than_its_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "universal_rpa.bootstrap.ctypes.windll.user32.GetForegroundWindow",
        lambda: 777,
    )

    class _HangingSource:
        def focused_element(self) -> object | None:
            time.sleep(0.3)
            return None

        def element_from_point(self, screen_x: int, screen_y: int) -> object | None:
            del screen_x, screen_y
            return None

        def parent(self, element: object) -> object | None:
            del element
            return None

        def root_from_hwnd(self, top_level_hwnd: int) -> object | None:
            del top_level_hwnd
            return None

        def descendants(self, root: object) -> tuple[object, ...]:
            del root
            return ()

    facade = PywinautoUiaFacade(source=_HangingSource(), resolution_budget_seconds=0.05)
    try:
        capture, cache = _capture(focused_runtime_id=facade.focused_runtime_id)

        started = time.monotonic()
        capture._publish_focus()
        elapsed = time.monotonic() - started

        assert elapsed < 0.25
        assert cache.snapshot().cached_uia_runtime_id is None
    finally:
        facade.close()


def test_no_runtime_id_source_publishes_none_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "universal_rpa.bootstrap.ctypes.windll.user32.GetForegroundWindow",
        lambda: 777,
    )
    capture, cache = _capture()

    capture._publish_focus()

    assert cache.snapshot().cached_uia_runtime_id is None
