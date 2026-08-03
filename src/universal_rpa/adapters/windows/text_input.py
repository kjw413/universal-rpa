"""Ordered text input strategies for UIA and legacy Windows controls."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, cast

from universal_rpa.domain.errors import ErrorCode, RpaError
from universal_rpa.domain.types import FrozenJsonObject, FrozenMapping
from universal_rpa.ports.credentials import SecretValue

from .input_driver import WindowsInputDriver
from .target_resolver import ResolvedTarget, ResolvedUiaTarget


@dataclass(frozen=True, slots=True)
class TextInputResult:
    strategy: Literal["uia_value", "set_edit_text", "paste", "direct_keys"]
    verified: bool
    evidence: FrozenJsonObject


class TextInputStrategy:
    def __init__(self, driver: WindowsInputDriver) -> None:
        self._driver = driver

    def set_text(
        self,
        target: ResolvedTarget,
        value: str | SecretValue,
        *,
        verify: bool,
    ) -> TextInputResult:
        if not isinstance(target, ResolvedUiaTarget):
            raise RpaError(
                ErrorCode.ACTION_FAILED, "텍스트 입력에 사용할 UI 요소를 찾을 수 없습니다."
            )
        with value.reveal() if isinstance(value, SecretValue) else _text(value) as text:
            element = cast(Any, target.element)
            strategies: tuple[
                tuple[
                    Literal["uia_value", "set_edit_text", "paste", "direct_keys"],
                    Callable[[], object],
                ],
                ...,
            ] = (
                ("uia_value", lambda: element.iface_value.SetValue(text)),
                ("set_edit_text", lambda: element.set_edit_text(text)),
                ("paste", lambda: self._driver.paste_text(target, text)),
                ("direct_keys", lambda: self._driver.direct_text(target, text)),
            )
            for strategy, operation in strategies:
                try:
                    self._driver.verify_target(target)
                    operation()
                    verified = self._verify(target.element, text) if verify else False
                    if verify and not verified:
                        continue
                    return TextInputResult(strategy, verified, FrozenMapping.empty())
                except RpaError:
                    continue
                except Exception:
                    continue
        raise RpaError(ErrorCode.ACTION_FAILED, "텍스트 입력 후 값을 확인할 수 없습니다.")

    @staticmethod
    def _verify(element: object, expected: str) -> bool:
        dynamic = cast(Any, element)
        getters: tuple[Callable[[], object], ...] = (
            lambda: dynamic.iface_value.CurrentValue,
            lambda: dynamic.get_value(),
            lambda: dynamic.window_text(),
        )
        for getter in getters:
            try:
                return str(getter()) == expected
            except Exception:
                continue
        return False


class _text:
    def __init__(self, value: str) -> None:
        self._value = value

    def __enter__(self) -> str:
        return self._value

    def __exit__(self, *args: object) -> None:
        return None


__all__ = ["TextInputResult", "TextInputStrategy"]
