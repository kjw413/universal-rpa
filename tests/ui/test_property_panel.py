from __future__ import annotations

from uuid import UUID

from universal_rpa.application.editing import SetStepValue
from universal_rpa.domain.values import LiteralValue, SecretRefValue
from universal_rpa.domain.workflow import ActionStep
from universal_rpa.ui.property_panel import PropertyPanel

STEP_ID = UUID("00000000-0000-0000-0000-000000000881")


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
