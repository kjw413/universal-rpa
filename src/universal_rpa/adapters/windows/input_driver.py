"""Native input boundary that rechecks foreground immediately before use."""

from __future__ import annotations

import time
from importlib import import_module
from typing import Any, cast

from universal_rpa.domain.errors import ErrorCode, RpaError

from .foreground import ForegroundGuard, WindowIdentity
from .target_resolver import ResolvedCoordinateTarget, ResolvedTarget, ResolvedUiaTarget

#: How long to wait between the two clicks of a double click.
#:
#: Windows only pairs two clicks that are separated in time but fall inside its
#: double-click interval, and pywinauto can produce neither end of that window:
#: ``double_click_input`` injects both cycles in the same instant, while two
#: ``click_input`` calls block until the whole interval has elapsed. Measured
#: against the harness, 0 ms never registers and 30 ms always does, so this
#: keeps margin above the floor while staying far below the ~500 ms ceiling.
DOUBLE_CLICK_GAP_SECONDS = 0.06


class WindowsInputDriver:
    def __init__(self, guard: ForegroundGuard) -> None:
        self._guard = guard

    @staticmethod
    def _identity(target: ResolvedTarget) -> WindowIdentity:
        return target.window

    def verify_target(self, target: ResolvedTarget) -> None:
        self._guard.verify(self._identity(target))

    def activate(self, target: ResolvedTarget) -> None:
        self._guard.verify(self._identity(target))
        try:
            from pywinauto import Desktop  # type: ignore[import-untyped]

            Desktop(backend="uia").window(handle=target.window.top_level_hwnd).set_focus()
        except Exception:
            raise RpaError(ErrorCode.ACTION_FAILED, "대상 창을 활성화할 수 없습니다.") from None

    def click(self, target: ResolvedTarget, button: str = "left", *, double: bool = False) -> None:
        self._guard.verify(self._identity(target))
        if double:
            self._double_click(target, button)
            return
        try:
            if isinstance(target, ResolvedUiaTarget):
                cast(Any, target.element).click_input(button=button)
                return
            from pywinauto import mouse

            mouse.click(button=button, coords=target.screen_point)
        except Exception:
            raise RpaError(ErrorCode.ACTION_FAILED, "마우스 입력을 수행할 수 없습니다.") from None

    def _double_click(self, target: ResolvedTarget, button: str) -> None:
        """Two clicks separated by an interval Windows recognises as a pair.

        Injected through Win32 rather than pywinauto because pywinauto offers
        only the two failing extremes -- both cycles in one instant, or a
        forced wait for the entire double-click interval -- and neither is
        seen as a double click. Single clicks keep using pywinauto, which
        delivers them reliably.
        """

        point = self._pointer_target(target)
        if point is None:
            raise RpaError(
                ErrorCode.ACTION_FAILED, "더블클릭할 대상의 좌표를 확인할 수 없습니다."
            )
        try:
            import win32api  # type: ignore[import-untyped]
            import win32con  # type: ignore[import-untyped]

            buttons = {
                "left": (win32con.MOUSEEVENTF_LEFTDOWN, win32con.MOUSEEVENTF_LEFTUP),
                "right": (win32con.MOUSEEVENTF_RIGHTDOWN, win32con.MOUSEEVENTF_RIGHTUP),
                "middle": (win32con.MOUSEEVENTF_MIDDLEDOWN, win32con.MOUSEEVENTF_MIDDLEUP),
            }
            pressed = buttons.get(button.casefold())
            if pressed is None:
                raise RpaError(ErrorCode.INVALID_SCHEMA, "지원하지 않는 마우스 버튼입니다.")
            down, up = pressed
            win32api.SetCursorPos(point)
            for index in range(2):
                if index:
                    time.sleep(DOUBLE_CLICK_GAP_SECONDS)
                    # The pair spans real time, so the window can change under
                    # it exactly as it can during a drag.
                    self._guard.verify(self._identity(target))
                win32api.mouse_event(down, 0, 0, 0, 0)
                win32api.mouse_event(up, 0, 0, 0, 0)
        except RpaError:
            raise
        except Exception:
            raise RpaError(ErrorCode.ACTION_FAILED, "마우스 입력을 수행할 수 없습니다.") from None

    @staticmethod
    def _element_centre(element: object) -> tuple[int, int] | None:
        """The midpoint of a UIA element's screen rectangle, when it has one."""

        try:
            rectangle = cast(Any, element).rectangle()
            left = int(rectangle.left)
            top = int(rectangle.top)
            right = int(rectangle.right)
            bottom = int(rectangle.bottom)
        except Exception:
            return None
        if right <= left or bottom <= top:
            return None
        return (left + right) // 2, (top + bottom) // 2

    @classmethod
    def _pointer_target(cls, target: ResolvedTarget) -> tuple[int, int] | None:
        """Where the pointer must be for a drag or a scroll to mean anything.

        A coordinate target already carries its recorded point.  A UIA target
        carries an element, whose rectangle midpoint is the equivalent -- without
        this, drag and scroll would only ever work through the guarded coordinate
        fallback, which is the exception rather than the normal path.
        """

        if isinstance(target, ResolvedCoordinateTarget):
            return target.screen_point
        if isinstance(target, ResolvedUiaTarget):
            return cls._element_centre(target.element)
        return None

    def drag(self, target: ResolvedTarget, end: tuple[int, int], button: str = "left") -> None:
        self._guard.verify(self._identity(target))
        start = self._pointer_target(target)
        if start is None:
            raise RpaError(ErrorCode.ACTION_FAILED, "UIA 대상의 드래그 좌표를 확인할 수 없습니다.")
        try:
            from pywinauto import mouse

            mouse.press(button=button, coords=start)
            self._guard.verify(self._identity(target))
            mouse.move(coords=end)
            self._guard.verify(self._identity(target))
            mouse.release(button=button, coords=end)
        except RpaError:
            raise
        except Exception:
            raise RpaError(ErrorCode.ACTION_FAILED, "드래그 입력을 수행할 수 없습니다.") from None

    def scroll(self, target: ResolvedTarget, horizontal: int, vertical: int) -> None:
        self._guard.verify(self._identity(target))
        point = self._pointer_target(target)
        if point is None:
            # Scrolling wherever the pointer happens to be would move whatever is
            # under it, which is worse than refusing the step.
            raise RpaError(ErrorCode.ACTION_FAILED, "UIA 대상의 스크롤 좌표를 확인할 수 없습니다.")
        try:
            from pywinauto import mouse

            mouse.scroll(coords=point, wheel_dist=vertical)
            if horizontal:
                mouse.scroll(coords=point, wheel_dist=horizontal, pressed="shift")
        except Exception:
            raise RpaError(ErrorCode.ACTION_FAILED, "스크롤 입력을 수행할 수 없습니다.") from None

    @staticmethod
    def _focus_element(target: ResolvedTarget) -> None:
        """Put focus on the addressed element before any keystroke.

        ``send_keys`` delivers to whatever already holds focus, so without this
        a key aimed at one control lands wherever the window happened to focus
        -- ``activate`` only raises the top-level window, not the element. This
        is the keyboard counterpart of the drag/scroll defect: an action that
        names a target must act on that target.
        """

        if not isinstance(target, ResolvedUiaTarget):
            return
        try:
            cast(Any, target.element).set_focus()
        except Exception:
            # Typing anyway would enter the keys into an unknown control, which
            # is the more damaging of the two failures.
            raise RpaError(
                ErrorCode.ACTION_FAILED, "키를 보낼 대상에 포커스를 줄 수 없습니다."
            ) from None

    def press_key(
        self, target: ResolvedTarget, key: str, *, modifiers: tuple[str, ...] = ()
    ) -> None:
        self._guard.verify(self._identity(target))
        self._focus_element(target)
        try:
            from pywinauto import keyboard

            prefixes = {"ctrl": "^", "shift": "+", "alt": "%", "win": "#"}
            if any(modifier not in prefixes for modifier in modifiers):
                raise RpaError(ErrorCode.INVALID_SCHEMA, "지원하지 않는 보조 키입니다.")
            prefix = "".join(prefixes[modifier] for modifier in modifiers)
            key_aliases = {
                "page_up": "PGUP",
                "page_down": "PGDN",
                "escape": "ESC",
                "delete": "DELETE",
                "backspace": "BACKSPACE",
                "space": "SPACE",
            }
            normalized = key_aliases.get(key.casefold(), key.upper())
            mapped = f"{{{normalized}}}" if len(key) > 1 else key
            keyboard.send_keys(prefix + mapped, with_spaces=True, pause=0)
        except RpaError:
            raise
        except Exception:
            raise RpaError(ErrorCode.ACTION_FAILED, "키보드 입력을 수행할 수 없습니다.") from None

    def direct_text(self, target: ResolvedUiaTarget, value: str) -> None:
        """Type through a control only after a last-moment foreground guard."""

        self._guard.verify(self._identity(target))
        try:
            cast(Any, target.element).type_keys(value, with_spaces=True, pause=0)
        except Exception:
            raise RpaError(ErrorCode.ACTION_FAILED, "직접 키 입력을 수행할 수 없습니다.") from None

    def paste_text(self, target: ResolvedTarget, value: str) -> None:
        self._guard.verify(self._identity(target))
        clipboard = cast(Any, import_module("win32clipboard"))
        previous: str | None = None
        changed = False
        failure: RpaError | None = None
        try:
            clipboard.OpenClipboard()
            try:
                if clipboard.IsClipboardFormatAvailable(13):
                    previous = str(clipboard.GetClipboardData(13))
                clipboard.EmptyClipboard()
                clipboard.SetClipboardText(value)
                changed = True
            finally:
                clipboard.CloseClipboard()
            self.press_key(target, "v", modifiers=("ctrl",))
        except RpaError as error:
            failure = error
        except Exception:
            failure = RpaError(
                ErrorCode.ACTION_FAILED, "안전한 붙여넣기 입력을 수행할 수 없습니다."
            )
        finally:
            if changed:
                try:
                    clipboard.OpenClipboard()
                    try:
                        clipboard.EmptyClipboard()
                        if previous is not None:
                            clipboard.SetClipboardText(previous)
                    finally:
                        clipboard.CloseClipboard()
                except Exception:
                    if failure is None:
                        failure = RpaError(
                            ErrorCode.ACTION_FAILED, "기존 클립보드 내용을 복원할 수 없습니다."
                        )
        if failure is not None:
            raise failure


__all__ = ["WindowsInputDriver"]
