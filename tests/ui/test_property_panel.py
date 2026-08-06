from __future__ import annotations

from uuid import UUID

from tests.ui.test_wait_assertion_editor import descriptor, target
from universal_rpa.application.editing import PatchActionStep, SetStepValue
from universal_rpa.domain.conditions import WaitSpec
from universal_rpa.domain.values import LiteralValue, SecretRefValue
from universal_rpa.domain.workflow import ActionStep
from universal_rpa.ui.property_panel import PropertyPanel

STEP_ID = UUID("00000000-0000-0000-0000-000000000881")


def clickable_step() -> ActionStep:
    return ActionStep(
        step_id=STEP_ID,
        label="조회 버튼 클릭",
        action_type="windows.click",
        target=target(),
    )


def test_a_wait_chosen_in_the_panel_reaches_the_step(qtbot: object) -> None:
    """Otherwise the wait editor is decoration: the step is saved without it."""

    panel = PropertyPanel()
    qtbot.addWidget(panel)  # type: ignore[attr-defined]
    panel.set_adapter_descriptors({"windows": descriptor()})
    panel.set_step(clickable_step())

    panel.wait_editor.condition_combo.setCurrentText("windows.element_exists")
    panel.wait_editor.timeout_ms.setValue(5_000)
    command = panel.pending_command()

    assert isinstance(command, PatchActionStep)
    wait = command.changes["wait"]
    assert isinstance(wait, WaitSpec)
    assert wait.condition.condition_type == "windows.element_exists"
    assert wait.timeout_ms == 5_000
    assert wait.condition.target == target()


def test_an_untouched_wait_editor_patches_nothing(qtbot: object) -> None:
    panel = PropertyPanel()
    qtbot.addWidget(panel)  # type: ignore[attr-defined]
    panel.set_adapter_descriptors({"windows": descriptor()})
    panel.set_step(clickable_step())

    assert panel.pending_command() is None


def test_secret_mode_stays_draft_until_reference_is_selected(qtbot: object) -> None:
    panel = PropertyPanel()
    qtbot.addWidget(panel)  # type: ignore[attr-defined]
    panel.set_step(
        ActionStep(
            step_id=STEP_ID,
            label="비밀번호 입력",
            action_type="windows.set_text",
            value=LiteralValue(value="sensitive"),
        )
    )

    panel.mode_combo.setCurrentText("비밀값")

    assert panel.pending_command() is None
    assert "sensitive" not in panel.value_input.text()

    panel.select_credential_reference("mis/query-password")
    command = panel.pending_command()

    assert isinstance(command, SetStepValue)
    assert command.value == SecretRefValue(credential_ref="mis/query-password")
    assert "sensitive" not in repr(command)
