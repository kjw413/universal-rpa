"""Translates a pywinauto UIAElementInfo (or any COM element wearing the same
shape) into the attributes ``capture_target_snapshot`` reads.

pywinauto's ``UIAElementInfo`` does not expose bounds, ``is_password``, or a
value getter in that shape, so this view performs the translation. Any UIA
read that fails resolves to a safe, fail-closed default rather than raising --
an element that can no longer answer whether it is a password field must be
treated as one.
"""

from __future__ import annotations

from typing import Any, cast


class UiaElementView:
    def __init__(self, info: object) -> None:
        self._info = info

    @property
    def automation_id(self) -> str | None:
        return self._safe_str("automation_id")

    @property
    def control_type(self) -> str:
        return self._safe_str("control_type") or "Unknown"

    @property
    def name(self) -> str | None:
        return self._safe_str("name")

    @property
    def class_name(self) -> str | None:
        return self._safe_str("class_name")

    @property
    def runtime_id(self) -> tuple[int, ...] | None:
        try:
            raw = getattr(self._info, "runtime_id", None)
        except Exception:
            return None
        if not raw:
            return None
        try:
            return tuple(int(part) for part in cast(Any, raw))
        except (TypeError, ValueError):
            return None

    @property
    def is_password(self) -> bool:
        element = self._com_element()
        if element is None:
            return True
        try:
            return bool(element.CurrentIsPassword)
        except Exception:
            return True

    def get_value(self) -> str | None:
        element = self._com_element()
        if element is None:
            return None
        try:
            # Imported here, not at module scope: importing pywinauto's UIA
            # definitions builds its COM pattern registry as a side effect, so
            # a module-level import drags the whole native stack into anything
            # that merely imports bootstrap -- which broke startup outright in
            # the packaged build, where comtypes resolves its submodules
            # dynamically.
            from pywinauto.uia_defines import get_elem_interface  # type: ignore[import-untyped]

            pattern = get_elem_interface(element, "Value")
            value = pattern.CurrentValue
        except Exception:
            return None
        return None if value is None else str(value)

    @property
    def bounding_rectangle(self) -> object | None:
        try:
            return getattr(self._info, "rectangle", None)
        except Exception:
            return None

    def _com_element(self) -> Any | None:
        try:
            return getattr(self._info, "element", None)
        except Exception:
            return None

    def _safe_str(self, name: str) -> str | None:
        try:
            value = getattr(self._info, name, None)
        except Exception:
            return None
        return str(value) if value else None


__all__ = ["UiaElementView"]
