from __future__ import annotations

from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path

import pytest

from tests.contract.automation_adapter_contract import (
    TargetingAutomationAdapterContract,
    execution_context,
    fake_target,
    runtime_environment,
    target_capture_request,
)
from universal_rpa.adapters.fake import FakeAutomationAdapter
from universal_rpa.domain.conditions import AssertionSpec, ConditionSpec
from universal_rpa.domain.errors import ErrorCode, RpaError
from universal_rpa.domain.results import OutputCommit, TableData
from universal_rpa.domain.types import FrozenMapping
from universal_rpa.ports.automation import (
    ActionRequest,
    AdapterActionResult,
    AssertionObservation,
    CancellationToken,
    ConditionObservation,
    DataPreview,
    SecretValue,
    TargetCaptureResult,
)


def supported_request(adapter: FakeAutomationAdapter) -> ActionRequest:
    action_type = next(iter(adapter.descriptor().actions))
    return ActionRequest(
        action_type=action_type,
        target=fake_target(adapter.adapter_id),
        parameters={},
        value=None,
        has_postcondition_or_assertion=True,
    )


class TestFakeAutomationAdapter(TargetingAutomationAdapterContract):
    def make_adapter(self) -> FakeAutomationAdapter:
        return FakeAutomationAdapter()

    def make_supported_request(self, adapter: FakeAutomationAdapter) -> ActionRequest:
        return supported_request(adapter)

    def side_effect_count(self, adapter: FakeAutomationAdapter) -> int:
        return len(adapter.calls)

    def configure_ambiguous_target(self, adapter: FakeAutomationAdapter) -> None:
        adapter.script.append(RpaError(ErrorCode.TARGET_AMBIGUOUS, "대상이 여러 개 발견되었습니다"))


def test_invalid_parameters_have_no_side_effect() -> None:
    adapter = FakeAutomationAdapter()
    request = supported_request(adapter)
    request = ActionRequest(
        action_type=request.action_type,
        target=request.target,
        parameters={"invalid": True},
        value=request.value,
        has_postcondition_or_assertion=request.has_postcondition_or_assertion,
    )

    result = adapter.execute(request, execution_context(), CancellationToken())

    assert result.error_code is ErrorCode.INVALID_SCHEMA
    assert adapter.calls == ()


def test_script_is_fifo_and_reset_clears_script_and_calls() -> None:
    adapter = FakeAutomationAdapter()
    adapter.script.extend(
        (
            AdapterActionResult(output={"sequence": 1}, evidence={}),
            AdapterActionResult(output={"sequence": 2}, evidence={}),
        )
    )

    first = adapter.execute(supported_request(adapter), execution_context(), CancellationToken())
    second = adapter.execute(supported_request(adapter), execution_context(), CancellationToken())

    assert first.output == FrozenMapping.from_mapping({"sequence": 1})
    assert second.output == FrozenMapping.from_mapping({"sequence": 2})
    assert len(adapter.calls) == 2
    adapter.script.append(AdapterActionResult(output=None, evidence={}))
    adapter.reset()
    assert adapter.calls == ()
    assert tuple(adapter.script) == ()


@pytest.mark.parametrize(
    ("match_count", "expected_code"),
    (
        (0, ErrorCode.TARGET_NOT_FOUND),
        (2, ErrorCode.TARGET_AMBIGUOUS),
    ),
)
def test_target_match_failures_do_not_record_calls(
    match_count: int, expected_code: ErrorCode
) -> None:
    adapter = FakeAutomationAdapter()
    request = supported_request(adapter)
    request = ActionRequest(
        action_type=request.action_type,
        target=fake_target(adapter.adapter_id, candidate=match_count),
        parameters={"match_count": match_count},
        value=None,
        has_postcondition_or_assertion=True,
    )

    result = adapter.execute(request, execution_context(), CancellationToken())

    assert result.error_code is expected_code
    assert adapter.calls == ()


def test_capture_can_return_zero_one_or_two_candidates() -> None:
    adapter = FakeAutomationAdapter()

    for expected_count in (0, 1, 2):
        adapter.script.append(expected_count)
        result = adapter.capture_target(target_capture_request(), CancellationToken())
        assert len(result.candidates) == expected_count
        assert result.target is (result.candidates[0] if expected_count == 1 else None)


def test_capture_cancellation_does_not_consume_script_or_record_call() -> None:
    adapter = FakeAutomationAdapter()
    adapter.script.append(1)
    token = CancellationToken()
    token.cancel()

    result = adapter.capture_target(target_capture_request(), token)

    assert [issue.code for issue in result.issues] == [ErrorCode.CANCELLED]
    assert tuple(adapter.script) == (1,)
    assert adapter.calls == ()


def test_non_targeting_adapter_does_no_capture_work() -> None:
    adapter = FakeAutomationAdapter(supports_target_capture=False)
    adapter.script.append(1)

    result = adapter.capture_target(target_capture_request(), CancellationToken())

    assert result == TargetCaptureResult(
        target=None,
        candidates=(),
        preview_png=None,
        issues=result.issues,
    )
    assert [issue.code for issue in result.issues] == [ErrorCode.ACTION_UNSUPPORTED]
    assert tuple(adapter.script) == (1,)
    assert adapter.calls == ()


@pytest.mark.parametrize(
    ("mode", "match_count", "expected"),
    (
        ("must_exist_now", 0, ErrorCode.TARGET_NOT_FOUND),
        ("must_exist_now", 2, ErrorCode.TARGET_AMBIGUOUS),
        ("may_be_absent_now", 0, None),
        ("may_be_absent_now", 2, ErrorCode.TARGET_AMBIGUOUS),
        ("deferred", 2, None),
    ),
)
def test_validate_target_modes(mode: str, match_count: int, expected: ErrorCode | None) -> None:
    adapter = FakeAutomationAdapter()
    target = fake_target(adapter.adapter_id)
    target = target.model_copy(update={"payload": {"match_count": match_count}})

    issues = adapter.validate_target(target, runtime_environment(), mode)  # type: ignore[arg-type]

    assert [issue.code for issue in issues] == ([] if expected is None else [expected])
    assert adapter.calls == ()


def test_validate_target_rejects_namespace_and_environment_without_lookup() -> None:
    adapter = FakeAutomationAdapter()
    runtime = runtime_environment().model_copy(update={"interactive_desktop": False})

    namespace_issues = adapter.validate_target(
        fake_target("other"), runtime_environment(), "must_exist_now"
    )
    environment_issues = adapter.validate_target(
        fake_target(adapter.adapter_id), runtime, "must_exist_now"
    )

    assert [issue.code for issue in namespace_issues] == [ErrorCode.INVALID_SCHEMA]
    assert [issue.code for issue in environment_issues] == [ErrorCode.ENVIRONMENT_MISMATCH]
    assert adapter.calls == ()


def test_condition_and_assertion_evaluation_are_capability_checked_and_cancellable() -> None:
    adapter = FakeAutomationAdapter()
    context = execution_context()
    token = CancellationToken()
    token.cancel()

    with pytest.raises(RpaError) as condition_cancelled:
        adapter.evaluate_condition(
            ConditionSpec(condition_type="fake.ready"),
            context,
            token,
        )
    with pytest.raises(RpaError) as assertion_cancelled:
        adapter.evaluate_assertion(
            AssertionSpec(assertion_type="fake.equals"),
            None,
            None,
            context,
            token,
        )
    with pytest.raises(RpaError) as unsupported:
        adapter.evaluate_condition(
            ConditionSpec(condition_type="fake.unknown"),
            context,
            CancellationToken(),
        )

    assert condition_cancelled.value.code is ErrorCode.CANCELLED
    assert assertion_cancelled.value.code is ErrorCode.CANCELLED
    assert unsupported.value.code is ErrorCode.ACTION_UNSUPPORTED
    assert adapter.calls == ()


def test_scripted_condition_assertion_safe_error_and_output_commit() -> None:
    adapter = FakeAutomationAdapter()
    context = execution_context()
    commit = OutputCommit(
        destination=Path("out.csv"),
        format="csv",
        sheet_name=None,
        row_count=1,
        sha256="a" * 64,
        headers_sha256="b" * 64,
        committed=True,
        producer_step_id=context.step_id,
    )
    adapter.script.extend(
        (
            ConditionObservation(satisfied=True, observed={"ready": True}, evidence={}),
            AssertionObservation(passed=True, evidence={"checked": True}),
            RpaError(ErrorCode.ACTION_FAILED, "안전한 오류"),
            commit,
        )
    )

    condition = adapter.evaluate_condition(
        ConditionSpec(condition_type="fake.ready"), context, CancellationToken()
    )
    assertion = adapter.evaluate_assertion(
        AssertionSpec(assertion_type="fake.equals"),
        None,
        None,
        context,
        CancellationToken(),
    )
    error_result = adapter.execute(supported_request(adapter), context, CancellationToken())
    commit_result = adapter.execute(supported_request(adapter), context, CancellationToken())

    assert condition.satisfied is True
    assert assertion.passed is True
    assert error_result.error_code is ErrorCode.ACTION_FAILED
    assert error_result.safe_message == "안전한 오류"
    assert commit_result.output_commit == commit


def test_raw_exception_is_mapped_without_exposing_message() -> None:
    adapter = FakeAutomationAdapter()
    adapter.script.append(RuntimeError("password=do-not-leak"))

    result = adapter.execute(supported_request(adapter), execution_context(), CancellationToken())

    assert result.error_code is ErrorCode.INTERNAL_ERROR
    assert "do-not-leak" not in result.safe_message
    assert "do-not-leak" not in repr(result.evidence)
    assert adapter.calls == ()


def test_contract_value_objects_deep_freeze_inputs_and_reject_nonfinite_json() -> None:
    parameters: dict[str, object] = {"nested": {"items": [1]}}
    evidence: dict[str, object] = {"nested": {"items": [2]}}
    request = ActionRequest(
        action_type="fake.read",
        target=None,
        parameters=parameters,  # type: ignore[arg-type]
        value=None,
        has_postcondition_or_assertion=False,
    )
    result = AdapterActionResult(output={"ok": True}, evidence=evidence)  # type: ignore[arg-type]
    parameters["nested"] = {"items": [9]}
    evidence["nested"] = {"items": [9]}

    assert request.parameters["nested"] == FrozenMapping.from_mapping({"items": [1]})
    assert result.evidence["nested"] == FrozenMapping.from_mapping({"items": [2]})
    with pytest.raises(ValueError, match="finite"):
        AdapterActionResult(output={"bad": float("nan")}, evidence={})


def test_data_preview_copies_each_row_and_validates_count() -> None:
    source: dict[str, object] = {"name": "before"}
    preview = DataPreview(
        headers=("name",),
        rows=(source,),  # type: ignore[arg-type]
        total_row_count=1,
    )
    source["name"] = "after"

    assert isinstance(preview.rows[0], FrozenMapping)
    assert preview.rows[0]["name"] == "before"
    with pytest.raises(ValueError, match="total row count"):
        DataPreview(headers=("name",), rows=({"name": "one"},), total_row_count=0)


def test_execution_context_copies_mapping_and_row_inputs() -> None:
    variables: dict[str, object] = {"amount": Decimal("12.50")}
    row: dict[str, object] = {"name": "before"}
    context = execution_context()
    copied = type(context)(
        run_id=context.run_id,
        step_id=context.step_id,
        iteration_path=(1,),
        variables=variables,  # type: ignore[arg-type]
        credential_refs={"token": "credential-id"},  # type: ignore[arg-type]
        date_context=context.date_context,
        output_root=context.output_root,
        row_stack=(row,),  # type: ignore[arg-type]
        action_outputs={},
    )
    variables["amount"] = Decimal("99")
    row["name"] = "after"

    assert copied.variables["amount"] == Decimal("12.50")
    assert copied.row_stack[0]["name"] == "before"


def test_secret_value_can_only_be_revealed_explicitly() -> None:
    secret = SecretValue.from_text("top-secret")

    assert repr(secret) == "SecretValue(<redacted>)"
    with pytest.raises(TypeError, match="cannot be converted"):
        str(secret)
    with secret.reveal() as revealed:
        assert revealed == "top-secret"


def test_frozen_contract_objects_cannot_be_reassigned() -> None:
    result = AdapterActionResult(output=None, evidence={})
    with pytest.raises(FrozenInstanceError):
        result.safe_message = "changed"  # type: ignore[misc]


def test_table_output_is_preserved_as_canonical_domain_type() -> None:
    table = TableData(headers=("name",), rows=(("Ada",),))
    result = AdapterActionResult(output=table, evidence={})

    assert result.output is table
    assert isinstance(result.evidence, Mapping)
