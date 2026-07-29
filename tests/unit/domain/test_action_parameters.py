from __future__ import annotations

from typing import get_type_hints

import pytest
from pydantic import BaseModel, ValidationError

from universal_rpa.domain.action_parameters import (
    BUILTIN_ACTION_PARAMETER_MODELS,
    DragParameters,
    HotkeyParameters,
    MouseButtonParameters,
    NoParameters,
    PressKeyParameters,
    ScrollParameters,
    validate_builtin_action_parameters,
)
from universal_rpa.domain.types import FrozenJsonObject, FrozenMapping


def test_builtin_registry_has_the_exact_shared_model_bindings() -> None:
    assert dict(BUILTIN_ACTION_PARAMETER_MODELS.items()) == {
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
    assert all(
        isinstance(model, type) and issubclass(model, BaseModel)
        for model in BUILTIN_ACTION_PARAMETER_MODELS.values()
    )


def test_builtin_action_parameters_have_exact_typed_fields() -> None:
    drag = validate_builtin_action_parameters(
        "windows.drag",
        {"button": "left", "end_point": {"x": 0.8, "y": 0.4}},
    )
    assert drag["button"] == "left"
    assert drag["end_point"] == FrozenMapping.from_mapping({"x": 0.8, "y": 0.4})

    hotkey = validate_builtin_action_parameters(
        "windows.hotkey", {"key": "a", "modifiers": ["ctrl"]}
    )
    assert hotkey["key"] == "a"
    assert hotkey["modifiers"] == ("ctrl",)
    assert (
        validate_builtin_action_parameters("windows.press_key", {"key": "enter"})["key"] == "enter"
    )

    with pytest.raises(ValidationError):
        validate_builtin_action_parameters(
            "windows.scroll", {"horizontal_delta": 0, "vertical_delta": 0}
        )
    with pytest.raises(ValidationError):
        validate_builtin_action_parameters(
            "windows.hotkey", {"key": "f12", "modifiers": ["ctrl", "shift"]}
        )
    with pytest.raises(ValidationError):
        validate_builtin_action_parameters("windows.click", {"button": "left", "x": 1})


@pytest.mark.parametrize(
    "key",
    ["a", "z", "0", "9", "enter", "page_down", "left", "f1", "f24"],
)
def test_press_key_accepts_every_key_family_in_the_explicit_whitelist(key: str) -> None:
    assert validate_builtin_action_parameters("windows.press_key", {"key": key})["key"] == key


@pytest.mark.parametrize("key", ["A", "ctrl", "return", "f25", "browser_back", "a-b"])
def test_press_key_rejects_names_outside_the_explicit_whitelist(key: str) -> None:
    with pytest.raises(ValidationError):
        validate_builtin_action_parameters("windows.press_key", {"key": key})


@pytest.mark.parametrize(
    "modifiers",
    [(), ("shift", "ctrl"), ("ctrl", "ctrl"), ("ctrl", "win", "shift")],
)
def test_hotkey_requires_a_nonempty_canonical_modifier_subset(
    modifiers: tuple[str, ...],
) -> None:
    with pytest.raises(ValidationError):
        HotkeyParameters(key="a", modifiers=modifiers)  # type: ignore[arg-type]


def test_validation_returns_a_precisely_typed_deep_frozen_copy() -> None:
    source = {"button": "right", "end_point": {"x": 0.25, "y": 0.75}}

    validated = validate_builtin_action_parameters("windows.drag", source)
    source["end_point"]["x"] = 1.0  # type: ignore[index]

    assert get_type_hints(validate_builtin_action_parameters)["return"] == FrozenJsonObject
    assert isinstance(validated, FrozenMapping)
    assert validated["end_point"] == FrozenMapping.from_mapping({"x": 0.25, "y": 0.75})


def test_unknown_action_type_has_no_builtin_parameter_contract() -> None:
    with pytest.raises(ValueError, match="unsupported built-in action type"):
        validate_builtin_action_parameters("custom.action", {})
