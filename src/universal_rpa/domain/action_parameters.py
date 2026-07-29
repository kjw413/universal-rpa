from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from universal_rpa.domain.targets import RelativePoint
from universal_rpa.domain.types import (
    FrozenJsonObject,
    FrozenMapping,
    JsonValue,
    deep_freeze_json,
)


class NoParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MouseButtonParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    button: Literal["left", "right", "middle"] = "left"


class DragParameters(MouseButtonParameters):
    end_point: RelativePoint


class ScrollParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    horizontal_delta: int = Field(ge=-120_000, le=120_000)
    vertical_delta: int = Field(ge=-120_000, le=120_000)

    @model_validator(mode="after")
    def has_nonzero_delta(self) -> ScrollParameters:
        if self.horizontal_delta == 0 and self.vertical_delta == 0:
            raise ValueError("scroll requires at least one nonzero delta")
        return self


WindowsKey = Literal[
    "a",
    "b",
    "c",
    "d",
    "e",
    "f",
    "g",
    "h",
    "i",
    "j",
    "k",
    "l",
    "m",
    "n",
    "o",
    "p",
    "q",
    "r",
    "s",
    "t",
    "u",
    "v",
    "w",
    "x",
    "y",
    "z",
    "0",
    "1",
    "2",
    "3",
    "4",
    "5",
    "6",
    "7",
    "8",
    "9",
    "enter",
    "tab",
    "esc",
    "space",
    "backspace",
    "delete",
    "insert",
    "home",
    "end",
    "page_up",
    "page_down",
    "left",
    "right",
    "up",
    "down",
    "f1",
    "f2",
    "f3",
    "f4",
    "f5",
    "f6",
    "f7",
    "f8",
    "f9",
    "f10",
    "f11",
    "f12",
    "f13",
    "f14",
    "f15",
    "f16",
    "f17",
    "f18",
    "f19",
    "f20",
    "f21",
    "f22",
    "f23",
    "f24",
]


class PressKeyParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: WindowsKey


ModifierKey = Literal["ctrl", "alt", "shift", "win"]
_MODIFIER_ORDER: tuple[ModifierKey, ...] = ("ctrl", "alt", "shift", "win")


class HotkeyParameters(PressKeyParameters):
    modifiers: tuple[ModifierKey, ...] = Field(min_length=1)

    @field_validator("modifiers", mode="after")
    @classmethod
    def modifiers_are_a_canonical_subset(
        cls, modifiers: tuple[ModifierKey, ...]
    ) -> tuple[ModifierKey, ...]:
        canonical = tuple(modifier for modifier in _MODIFIER_ORDER if modifier in modifiers)
        if modifiers != canonical:
            raise ValueError("hotkey modifiers must be unique and in canonical order")
        return modifiers

    @model_validator(mode="after")
    def is_not_a_recorder_control_chord(self) -> HotkeyParameters:
        if self.key in {"f11", "f12"} and {"ctrl", "shift"} <= set(self.modifiers):
            raise ValueError("recorder control hotkeys are reserved")
        return self


BUILTIN_ACTION_PARAMETER_MODELS: FrozenMapping[str, type[BaseModel]] = FrozenMapping.from_mapping(
    {
        "windows.click": MouseButtonParameters,
        "windows.double_click": MouseButtonParameters,
        "windows.drag": DragParameters,
        "windows.scroll": ScrollParameters,
        "windows.press_key": PressKeyParameters,
        "windows.hotkey": HotkeyParameters,
        "windows.activate_window": NoParameters,
        "windows.set_text": NoParameters,
        "windows.wait": NoParameters,
    }
)


def validate_builtin_action_parameters(
    action_type: str,
    parameters: Mapping[str, JsonValue] | FrozenJsonObject,
) -> FrozenJsonObject:
    try:
        model = BUILTIN_ACTION_PARAMETER_MODELS[action_type]
    except KeyError as error:
        raise ValueError(f"unsupported built-in action type: {action_type}") from error

    validated = model.model_validate(dict(parameters))
    frozen = deep_freeze_json(validated.model_dump(mode="json"))
    if not isinstance(frozen, FrozenMapping):
        raise TypeError("validated action parameters must be a JSON object")
    return frozen


__all__ = [
    "BUILTIN_ACTION_PARAMETER_MODELS",
    "DragParameters",
    "HotkeyParameters",
    "ModifierKey",
    "MouseButtonParameters",
    "NoParameters",
    "PressKeyParameters",
    "ScrollParameters",
    "WindowsKey",
    "validate_builtin_action_parameters",
]
