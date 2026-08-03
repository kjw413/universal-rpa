from __future__ import annotations

from collections.abc import Mapping

import pytest

from universal_rpa.domain.action_parameters import validate_builtin_action_parameters
from universal_rpa.domain.types import FrozenMapping, JsonValue
from universal_rpa.ui.action_parameter_editor import ActionParameterEditor


@pytest.mark.parametrize(
    ("action_type", "draft"),
    [
        ("windows.click", {"button": "right"}),
        ("windows.double_click", {"button": "left"}),
        ("windows.drag", {"button": "left", "end_point": {"x": 0.8, "y": 0.4}}),
        ("windows.scroll", {"horizontal_delta": -120, "vertical_delta": 240}),
        ("windows.press_key", {"key": "enter"}),
        ("windows.hotkey", {"key": "a", "modifiers": ["ctrl"]}),
        ("windows.activate_window", {}),
        ("windows.set_text", {}),
        ("windows.wait", {}),
    ],
)
def test_editor_emits_only_canonical_m1_parameters(
    qtbot: object,
    action_type: str,
    draft: Mapping[str, JsonValue],
) -> None:
    editor = ActionParameterEditor()
    qtbot.addWidget(editor)  # type: ignore[attr-defined]
    editor.set_action(action_type, FrozenMapping.empty())
    editor.set_draft(draft)

    assert editor.pending_parameters() == validate_builtin_action_parameters(action_type, draft)


def test_zero_scroll_is_incomplete_draft(qtbot: object) -> None:
    editor = ActionParameterEditor()
    qtbot.addWidget(editor)  # type: ignore[attr-defined]
    editor.set_action("windows.scroll", FrozenMapping.empty())
    editor.set_draft({"horizontal_delta": 0, "vertical_delta": 0})

    assert editor.pending_parameters() is None
    assert editor.error_text
