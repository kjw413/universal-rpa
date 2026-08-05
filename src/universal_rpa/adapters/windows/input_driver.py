"""Native input boundary that rechecks foreground immediately before use."""

from __future__ import annotations

from importlib import import_module
from typing import Any, cast

from universal_rpa.domain.errors import ErrorCode, RpaError

from .foreground import ForegroundGuard, WindowIdentity
from .target_resolver import ResolvedCoordinateTarget, ResolvedTarget, ResolvedUiaTarget


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
        try:
            if isinstance(target, ResolvedUiaTarget):
                element = cast(Any, target.element)
                if double:
                    element.double_click_input(button=button)
                else:
                    element.click_input(button=button)
                return
            from pywinauto import mouse

            if double:
                mouse.double_click(button=button, coords=target.screen_point)
            else:
                mouse.click(button=button, coords=target.screen_point)
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

    def press_key(
        self, target: ResolvedTarget, key: str, *, modifiers: tuple[str, ...] = ()
    ) -> None:
        self._guard.verify(self._identity(target))
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
