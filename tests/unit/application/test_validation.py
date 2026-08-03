from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from tests.helpers.validation_fakes import (
    MemoryDataSources,
    MemorySecrets,
    ValidationSpyAdapter,
    descriptor,
    fake_target,
    registry_with,
    runtime_environment,
    successful_assertion,
)
from universal_rpa.adapters.registry import AdapterRegistry
from universal_rpa.application.validation import ValidationContext, ValidationService
from universal_rpa.domain.conditions import ConditionSpec, WaitSpec
from universal_rpa.domain.errors import ErrorCode, ValidationReport
from universal_rpa.domain.types import DataCell, FrozenMapping
from universal_rpa.domain.values import (
    CredentialSource,
    DataColumnSource,
    LiteralValue,
    RunInputSource,
    SecretRefValue,
    VariableDefinition,
)
from universal_rpa.domain.workflow import (
    ActionStep,
    CsvDataSource,
    FailurePolicy,
    IfPresentStep,
    PresenceSpec,
    ProjectRelativePath,
    TargetAppSpec,
    Workflow,
)
from universal_rpa.ports.data_sources import DataPreview

NOW = datetime(2026, 7, 27, tzinfo=UTC)
STEP_ID = UUID("00000000-0000-0000-0000-000000000821")


def action_step(
    *,
    action_type: str = "fake.read",
    target_matches: int = 1,
    verified: bool = True,
    failure_policy: FailurePolicy | None = None,
    value: LiteralValue | SecretRefValue | None = None,
    postcondition: WaitSpec | None = None,
) -> ActionStep:
    return ActionStep(
        step_id=STEP_ID,
        label="조회",
        action_type=action_type,
        target=fake_target(matches=target_matches),
        value=value,
        failure_policy=failure_policy or FailurePolicy(),
        postcondition=postcondition,
        assertions=(successful_assertion(),) if verified else (),
    )


def workflow(
    *steps: ActionStep | IfPresentStep,
    variables: tuple[VariableDefinition, ...] = (),
    data_sources: tuple[CsvDataSource, ...] = (),
) -> Workflow:
    return Workflow(
        workflow_id=UUID("00000000-0000-0000-0000-000000000820"),
        name="검증 테스트",
        revision=1,
        target_apps=(
            TargetAppSpec(
                app_id="fake_app",
                process_executable="fake.exe",
                window_class="FakeWindow",
            ),
        ),
        variables=variables,
        data_sources=data_sources,
        steps=steps or (action_step(),),
        created_at=NOW,
        updated_at=NOW,
    )


def context(tmp_path: Path, *, values: Mapping[str, DataCell] | None = None) -> ValidationContext:
    project = tmp_path / "project"
    output = tmp_path / "output"
    project.mkdir(exist_ok=True)
    output.mkdir(exist_ok=True)
    return ValidationContext(
        project_dir=project,
        runtime=runtime_environment(),
        variable_values=values or {},
        output_root=output,
    )


def issue_codes(report: ValidationReport) -> set[ErrorCode]:
    return {issue.code for issue in report.errors}


def test_missing_adapter_is_an_error() -> None:
    report = ValidationService(registry=AdapterRegistry()).validate_static(workflow())

    assert issue_codes(report) == {ErrorCode.ADAPTER_MISSING}


def test_validation_never_executes_an_action(tmp_path: Path) -> None:
    adapter = ValidationSpyAdapter()
    report = ValidationService(registry=registry_with(adapter)).validate_environment(
        workflow(),
        context(tmp_path),
    )

    assert report.is_valid
    assert not any(call.operation == "execute" for call in adapter.calls)
    assert adapter.validated_action_specs == [workflow().steps[0]]


def test_verification_and_retry_authority_come_from_descriptor() -> None:
    adapter = ValidationSpyAdapter(descriptor(idempotent=False))
    retry = FailurePolicy(mode="retry", retry_count=1)

    report = ValidationService(registry=registry_with(adapter)).validate_static(
        workflow(action_step(verified=False, failure_policy=retry))
    )

    assert ErrorCode.INVALID_SCHEMA in issue_codes(report)
    assert any("완료 조건" in issue.safe_message for issue in report.errors)
    assert any("재시도" in issue.safe_message for issue in report.errors)


def test_intrinsic_action_does_not_require_user_assertion() -> None:
    adapter = ValidationSpyAdapter(descriptor(action="fake.atomic_save", verification="intrinsic"))
    step = action_step(action_type="fake.atomic_save", verified=False)

    report = ValidationService(registry=registry_with(adapter)).validate_static(workflow(step))

    assert report.is_valid


def test_assertion_must_be_compatible_with_action() -> None:
    adapter = ValidationSpyAdapter(descriptor(compatible_assertions=frozenset()))

    report = ValidationService(registry=registry_with(adapter)).validate_static(workflow())

    assert ErrorCode.INVALID_SCHEMA in issue_codes(report)
    assert any(issue.path.endswith("assertion_type") for issue in report.errors)


def test_if_present_zero_match_is_optional_but_child_is_deferred(tmp_path: Path) -> None:
    adapter = ValidationSpyAdapter()
    optional = IfPresentStep(
        step_id=UUID("00000000-0000-0000-0000-000000000822"),
        label="있을 때만",
        condition=PresenceSpec(
            condition_type="fake.element_exists",
            target=fake_target(matches=0),
            timeout_ms=1_000,
        ),
        steps=(action_step(target_matches=0),),
    )

    report = ValidationService(registry=registry_with(adapter)).validate_environment(
        workflow(optional),
        context(tmp_path),
    )

    assert report.is_valid
    assert adapter.validation_modes == ["may_be_absent_now", "deferred"]


def test_wait_target_may_be_absent_before_execution(tmp_path: Path) -> None:
    adapter = ValidationSpyAdapter()
    delayed = WaitSpec(
        condition=ConditionSpec(
            condition_type="fake.ready",
            target=fake_target(matches=0),
            expected=True,
        ),
        timeout_ms=1_000,
    )

    report = ValidationService(registry=registry_with(adapter)).validate_environment(
        workflow(action_step(postcondition=delayed)),
        context(tmp_path),
    )

    assert report.is_valid
    assert adapter.validation_modes == ["must_exist_now", "deferred"]


def test_immediate_target_ambiguity_is_an_error(tmp_path: Path) -> None:
    adapter = ValidationSpyAdapter()

    report = ValidationService(registry=registry_with(adapter)).validate_environment(
        workflow(action_step(target_matches=2)),
        context(tmp_path),
    )

    assert ErrorCode.TARGET_AMBIGUOUS in issue_codes(report)


def test_context_defensively_copies_variable_values(tmp_path: Path) -> None:
    values = {"query_date": "2026-07-27"}
    validation_context = context(tmp_path, values=values)
    values["query_date"] = "mutated"

    assert validation_context.variable_values["query_date"] == "2026-07-27"


def test_required_variable_and_secret_are_checked(tmp_path: Path) -> None:
    variables = (
        VariableDefinition(
            variable_id="query_date",
            label="조회일",
            value_type="date",
            source=RunInputSource(required=True),
        ),
        VariableDefinition(
            variable_id="password",
            label="비밀번호",
            value_type="secret",
            source=CredentialSource(credential_ref="erp/password"),
        ),
    )
    adapter = ValidationSpyAdapter()

    report = ValidationService(
        registry=registry_with(adapter),
        secret_store=MemorySecrets(frozenset()),
    ).validate_environment(
        workflow(
            action_step(value=SecretRefValue(credential_ref="erp/missing")),
            variables=variables,
        ),
        context(tmp_path),
    )

    assert ErrorCode.SECRET_MISSING in issue_codes(report)
    assert any("필수 실행 변수" in issue.safe_message for issue in report.errors)


def test_column_source_requires_preview_header(tmp_path: Path) -> None:
    source = CsvDataSource(
        data_source_id="orders",
        label="주문",
        path=ProjectRelativePath("inputs/orders.csv"),
        encoding="utf-8",
    )
    variable = VariableDefinition(
        variable_id="factory",
        label="공장",
        value_type="choice",
        source=DataColumnSource(
            source_type="csv_column",
            data_source_id="orders",
            column_name="factory",
        ),
    )
    previews = MemoryDataSources(
        {
            "orders": DataPreview(
                headers=("order_id",),
                rows=(FrozenMapping((("order_id", "A-1"),)),),
                total_row_count=1,
            )
        }
    )

    report = ValidationService(
        registry=registry_with(ValidationSpyAdapter()),
        data_sources=previews,
    ).validate_environment(
        workflow(variables=(variable,), data_sources=(source,)),
        context(tmp_path),
    )

    assert ErrorCode.DATA_SOURCE_INVALID in issue_codes(report)
    assert previews.preview_calls == ["orders"]


def test_unavailable_output_root_fails_closed(tmp_path: Path) -> None:
    adapter = ValidationSpyAdapter()
    validation_context = ValidationContext(
        project_dir=tmp_path,
        runtime=runtime_environment(),
        variable_values={},
        output_root=tmp_path / "missing-output",
    )

    report = ValidationService(registry=registry_with(adapter)).validate_environment(
        workflow(),
        validation_context,
    )

    assert ErrorCode.OUTPUT_UNAVAILABLE in issue_codes(report)
