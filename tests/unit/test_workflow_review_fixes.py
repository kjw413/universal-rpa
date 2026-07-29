from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

import scripts.export_schema as export_schema
from universal_rpa.application.workflow_codec import dump_workflow, load_workflow
from universal_rpa.domain.conditions import ConditionSpec
from universal_rpa.domain.targets import TargetSpec
from universal_rpa.domain.types import FrozenMapping
from universal_rpa.domain.values import LiteralValue
from universal_rpa.domain.workflow import (
    ActionStep,
    InlineDataSource,
    OutputRelativePath,
    ProjectRelativePath,
    Workflow,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_ID = UUID("00000000-0000-0000-0000-000000000001")
STEP_1 = UUID("00000000-0000-0000-0000-000000000101")
STEP_2 = UUID("00000000-0000-0000-0000-000000000102")
STEP_3 = UUID("00000000-0000-0000-0000-000000000103")
STEP_4 = UUID("00000000-0000-0000-0000-000000000104")
NOW = datetime(2026, 7, 27, 1, 2, 3, tzinfo=UTC)


def action_step(
    *,
    step_id: UUID = STEP_1,
    action_type: str = "custom.action",
    **changes: object,
) -> dict[str, object]:
    step: dict[str, object] = {
        "step_id": step_id,
        "label": f"Action {step_id}",
        "kind": "action",
        "action_type": action_type,
    }
    step.update(changes)
    return step


def loop_step(
    *,
    step_id: UUID,
    steps: tuple[dict[str, object], ...],
) -> dict[str, object]:
    return {
        "step_id": step_id,
        "label": f"Loop {step_id}",
        "kind": "loop",
        "data_source_id": "orders",
        "steps": steps,
    }


def workflow_payload(
    steps: tuple[dict[str, object], ...] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": "1",
        "workflow_id": WORKFLOW_ID,
        "name": "Review workflow",
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


def extraction(step_id: UUID) -> dict[str, object]:
    return action_step(
        step_id=step_id,
        action_type="clipboard.extract_table",
        assertions=({"assertion_type": "clipboard.table"},),
    )


def save(step_id: UUID, producer_id: UUID) -> dict[str, object]:
    return action_step(
        step_id=step_id,
        action_type="tabular.save_table",
        input_step_id=producer_id,
    )


def test_workflow_dump_is_canonical_across_python_hash_seeds() -> None:
    probe = """
from datetime import UTC, datetime
from uuid import UUID
import sys
from universal_rpa.application.workflow_codec import dump_workflow
from universal_rpa.domain.workflow import Workflow

now = datetime(2026, 7, 27, tzinfo=UTC)
workflow = Workflow.model_validate({
    "schema_version": "1",
    "workflow_id": UUID("00000000-0000-0000-0000-000000000001"),
    "name": "Canonical",
    "revision": 0,
    "target_apps": [{
        "app_id": "erp",
        "process_executable": "erp.exe",
        "window_class": "ERPMain",
    }],
    "steps": [{
        "step_id": UUID("00000000-0000-0000-0000-000000000101"),
        "label": "Assert",
        "kind": "action",
        "action_type": "custom.action",
        "assertions": [{
            "assertion_type": "clipboard.table",
            "required_headers": {"zeta", "alpha", "middle"},
            "required_tokens": {"ready", "complete", "approved"},
        }],
    }],
    "created_at": now,
    "updated_at": now,
})
sys.stdout.buffer.write(dump_workflow(workflow))
"""
    outputs: list[bytes] = []
    for seed in ("1", "987654"):
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = seed
        result = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            check=False,
            cwd=PROJECT_ROOT,
            env=environment,
        )
        assert result.returncode == 0, result.stderr.decode()
        outputs.append(result.stdout)

    assert outputs[0] == outputs[1]
    assertion = json.loads(outputs[0])["steps"][0]["assertions"][0]
    assert assertion["required_headers"] == ["alpha", "middle", "zeta"]
    assert assertion["required_tokens"] == ["approved", "complete", "ready"]


@pytest.mark.parametrize("non_finite", [float("nan"), float("inf"), float("-inf")])
def test_workflow_scalar_boundaries_reject_non_finite_numbers(
    non_finite: float,
) -> None:
    with pytest.raises(ValidationError):
        InlineDataSource(
            data_source_id="rows",
            label="Rows",
            headers=("value",),
            rows=((non_finite,),),
        )
    with pytest.raises(ValidationError):
        LiteralValue(value=non_finite)


def test_inline_data_rejects_non_finite_rows_supplied_by_generator() -> None:
    rows = (row for row in ((float("nan"),),))
    with pytest.raises(ValidationError):
        InlineDataSource(
            data_source_id="rows",
            label="Rows",
            headers=("value",),
            rows=rows,  # type: ignore[arg-type]
        )

    payload = workflow_payload()
    payload["data_sources"] = [
        {
            "source_type": "inline",
            "data_source_id": "orders",
            "label": "Orders",
            "headers": ["value"],
            "rows": (row for row in ((float("nan"),),)),
        }
    ]
    with pytest.raises(ValidationError):
        Workflow.model_validate(payload)


@pytest.mark.parametrize("non_finite", [float("nan"), float("inf"), float("-inf")])
def test_arbitrary_json_boundaries_reject_non_finite_numbers(
    non_finite: float,
) -> None:
    with pytest.raises(ValidationError):
        TargetSpec(adapter_id="fake", payload={"value": [non_finite]})
    with pytest.raises(ValidationError):
        ConditionSpec(
            condition_type="fake.value_equals",
            expected={"value": [non_finite]},
        )
    with pytest.raises(ValidationError):
        ActionStep.model_validate(action_step(parameters={"nested": {"value": non_finite}}))


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_textual_workflow_json_rejects_nonstandard_number_constants(
    constant: str,
) -> None:
    serialized = dump_workflow(load_workflow(workflow_payload())).decode()
    serialized = serialized.replace(
        '"parameters": {}',
        f'"parameters": {{"invalid": {constant}}}',
    )

    with pytest.raises(ValueError, match="non-finite JSON number"):
        load_workflow(serialized)


def test_dump_defensively_rejects_non_finite_constructed_models() -> None:
    workflow = load_workflow(workflow_payload())
    step = workflow.steps[0]
    assert isinstance(step, ActionStep)
    invalid_step = step.model_copy(
        update={"parameters": FrozenMapping.from_mapping({"invalid": float("nan")})}
    )
    invalid_workflow = workflow.model_copy(update={"steps": (invalid_step,)})

    with pytest.raises(ValueError):
        dump_workflow(invalid_workflow)


def test_finite_numbers_round_trip_through_every_workflow_boundary() -> None:
    payload = workflow_payload(
        (
            action_step(
                value={"mode": "literal", "value": 1.25},
                parameters={"nested": {"value": 2.5}},
                target={"adapter_id": "fake", "payload": {"score": 3.75}},
                precondition={
                    "condition": {
                        "condition_type": "fake.value_equals",
                        "expected": {"score": 4.5},
                    },
                    "timeout_ms": 100,
                },
            ),
        )
    )
    payload["data_sources"] = [
        {
            "source_type": "inline",
            "data_source_id": "orders",
            "label": "Orders",
            "headers": ["value"],
            "rows": [[5.25]],
        }
    ]

    workflow = load_workflow(payload)

    assert load_workflow(dump_workflow(workflow)) == workflow


def _make_directory_symlink(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as error:
        if error.winerror == 1314:
            pytest.skip(f"directory symlink privilege unavailable: {error}")
        raise


def test_symlink_helper_reraises_unexpected_os_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def raise_unexpected_error(
        path: Path, target: Path, *, target_is_directory: bool = False
    ) -> None:
        raise OSError("unexpected filesystem failure")

    monkeypatch.setattr(Path, "symlink_to", raise_unexpected_error)

    with pytest.raises(OSError, match="unexpected filesystem failure"):
        try:
            _make_directory_symlink(tmp_path / "link", tmp_path / "target")
        except pytest.skip.Exception:
            pytest.fail("unexpected OSError was incorrectly converted to a skip")


def test_path_resolution_rejects_a_symlink_or_reparse_root(tmp_path: Path) -> None:
    actual_root = tmp_path / "actual"
    actual_root.mkdir()
    linked_root = tmp_path / "linked"
    _make_directory_symlink(linked_root, actual_root)

    with pytest.raises(ValueError, match="root cannot be"):
        OutputRelativePath("report.csv").resolve_under(linked_root)


def test_path_resolution_rejects_an_intermediate_escaping_link(tmp_path: Path) -> None:
    root = tmp_path / "project"
    inputs = root / "inputs"
    outside = tmp_path / "outside"
    inputs.mkdir(parents=True)
    outside.mkdir()
    _make_directory_symlink(inputs / "linked", outside)

    with pytest.raises(ValueError, match="cannot traverse"):
        ProjectRelativePath("inputs/linked/orders.csv").resolve_under(root)


def test_path_resolution_defensively_rejects_resolved_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "approved"
    root.mkdir()
    outside = tmp_path / "outside" / "report.csv"
    real_resolve = Path.resolve

    def controlled_resolve(path: Path, *args: object, **kwargs: object) -> Path:
        if path == root:
            return root
        if path == root / "report.csv":
            return outside
        return real_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", controlled_resolve)

    with pytest.raises(ValueError, match="escapes supplied root"):
        OutputRelativePath("report.csv").resolve_under(root)


@pytest.mark.parametrize(
    "steps",
    [
        (save(STEP_1, STEP_2), extraction(STEP_2)),
        (
            loop_step(step_id=STEP_1, steps=(extraction(STEP_2),)),
            loop_step(step_id=STEP_3, steps=(save(STEP_4, STEP_2),)),
        ),
        (
            extraction(STEP_1),
            loop_step(step_id=STEP_2, steps=(save(STEP_3, STEP_1),)),
        ),
        (
            loop_step(step_id=STEP_1, steps=(extraction(STEP_2),)),
            save(STEP_3, STEP_2),
        ),
    ],
    ids=[
        "producer-after-consumer",
        "sibling-loop",
        "outer-to-inner",
        "inner-to-outer",
    ],
)
def test_save_table_rejects_non_dominating_iteration_frames(
    steps: tuple[dict[str, object], ...],
) -> None:
    with pytest.raises(ValidationError, match="same iteration frame"):
        Workflow.model_validate(workflow_payload(steps))


def test_schema_check_reports_a_temporary_mismatched_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = tmp_path / "workflow-v1.schema.json"
    snapshot.write_bytes(b"mismatched\n")
    monkeypatch.setattr(export_schema, "SCHEMA_PATH", snapshot)
    monkeypatch.setattr(sys, "argv", ["export_schema.py", "--check"])

    assert export_schema.main() == 1
