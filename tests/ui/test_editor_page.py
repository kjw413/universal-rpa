from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from universal_rpa.application.editing import RenameStep, WorkflowEditingService
from universal_rpa.application.projects import ProjectSession
from universal_rpa.domain.targets import TargetSpec
from universal_rpa.domain.workflow import ActionStep, LoopStep, TargetAppSpec, Workflow
from universal_rpa.ui.editor_page import WorkflowEditor
from universal_rpa.ui.loop_dialog import LoopDialog
from universal_rpa.ui.variable_dialog import VariableDialog

FIRST_STEP_ID = UUID("00000000-0000-0000-0000-000000000831")
SECOND_STEP_ID = UUID("00000000-0000-0000-0000-000000000832")
NOW = datetime(2026, 7, 27, tzinfo=UTC)


def target(automation_id: str) -> TargetSpec:
    return TargetSpec.model_validate(
        {
            "adapter_id": "windows",
            "payload": {
                "selector": {"automation_id": automation_id},
                "coordinate_fallback": None,
            },
        }
    )


def project_session(tmp_path: Path) -> ProjectSession:
    workflow = Workflow(
        workflow_id=UUID("00000000-0000-0000-0000-000000000830"),
        name="편집기 테스트",
        revision=1,
        target_apps=(
            TargetAppSpec(
                app_id="erp",
                process_executable="erp.exe",
                window_class="ERPMain",
            ),
        ),
        steps=(
            ActionStep(
                step_id=FIRST_STEP_ID,
                label="첫 단계",
                action_type="windows.click",
                target=target("first"),
            ),
            ActionStep(
                step_id=SECOND_STEP_ID,
                label="둘째 단계",
                action_type="windows.press_key",
                target=target("second"),
                parameters={"key": "enter"},
            ),
        ),
        created_at=NOW,
        updated_at=NOW,
    )
    return ProjectSession(tmp_path, workflow, workflow.revision, False)


class AcceptingVariableDialog(VariableDialog):
    """A variable dialog the user has already filled in and accepted."""

    def exec(self) -> int:
        return int(QDialog.DialogCode.Accepted)


class RejectingVariableDialog(VariableDialog):
    """A variable dialog the user cancelled."""

    def exec(self) -> int:
        return int(QDialog.DialogCode.Rejected)


class AcceptingLoopDialog(LoopDialog):
    def exec(self) -> int:
        return int(QDialog.DialogCode.Accepted)


def test_adding_a_run_variable_puts_it_in_the_workflow(qtbot: object, tmp_path: Path) -> None:
    """Without this the Runner's input form can never have anything to show."""

    editor = WorkflowEditor(WorkflowEditingService())
    qtbot.addWidget(editor)  # type: ignore[attr-defined]
    editor.set_session(project_session(tmp_path))
    dialog = AcceptingVariableDialog()
    qtbot.addWidget(dialog)  # type: ignore[attr-defined]
    dialog.variable_id.setText("start_date")
    dialog.label_input.setText("시작일")
    dialog.value_type.setCurrentText("date")
    dialog.source_type.setCurrentText("run_input")
    editor.variable_dialog_factory = lambda: dialog

    editor.add_variable()

    assert editor.session is not None
    variables = editor.session.workflow.variables
    assert [variable.variable_id for variable in variables] == ["start_date"]
    assert variables[0].label == "시작일"


def test_rejected_variable_leaves_the_workflow_alone(qtbot: object, tmp_path: Path) -> None:
    editor = WorkflowEditor(WorkflowEditingService())
    qtbot.addWidget(editor)  # type: ignore[attr-defined]
    editor.set_session(project_session(tmp_path))
    dialog = RejectingVariableDialog()
    qtbot.addWidget(dialog)  # type: ignore[attr-defined]
    dialog.variable_id.setText("start_date")
    dialog.label_input.setText("시작일")
    editor.variable_dialog_factory = lambda: dialog

    editor.add_variable()

    assert editor.session is not None
    assert editor.session.workflow.variables == ()


def test_wrapping_selected_steps_in_a_loop_binds_them_to_a_data_source(
    qtbot: object,
    tmp_path: Path,
) -> None:
    """Repeating a job over a CSV needs both the source and the loop group."""

    editor = WorkflowEditor(WorkflowEditingService())
    qtbot.addWidget(editor)  # type: ignore[attr-defined]
    editor.set_session(project_session(tmp_path))
    dialog = AcceptingLoopDialog()
    qtbot.addWidget(dialog)  # type: ignore[attr-defined]
    dialog.label_input.setText("공장별 반복")
    dialog.data_source_id.setText("factories")
    dialog.source_type.setCurrentText("csv")
    dialog.path_input.setText("inputs/factories.csv")
    editor.loop_dialog_factory = lambda: dialog
    editor.tree_view.setCurrentIndex(editor.tree_model.index_for_step(FIRST_STEP_ID))

    editor.wrap_selection_in_loop()

    assert editor.session is not None
    workflow = editor.session.workflow
    assert [source.data_source_id for source in workflow.data_sources] == ["factories"]
    loop = workflow.steps[0]
    assert isinstance(loop, LoopStep)
    assert loop.data_source_id == "factories"
    assert [step.step_id for step in loop.steps] == [FIRST_STEP_ID]


def test_selection_updates_all_three_panes(qtbot: object, tmp_path: Path) -> None:
    editor = WorkflowEditor(WorkflowEditingService())
    qtbot.addWidget(editor)  # type: ignore[attr-defined]
    editor.set_session(project_session(tmp_path))
    selected = editor.tree_model.index_for_step(SECOND_STEP_ID)

    editor.tree_view.setCurrentIndex(selected)

    assert editor.target_preview.step_id == SECOND_STEP_ID
    assert editor.property_panel.step_id == SECOND_STEP_ID


def test_json_inspector_is_read_only(qtbot: object, tmp_path: Path) -> None:
    editor = WorkflowEditor(WorkflowEditingService())
    qtbot.addWidget(editor)  # type: ignore[attr-defined]
    editor.set_session(project_session(tmp_path))
    editor.show_json_inspector()
    before = editor.json_inspector.text_edit.toPlainText()

    qtbot.keyClicks(editor.json_inspector.text_edit, "malicious")  # type: ignore[attr-defined]

    assert editor.json_inspector.text_edit.isReadOnly()
    assert editor.json_inspector.text_edit.toPlainText() == before


def test_command_is_applied_once_and_emitted_as_typed_edit(
    qtbot: object,
    tmp_path: Path,
) -> None:
    editor = WorkflowEditor(WorkflowEditingService())
    qtbot.addWidget(editor)  # type: ignore[attr-defined]
    editor.set_session(project_session(tmp_path))
    observed: list[object] = []
    editor.edit_requested.connect(observed.append)
    command = RenameStep(step_id=FIRST_STEP_ID, label="변경된 단계")

    assert editor.apply_command(command)

    assert observed == [command]
    assert editor.session is not None and editor.session.dirty
    assert editor.session.workflow.steps[0].label == "변경된 단계"
    assert (
        editor.tree_model.data(
            editor.tree_model.index_for_step(FIRST_STEP_ID),
            int(Qt.ItemDataRole.DisplayRole),
        )
        == "동작 · 변경된 단계"
    )
