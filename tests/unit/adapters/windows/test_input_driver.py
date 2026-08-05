"""Drag and scroll must work against a UIA-resolved element.

UIA-first resolution is the product's core promise and the guarded coordinate
fallback is the exception, so an action that only works with a coordinate target
is effectively unavailable.  A resolved element exposes a screen rectangle; its
midpoint is a well-defined origin for a drag and a well-defined point to scroll
over.
"""

from __future__ import annotations

from typing import Any

import pytest

from universal_rpa.adapters.windows.foreground import WindowIdentity
from universal_rpa.adapters.windows.input_driver import WindowsInputDriver
from universal_rpa.adapters.windows.target_resolver import (
    ResolvedCoordinateTarget,
    ResolvedUiaTarget,
)
from universal_rpa.domain.errors import ErrorCode, RpaError

IDENTITY = WindowIdentity(
    process_id=100,
    process_executable="harness.exe",
    top_level_hwnd=900,
    window_class="HarnessWindow",
)


class _Rectangle:
    def __init__(self, left: int, top: int, right: int, bottom: int) -> None:
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom


class _Element:
    """A UIA element shaped like the ones pywinauto returns."""

    def __init__(
        self,
        rectangle: _Rectangle | None = None,
        *,
        raises: bool = False,
        focus_raises: bool = False,
        journal: list[str] | None = None,
    ) -> None:
        self._rectangle = rectangle or _Rectangle(100, 200, 300, 400)
        self._raises = raises
        self._focus_raises = focus_raises
        self.journal = journal if journal is not None else []

    def rectangle(self) -> _Rectangle:
        if self._raises:
            raise RuntimeError("the element went away")
        return self._rectangle

    def set_focus(self) -> None:
        if self._focus_raises:
            raise RuntimeError("the element cannot take focus")
        self.journal.append("focus")


class _PermissiveGuard:
    """Records that the driver rechecked, without needing a live desktop."""

    def __init__(self) -> None:
        self.verify_calls = 0

    def verify(self, identity: WindowIdentity) -> None:
        del identity
        self.verify_calls += 1


class _SpyMouse:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def press(self, **kwargs: Any) -> None:
        self.calls.append(("press", kwargs))

    def move(self, **kwargs: Any) -> None:
        self.calls.append(("move", kwargs))

    def release(self, **kwargs: Any) -> None:
        self.calls.append(("release", kwargs))

    def scroll(self, **kwargs: Any) -> None:
        self.calls.append(("scroll", kwargs))


class _SpyKeyboard:
    def __init__(self) -> None:
        #: Shared with the element under test so ordering is observable.
        self.journal: list[str] = []

    def send_keys(self, keys: str, **kwargs: Any) -> None:
        del kwargs
        self.journal.append(f"send:{keys}")


@pytest.fixture
def mouse(monkeypatch: pytest.MonkeyPatch) -> _SpyMouse:
    spy = _SpyMouse()
    module = type("_PywinautoModule", (), {"mouse": spy})
    monkeypatch.setitem(__import__("sys").modules, "pywinauto", module)
    monkeypatch.setitem(__import__("sys").modules, "pywinauto.mouse", spy)
    return spy


@pytest.fixture
def keyboard(monkeypatch: pytest.MonkeyPatch) -> _SpyKeyboard:
    spy = _SpyKeyboard()
    module = type("_PywinautoModule", (), {"keyboard": spy})
    monkeypatch.setitem(__import__("sys").modules, "pywinauto", module)
    monkeypatch.setitem(__import__("sys").modules, "pywinauto.keyboard", spy)
    return spy


def _uia_target(element: _Element | None = None) -> ResolvedUiaTarget:
    return ResolvedUiaTarget(window=IDENTITY, element=element or _Element())


def _coordinate_target() -> ResolvedCoordinateTarget:
    return ResolvedCoordinateTarget(window=IDENTITY, client_point=(10, 20), screen_point=(150, 300))


def test_drag_starts_from_the_centre_of_a_resolved_element(mouse: _SpyMouse) -> None:
    driver = WindowsInputDriver(_PermissiveGuard())  # type: ignore[arg-type]

    driver.drag(_uia_target(), (500, 600))

    names = [name for name, _ in mouse.calls]
    assert names == ["press", "move", "release"]
    assert mouse.calls[0][1]["coords"] == (200, 300)
    assert mouse.calls[2][1]["coords"] == (500, 600)


def test_drag_still_uses_the_recorded_point_for_a_coordinate_target(
    mouse: _SpyMouse,
) -> None:
    driver = WindowsInputDriver(_PermissiveGuard())  # type: ignore[arg-type]

    driver.drag(_coordinate_target(), (500, 600))

    assert mouse.calls[0][1]["coords"] == (150, 300)


def test_drag_rechecks_the_foreground_between_press_move_and_release(
    mouse: _SpyMouse,
) -> None:
    guard = _PermissiveGuard()

    WindowsInputDriver(guard).drag(_uia_target(), (500, 600))  # type: ignore[arg-type]

    assert guard.verify_calls >= 3


def test_drag_fails_closed_when_the_element_has_no_usable_rectangle(
    mouse: _SpyMouse,
) -> None:
    driver = WindowsInputDriver(_PermissiveGuard())  # type: ignore[arg-type]

    with pytest.raises(RpaError) as error:
        driver.drag(_uia_target(_Element(raises=True)), (500, 600))

    assert error.value.code is ErrorCode.ACTION_FAILED
    assert mouse.calls == []


def test_drag_fails_closed_on_a_degenerate_rectangle(mouse: _SpyMouse) -> None:
    driver = WindowsInputDriver(_PermissiveGuard())  # type: ignore[arg-type]

    with pytest.raises(RpaError) as error:
        driver.drag(_uia_target(_Element(_Rectangle(10, 10, 10, 10))), (500, 600))

    assert error.value.code is ErrorCode.ACTION_FAILED
    assert mouse.calls == []


def test_scroll_happens_over_the_resolved_element(mouse: _SpyMouse) -> None:
    driver = WindowsInputDriver(_PermissiveGuard())  # type: ignore[arg-type]

    driver.scroll(_uia_target(), 0, -3)

    assert mouse.calls[0][0] == "scroll"
    assert mouse.calls[0][1]["coords"] == (200, 300)
    assert mouse.calls[0][1]["wheel_dist"] == -3


def test_horizontal_scroll_is_a_shifted_wheel_over_the_same_point(
    mouse: _SpyMouse,
) -> None:
    driver = WindowsInputDriver(_PermissiveGuard())  # type: ignore[arg-type]

    driver.scroll(_uia_target(), 2, -3)

    assert [name for name, _ in mouse.calls] == ["scroll", "scroll"]
    assert mouse.calls[1][1]["coords"] == (200, 300)
    assert mouse.calls[1][1]["pressed"] == "shift"


def test_scroll_fails_closed_rather_than_scrolling_an_unknown_point(
    mouse: _SpyMouse,
) -> None:
    """Scrolling wherever the pointer happens to be is worse than not scrolling."""

    driver = WindowsInputDriver(_PermissiveGuard())  # type: ignore[arg-type]

    with pytest.raises(RpaError) as error:
        driver.scroll(_uia_target(_Element(raises=True)), 0, -3)

    assert error.value.code is ErrorCode.ACTION_FAILED
    assert mouse.calls == []


def test_press_key_focuses_the_addressed_element_before_sending(
    keyboard: _SpyKeyboard,
) -> None:
    """send_keys goes wherever focus already is, so a key aimed at one control
    otherwise lands in whatever the window happened to focus."""

    driver = WindowsInputDriver(_PermissiveGuard())  # type: ignore[arg-type]
    element = _Element(journal=keyboard.journal)

    driver.press_key(_uia_target(element), "enter")

    assert keyboard.journal == ["focus", "send:{ENTER}"]


def test_hotkey_focuses_the_addressed_element_before_sending(
    keyboard: _SpyKeyboard,
) -> None:
    """Observed live: Ctrl+A aimed at the date field selected the *normal text*
    field instead, because the keys were sent globally to whatever had focus."""

    driver = WindowsInputDriver(_PermissiveGuard())  # type: ignore[arg-type]
    element = _Element(journal=keyboard.journal)

    driver.press_key(_uia_target(element), "a", modifiers=("ctrl",))

    assert keyboard.journal == ["focus", "send:^a"]


def test_press_key_refuses_rather_than_typing_into_an_unknown_control(
    keyboard: _SpyKeyboard,
) -> None:
    """If the addressed element cannot take focus we cannot honour the target,
    and typing anyway could enter text into whatever else is focused."""

    driver = WindowsInputDriver(_PermissiveGuard())  # type: ignore[arg-type]

    with pytest.raises(RpaError) as error:
        driver.press_key(_uia_target(_Element(focus_raises=True)), "a", modifiers=("ctrl",))

    assert error.value.code is ErrorCode.ACTION_FAILED
    assert keyboard.journal == []


def test_press_key_on_a_coordinate_target_sends_without_an_element_to_focus(
    keyboard: _SpyKeyboard,
) -> None:
    """A guarded coordinate fallback has no element; it must still work."""

    driver = WindowsInputDriver(_PermissiveGuard())  # type: ignore[arg-type]

    driver.press_key(_coordinate_target(), "enter")

    assert keyboard.journal == ["send:{ENTER}"]
