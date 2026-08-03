from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from PySide6.QtCore import Qt

from universal_rpa.domain.targets import TargetSpec
from universal_rpa.domain.values import SecretRefValue
from universal_rpa.domain.workflow import (
    ActionStep,
    InlineDataSource,
    LoopStep,
    TargetAppSpec,
    Workflow,
)
from universal_rpa.ui.step_tree_model import StepTreeRole, WorkflowTreeModel

OUTER_LOOP_ID = UUID("00000000-0000-0000-0000-000000000841")
INNER_LOOP_ID = UUID("00000000-0000-0000-0000-000000000842")
INNER_ACTION_ID = UUID("00000000-0000-0000-0000-000000000843")
NOW = datetime(2026, 7, 27, tzinfo=UTC)


def nested_workflow() -> Workflow:
    target = TargetSpec.model_validate(
        {
            "adapter_id": "windows",
            "payload": {
                "selector": {"automation_id": "password"},
                "coordinate_fallback": None,
            },
        }
    )
    action = ActionStep(
        step_id=INNER_ACTION_ID,
        label="암호 입력",
        action_type="windows.set_text",
        target=target,
        value=SecretRefValue(credential_ref="erp/password"),
    )
    inner = LoopStep(
        step_id=INNER_LOOP_ID,
        label="내부 반복",
        data_source_id="rows",
        steps=(action,),
    )
    outer = LoopStep(
        step_id=OUTER_LOOP_ID,
        label="외부 반복",
        data_source_id="rows",
        steps=(inner,),
    )
    return Workflow(
        workflow_id=UUID("00000000-0000-0000-0000-000000000840"),
        name="트리 테스트",
        revision=1,
        target_apps=(
            TargetAppSpec(
                app_id="erp",
                process_executable="erp.exe",
                window_class="ERPMain",
            ),
        ),
        data_sources=(
            InlineDataSource(
                data_source_id="rows",
                label="행",
                headers=("value",),
                rows=(("A",),),
            ),
        ),
        steps=(outer,),
        created_at=NOW,
        updated_at=NOW,
    )


def test_recursive_indexes_preserve_parent_relationships() -> None:
    model = WorkflowTreeModel()
    model.set_workflow(nested_workflow())

    action_index = model.index_for_step(INNER_ACTION_ID)
    inner_index = model.parent(action_index)
    outer_index = model.parent(inner_index)

    assert model.step_id(inner_index) == INNER_LOOP_ID
    assert model.step_id(outer_index) == OUTER_LOOP_ID


def test_drag_drop_rejects_invalid_parent_and_loop_depth_three() -> None:
    model = WorkflowTreeModel()
    model.set_workflow(nested_workflow())

    assert not model.can_move(INNER_LOOP_ID, under=INNER_ACTION_ID)
    assert not model.can_move(OUTER_LOOP_ID, under=INNER_LOOP_ID)


def test_model_roles_never_expose_secret_references() -> None:
    model = WorkflowTreeModel()
    model.set_workflow(nested_workflow())
    index = model.index_for_step(INNER_ACTION_ID)
    exposed = (
        model.data(index, int(Qt.ItemDataRole.DisplayRole)),
        model.data(index, int(StepTreeRole.STEP_ID)),
        model.data(index, int(StepTreeRole.KIND)),
        model.data(index, int(StepTreeRole.ENABLED)),
        model.data(index, int(StepTreeRole.VALIDATION_SEVERITY)),
    )

    assert "erp/password" not in repr(exposed)
