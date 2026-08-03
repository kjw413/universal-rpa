"""Contract and descriptor guarantees for the tabular output adapter."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import UUID

import pytest

from tests.contract.automation_adapter_contract import (
    RUN_ID,
    STEP_ID,
    AutomationAdapterContract,
    execution_context,
    runtime_environment,
)
from universal_rpa.adapters.tabular.adapter import (
    TABULAR_ADAPTER_VERSION,
    TabularAutomationAdapter,
)
from universal_rpa.domain.conditions import AssertionSpec, ConditionSpec
from universal_rpa.domain.errors import ErrorCode
from universal_rpa.domain.results import LoopCursor, TableData
from universal_rpa.domain.targets import DateContext, TargetSpec
from universal_rpa.domain.types import FrozenMapping
from universal_rpa.ports.automation import (
    ActionRequest,
    AutomationAdapter,
    CancellationToken,
    ExecutionContext,
)

LOOP_ID = UUID("00000000-0000-0000-0000-000000000602")


def table_data() -> TableData:
    return TableData(headers=("factory", "amount"), rows=(("A동", 1), ("B동", 2)))


def save_parameters(output_path: str = "out.csv") -> FrozenMapping[str, object]:
    return FrozenMapping((("format", "csv"), ("output_path", output_path)))


def save_request(output_path: str = "out.csv") -> ActionRequest:
    return ActionRequest(
        action_type="tabular.save_table",
        target=None,
        parameters=save_parameters(output_path),  # type: ignore[arg-type]
        value=table_data(),
        has_postcondition_or_assertion=False,
    )


def output_context(
    output_root: Path,
    *,
    iteration_cursor: tuple[LoopCursor, ...] = (),
) -> ExecutionContext:
    return ExecutionContext(
        run_id=RUN_ID,
        step_id=STEP_ID,
        iteration_path=(),
        variables=FrozenMapping.empty(),
        credential_refs=FrozenMapping.empty(),
        date_context=DateContext(today=date(2026, 8, 3), run_date=date(2026, 8, 3)),
        output_root=output_root,
        row_stack=(),
        action_outputs=FrozenMapping.empty(),
        iteration_cursor=iteration_cursor,
    )


class TestTabularAutomationAdapter(AutomationAdapterContract):
    def make_adapter(self) -> AutomationAdapter:
        return TabularAutomationAdapter()

    def make_supported_request(self, adapter: AutomationAdapter) -> ActionRequest:
        del adapter
        return save_request()

    def side_effect_count(self, adapter: AutomationAdapter) -> int:
        committed: int = adapter.committed_saves  # type: ignore[attr-defined]
        return committed


def test_tabular_descriptor_is_exact() -> None:
    descriptor = TabularAutomationAdapter().descriptor()

    assert descriptor.adapter_id == "tabular"
    assert descriptor.implementation_version == TABULAR_ADAPTER_VERSION == "1.0.0"
    assert descriptor.supports_target_capture is False
    assert descriptor.actions == frozenset({"tabular.save_table"})
    assert descriptor.conditions == frozenset({"tabular.file_exists", "tabular.file_stable"})
    assert descriptor.assertions == frozenset()
    assert descriptor.verification_by_action["tabular.save_table"] == "intrinsic"
    assert descriptor.idempotent_actions == frozenset({"tabular.save_table"})
    assert descriptor.retryable_errors_by_action["tabular.save_table"] == frozenset(
        {ErrorCode.OUTPUT_UNAVAILABLE}
    )


def test_save_table_commits_beneath_the_execution_output_root(tmp_path: Path) -> None:
    adapter = TabularAutomationAdapter()
    context = output_context(
        tmp_path,
        iteration_cursor=(LoopCursor(loop_step_id=LOOP_ID, row_index=1),),
    )

    result = adapter.execute(save_request("exports/out.csv"), context, CancellationToken())

    assert result.error_code is None
    commit = result.output_commit
    assert commit is not None
    assert commit.destination == (tmp_path / "exports" / "out.csv").resolve()
    assert commit.producer_step_id == STEP_ID
    assert commit.producer_cursor == (LoopCursor(loop_step_id=LOOP_ID, row_index=1),)
    assert adapter.committed_saves == 1


def test_save_table_evidence_excludes_table_cells(tmp_path: Path) -> None:
    adapter = TabularAutomationAdapter()

    result = adapter.execute(save_request(), output_context(tmp_path), CancellationToken())

    encoded = repr(dict(result.evidence))
    assert "A동" not in encoded
    assert "B동" not in encoded


def test_save_table_without_an_extracted_table_fails_closed(tmp_path: Path) -> None:
    adapter = TabularAutomationAdapter()
    request = ActionRequest(
        action_type="tabular.save_table",
        target=None,
        parameters=save_parameters(),  # type: ignore[arg-type]
        value=None,
        has_postcondition_or_assertion=False,
    )

    result = adapter.execute(request, output_context(tmp_path), CancellationToken())

    assert result.error_code is ErrorCode.INVALID_SCHEMA
    assert adapter.committed_saves == 0
    assert list(tmp_path.iterdir()) == []


def test_adapter_rejects_targets_and_declares_no_assertions() -> None:
    adapter = TabularAutomationAdapter()
    target = TargetSpec(adapter_id="tabular", payload={"any": 1})

    target_issues = adapter.validate_target(target, runtime_environment(), "must_exist_now")
    assertion_issues = adapter.validate_assertion_spec(
        AssertionSpec(assertion_type="tabular.anything")
    )

    assert [issue.code for issue in target_issues] == [ErrorCode.INVALID_SCHEMA]
    assert [issue.code for issue in assertion_issues] == [ErrorCode.INVALID_SCHEMA]


@pytest.mark.parametrize("condition_type", ["tabular.file_exists", "tabular.file_stable"])
def test_supported_conditions_require_a_relative_output_path(condition_type: str) -> None:
    adapter = TabularAutomationAdapter()

    accepted = adapter.validate_condition_spec(
        ConditionSpec(condition_type=condition_type, expected={"output_path": "out.csv"})
    )
    rejected = adapter.validate_condition_spec(
        ConditionSpec(condition_type=condition_type, expected={"output_path": "../out.csv"})
    )

    assert accepted == ()
    assert [issue.code for issue in rejected] == [ErrorCode.INVALID_SCHEMA]


def test_file_exists_condition_observes_the_resolved_destination(tmp_path: Path) -> None:
    adapter = TabularAutomationAdapter()
    context = output_context(tmp_path)
    condition = ConditionSpec(
        condition_type="tabular.file_exists", expected={"output_path": "out.csv"}
    )

    absent = adapter.evaluate_condition(condition, context, CancellationToken())
    (tmp_path / "out.csv").write_bytes(b"present")
    present = adapter.evaluate_condition(condition, context, CancellationToken())

    assert absent.satisfied is False
    assert present.satisfied is True


def test_file_stable_condition_requires_two_identical_polls(tmp_path: Path) -> None:
    adapter = TabularAutomationAdapter()
    context = output_context(tmp_path)
    condition = ConditionSpec(
        condition_type="tabular.file_stable", expected={"output_path": "out.csv"}
    )
    destination = tmp_path / "out.csv"
    destination.write_bytes(b"first")

    first = adapter.evaluate_condition(condition, context, CancellationToken())
    second = adapter.evaluate_condition(condition, context, CancellationToken())
    destination.write_bytes(b"changed-content-of-a-different-length")
    third = adapter.evaluate_condition(condition, context, CancellationToken())

    assert first.satisfied is False
    assert second.satisfied is True
    assert third.satisfied is False


def test_unsupported_action_never_touches_the_output_root(tmp_path: Path) -> None:
    adapter = TabularAutomationAdapter()
    request = ActionRequest(
        action_type="tabular.delete_table",
        target=None,
        parameters=FrozenMapping.empty(),
        value=None,
        has_postcondition_or_assertion=False,
    )

    result = adapter.execute(request, output_context(tmp_path), CancellationToken())

    assert result.error_code is ErrorCode.ACTION_UNSUPPORTED
    assert list(tmp_path.iterdir()) == []


def test_execution_context_default_cursor_is_empty() -> None:
    assert execution_context().iteration_cursor == ()
