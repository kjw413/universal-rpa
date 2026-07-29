from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from universal_rpa.domain.types import FrozenMapping
from universal_rpa.domain.workflow import (
    ActionStep,
    CsvDataSource,
    IfPresentStep,
    InlineDataSource,
    LoopStep,
    OutputPolicy,
    OutputRelativePath,
    PresenceSpec,
    ProjectRelativePath,
    RunPolicy,
    Workflow,
    XlsxDataSource,
)

WORKFLOW_ID = UUID("00000000-0000-0000-0000-000000000001")
STEP_1 = UUID("00000000-0000-0000-0000-000000000101")
STEP_2 = UUID("00000000-0000-0000-0000-000000000102")
STEP_3 = UUID("00000000-0000-0000-0000-000000000103")
STEP_4 = UUID("00000000-0000-0000-0000-000000000104")
MISSING_STEP_ID = UUID("00000000-0000-0000-0000-000000000999")
NOW = datetime(2026, 7, 27, 1, 2, 3, tzinfo=UTC)


def windows_target_spec(*, coordinate_only: bool = False) -> dict[str, object]:
    payload: dict[str, object] = {
        "selector": None if coordinate_only else {"automation_id": "submit"},
        "coordinate_fallback": {
            "recorded_process_executable": "erp.exe",
            "recorded_window_class": "ERPMain",
            "point": {"x": 0.5, "y": 0.5},
            "recorded_dpi_x": 96,
            "recorded_dpi_y": 96,
            "recorded_client_width": 1280,
            "recorded_client_height": 720,
        }
        if coordinate_only
        else None,
    }
    return {"adapter_id": "windows", "payload": payload}


def wait_spec() -> dict[str, object]:
    return {
        "condition": {"condition_type": "windows.element_exists"},
        "timeout_ms": 1_000,
    }


def assertion_spec() -> dict[str, object]:
    return {"assertion_type": "windows.element_exists"}


def action_step(
    *,
    step_id: UUID = STEP_1,
    action_type: str = "windows.click",
    **changes: object,
) -> dict[str, object]:
    step: dict[str, object] = {
        "step_id": step_id,
        "label": f"Action {step_id}",
        "kind": "action",
        "action_type": action_type,
        "target": windows_target_spec(),
    }
    step.update(changes)
    return step


def presence_spec() -> dict[str, object]:
    return {
        "condition_type": "windows.element_exists",
        "target": windows_target_spec(),
        "timeout_ms": 1_000,
    }


def loop_step(
    *,
    step_id: UUID = STEP_2,
    steps: tuple[dict[str, object], ...] = (),
    **changes: object,
) -> dict[str, object]:
    step: dict[str, object] = {
        "step_id": step_id,
        "label": f"Loop {step_id}",
        "kind": "loop",
        "data_source_id": "orders",
        "steps": steps or (action_step(),),
    }
    step.update(changes)
    return step


def if_present_step(
    *,
    step_id: UUID = STEP_2,
    steps: tuple[dict[str, object], ...] = (),
    **changes: object,
) -> dict[str, object]:
    step: dict[str, object] = {
        "step_id": step_id,
        "label": f"If {step_id}",
        "kind": "if_present",
        "condition": presence_spec(),
        "steps": steps or (action_step(),),
    }
    step.update(changes)
    return step


def valid_workflow_payload(
    steps: tuple[dict[str, object], ...] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": "1",
        "workflow_id": WORKFLOW_ID,
        "name": "Order export",
        "revision": 0,
        "target_apps": [
            {
                "app_id": "erp",
                "process_executable": "erp.exe",
                "window_class": "ERPMain",
            }
        ],
        "data_sources": [
            {
                "source_type": "inline",
                "data_source_id": "orders",
                "label": "Orders",
                "headers": ["order_id"],
                "rows": [["A-001"]],
            }
        ],
        "steps": list(steps or (action_step(),)),
        "created_at": NOW,
        "updated_at": NOW,
    }


def test_recursive_tagged_steps_are_parsed_and_frozen() -> None:
    source = loop_step(
        steps=(
            if_present_step(
                step_id=STEP_3,
                steps=(action_step(step_id=STEP_4),),
            ),
        )
    )

    workflow = Workflow.model_validate(valid_workflow_payload((source,)))
    source["steps"][0]["steps"][0]["label"] = "mutated"  # type: ignore[index]

    assert isinstance(workflow.steps[0], LoopStep)
    assert isinstance(workflow.steps[0].steps[0], IfPresentStep)
    assert workflow.steps[0].steps[0].steps[0].label != "mutated"
    with pytest.raises(ValidationError):
        workflow.name = "mutated"


def test_third_nested_loop_is_rejected() -> None:
    nested = loop_step(
        steps=(
            loop_step(
                step_id=STEP_3,
                steps=(loop_step(step_id=STEP_4),),
            ),
        )
    )
    with pytest.raises(ValidationError, match="maximum loop depth is 2"):
        Workflow.model_validate(valid_workflow_payload((nested,)))


def test_workflow_cannot_self_declare_idempotency() -> None:
    with pytest.raises(ValidationError):
        ActionStep.model_validate(action_step(idempotent=True))


def test_composite_steps_cannot_retry() -> None:
    with pytest.raises(ValidationError):
        LoopStep.model_validate(loop_step(failure_policy={"mode": "retry", "retry_count": 1}))
    with pytest.raises(ValidationError):
        IfPresentStep.model_validate(
            if_present_step(failure_policy={"mode": "retry", "retry_count": 1})
        )


def test_if_present_accepts_only_positive_matching_target_presence_and_cannot_nest() -> None:
    with pytest.raises(ValidationError):
        PresenceSpec.model_validate(
            {
                "condition_type": "windows.value_equals",
                "target": windows_target_spec(),
                "timeout_ms": 1_000,
            }
        )
    with pytest.raises(ValidationError):
        PresenceSpec.model_validate(
            {
                "condition_type": "windows.element_exists",
                "target": windows_target_spec(),
                "timeout_ms": 1_000,
                "expected": False,
            }
        )
    with pytest.raises(ValidationError):
        PresenceSpec.model_validate(
            {
                "condition_type": "web.element_exists",
                "target": windows_target_spec(),
                "timeout_ms": 1_000,
            }
        )
    with pytest.raises(ValidationError):
        Workflow.model_validate(
            valid_workflow_payload(
                (
                    if_present_step(
                        steps=(
                            loop_step(
                                step_id=STEP_3,
                                steps=(if_present_step(step_id=STEP_4),),
                            ),
                        )
                    ),
                )
            )
        )


def test_data_source_shapes_are_discriminated_and_project_relative() -> None:
    csv = CsvDataSource(
        data_source_id="orders",
        label="주문",
        path="inputs/orders.csv",
        encoding="cp949",
    )
    xlsx = XlsxDataSource(
        data_source_id="orders",
        label="주문",
        path="inputs/orders.xlsx",
        sheet_name="Orders",
    )

    assert csv.source_type == "csv"
    assert xlsx.path.root == "inputs/orders.xlsx"
    for unsafe in (
        r"C:\orders.csv",
        "../orders.csv",
        "inputs/../orders.csv",
        "inputs//orders.csv",
        "/inputs/orders.csv",
        "inputs/CON.csv",
    ):
        with pytest.raises(ValidationError):
            CsvDataSource(
                data_source_id="orders",
                label="주문",
                path=unsafe,
                encoding="utf-8",
            )
    with pytest.raises(ValidationError):
        InlineDataSource(
            data_source_id="rows",
            label="행",
            headers=("factory",),
            rows=(({"nested": "no"},),),  # type: ignore[arg-type]
        )


def test_inline_data_is_trimmed_rectangular_unique_and_immutable() -> None:
    rows = [["A", 1]]
    source = InlineDataSource(
        data_source_id="rows",
        label="행",
        headers=(" factory ", "count"),
        rows=rows,
    )
    rows[0][0] = "mutated"

    assert source.headers == ("factory", "count")
    assert source.rows == (("A", 1),)
    with pytest.raises(ValidationError):
        InlineDataSource(
            data_source_id="rows",
            label="행",
            headers=("factory", "factory"),
            rows=(("A", "B"),),
        )
    with pytest.raises(ValidationError):
        InlineDataSource(
            data_source_id="rows",
            label="행",
            headers=("factory", "count"),
            rows=(("A",),),
        )


def test_relative_paths_resolve_only_under_the_supplied_root(tmp_path: Path) -> None:
    project_root = tmp_path / "chosen-project"
    (project_root / "inputs").mkdir(parents=True)
    path = ProjectRelativePath("inputs/orders.csv")
    output = OutputRelativePath("reports/orders.csv")

    assert path.resolve_under(project_root) == (project_root / "inputs" / "orders.csv").resolve()
    assert (
        output.resolve_under(tmp_path / "run-output")
        == (tmp_path / "run-output" / "reports" / "orders.csv").resolve()
    )


def test_save_table_requires_a_dominating_extraction_in_same_iteration_frame() -> None:
    save = action_step(
        step_id=STEP_2,
        action_type="tabular.save_table",
        input_step_id=MISSING_STEP_ID,
    )
    with pytest.raises(ValidationError):
        Workflow.model_validate(valid_workflow_payload((save,)))

    optional_extract = if_present_step(
        steps=(
            action_step(
                step_id=STEP_3,
                action_type="clipboard.extract_table",
                assertions=(assertion_spec(),),
            ),
        )
    )
    save_optional = action_step(
        step_id=STEP_4,
        action_type="tabular.save_table",
        input_step_id=STEP_3,
    )
    with pytest.raises(ValidationError):
        Workflow.model_validate(valid_workflow_payload((optional_extract, save_optional)))

    disabled_extract = action_step(
        action_type="clipboard.extract_table",
        enabled=False,
        assertions=(assertion_spec(),),
    )
    save_disabled = action_step(
        step_id=STEP_2,
        action_type="tabular.save_table",
        input_step_id=STEP_1,
    )
    with pytest.raises(ValidationError):
        Workflow.model_validate(valid_workflow_payload((disabled_extract, save_disabled)))


def test_enabled_dominating_extraction_can_feed_save_in_same_frame() -> None:
    extract = action_step(
        action_type="clipboard.extract_table",
        assertions=(assertion_spec(),),
    )
    save = action_step(
        step_id=STEP_2,
        action_type="tabular.save_table",
        input_step_id=STEP_1,
    )

    workflow = Workflow.model_validate(valid_workflow_payload((extract, save)))

    assert workflow.steps[1].input_step_id == STEP_1  # type: ignore[union-attr]


def test_extraction_and_coordinate_fallback_require_assertion_or_postcondition() -> None:
    with pytest.raises(ValidationError):
        ActionStep.model_validate(action_step(action_type="clipboard.extract_table", assertions=()))
    with pytest.raises(ValidationError):
        ActionStep.model_validate(
            action_step(
                target=windows_target_spec(coordinate_only=True),
                postcondition=None,
                assertions=(),
            )
        )
    assert ActionStep.model_validate(
        action_step(
            target=windows_target_spec(coordinate_only=True),
            postcondition=wait_spec(),
        )
    )


def test_wait_payload_and_skip_iteration_are_context_restricted() -> None:
    with pytest.raises(ValidationError):
        ActionStep.model_validate(action_step(action_type="windows.wait"))
    with pytest.raises(ValidationError):
        ActionStep.model_validate(action_step(wait=wait_spec()))
    with pytest.raises(ValidationError):
        Workflow.model_validate(
            valid_workflow_payload((action_step(failure_policy={"mode": "skip_iteration"}),))
        )

    valid_wait = action_step(
        step_id=STEP_3,
        action_type="windows.wait",
        wait=wait_spec(),
        target=None,
    )
    nested_skip = loop_step(
        steps=(
            action_step(
                failure_policy={"mode": "skip_iteration"},
            ),
        )
    )
    assert Workflow.model_validate(valid_workflow_payload((valid_wait, nested_skip)))


def test_ids_references_and_variable_data_source_kinds_are_resolved() -> None:
    payload = valid_workflow_payload()
    payload["variables"] = [
        {
            "variable_id": "order_id",
            "label": "Order",
            "value_type": "choice",
            "source": {
                "source_type": "csv_column",
                "data_source_id": "orders",
                "column_name": "order_id",
            },
        }
    ]
    with pytest.raises(ValidationError):
        Workflow.model_validate(payload)

    payload = valid_workflow_payload()
    payload["steps"] = [action_step(), action_step()]
    with pytest.raises(ValidationError):
        Workflow.model_validate(payload)

    payload = valid_workflow_payload()
    payload["steps"] = [
        action_step(
            value={"mode": "variable", "variable_id": "missing"},
        )
    ]
    with pytest.raises(ValidationError):
        Workflow.model_validate(payload)


def test_workflow_timestamps_are_utc_ordered_and_policy_limits_are_bounded() -> None:
    payload = valid_workflow_payload()
    payload["updated_at"] = NOW - timedelta(seconds=1)
    with pytest.raises(ValidationError):
        Workflow.model_validate(payload)

    payload = valid_workflow_payload()
    payload["created_at"] = NOW.replace(tzinfo=None)
    with pytest.raises(ValidationError):
        Workflow.model_validate(payload)

    assert RunPolicy().max_iterations == 1_000
    assert RunPolicy().max_runtime_seconds == 7_200
    assert OutputPolicy().artifact_retention_days == 30
    with pytest.raises(ValidationError):
        RunPolicy(max_iterations=10_001)
    with pytest.raises(ValidationError):
        RunPolicy(max_runtime_seconds=86_401)


def test_parameter_trees_are_deep_frozen_before_observation() -> None:
    parameters = {"custom": {"items": ["safe"]}}
    step = ActionStep.model_validate(
        action_step(action_type="custom.action", parameters=parameters)
    )
    parameters["custom"]["items"][0] = "mutated"  # type: ignore[index]

    assert isinstance(step.parameters, FrozenMapping)
    assert step.model_dump(mode="json")["parameters"] == {"custom": {"items": ["safe"]}}
