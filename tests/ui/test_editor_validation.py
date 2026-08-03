from __future__ import annotations

from pathlib import Path

from tests.ui.test_editor_page import FIRST_STEP_ID, project_session
from universal_rpa.adapters.registry import AdapterRegistry
from universal_rpa.application.editing import WorkflowEditingService
from universal_rpa.application.validation import ValidationService
from universal_rpa.domain.errors import ErrorCode
from universal_rpa.ui.editor_page import WorkflowEditor
from universal_rpa.ui.step_tree_model import StepTreeRole


def test_static_validation_badge_refreshes_after_session_load(
    qtbot: object,
    tmp_path: Path,
) -> None:
    editor = WorkflowEditor(
        WorkflowEditingService(),
        validation_service=ValidationService(registry=AdapterRegistry()),
    )
    qtbot.addWidget(editor)  # type: ignore[attr-defined]

    editor.set_session(project_session(tmp_path))

    severity = editor.tree_model.data(
        editor.tree_model.index_for_step(FIRST_STEP_ID),
        int(StepTreeRole.VALIDATION_SEVERITY),
    )
    report = ValidationService(registry=AdapterRegistry()).validate_static(
        project_session(tmp_path).workflow
    )
    assert ErrorCode.ADAPTER_MISSING in {issue.code for issue in report.errors}
    assert severity == "error"


def test_cancelled_retarget_preserves_old_target_and_preview(
    qtbot: object,
    tmp_path: Path,
) -> None:
    editor = WorkflowEditor(WorkflowEditingService())
    qtbot.addWidget(editor)  # type: ignore[attr-defined]
    editor.set_session(project_session(tmp_path))
    old_target = editor.selected_step()
    old_preview = editor.target_preview.preview_path

    class CancelledPicker:
        class DialogCode:
            Accepted = 1

        def exec(self) -> int:
            return 0

    editor.target_picker_factory = CancelledPicker  # type: ignore[assignment]
    editor.retarget_selected_step()

    assert editor.selected_step() == old_target
    assert editor.target_preview.preview_path == old_preview
