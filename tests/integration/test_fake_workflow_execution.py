from __future__ import annotations

from pathlib import Path

from tests.helpers.validation_fakes import (
    MemorySecrets,
    ValidationSpyAdapter,
    registry_with,
    runtime_environment,
)
from tests.unit.application.test_validation import action_step, workflow
from universal_rpa.adapters.tabular import TabularDataSourceProvider
from universal_rpa.application.execution import ExecutionService
from universal_rpa.application.loops import LoopPlanner
from universal_rpa.application.preflight import PreflightService
from universal_rpa.application.run_control import RunControl
from universal_rpa.application.validation import ValidationService
from universal_rpa.application.value_resolution import ValueResolver
from universal_rpa.application.variable_preparation import VariablePreparationService
from universal_rpa.domain.errors import ErrorCode
from universal_rpa.domain.execution import RunInputs, RunRequest
from universal_rpa.infrastructure.checkpoint_store import JsonCheckpointStore
from universal_rpa.infrastructure.execution_journal import JsonExecutionJournalStore


def _service(tmp_path: Path) -> ExecutionService:
    registry = registry_with(ValidationSpyAdapter())
    secrets = MemorySecrets(frozenset())
    return ExecutionService(
        preflight=PreflightService(
            ValidationService(registry=registry, data_sources=TabularDataSourceProvider()),
            lambda _: runtime_environment(),
        ),
        registry=registry,
        loop_planner=LoopPlanner(TabularDataSourceProvider()),
        variable_preparation=VariablePreparationService(),
        value_resolver=ValueResolver(secrets),
        secret_store=secrets,
        checkpoints=JsonCheckpointStore(tmp_path / "checkpoints"),
        journals=JsonExecutionJournalStore(tmp_path / "journals"),
    )


def test_a_preflight_rejection_reports_why_it_was_rejected(tmp_path: Path) -> None:
    """A run stopped by preflight must carry the reason preflight computed.

    Preflight produces a precise, already-safe issue -- an unsupported action,
    an incompatible assertion -- and that is the only thing telling an operator
    what to change. Reporting a generic internal error instead sends them
    looking for a product fault over a workflow they can fix.
    """

    project = tmp_path / "project"
    output = tmp_path / "output"
    project.mkdir()
    output.mkdir()

    report = _service(tmp_path).run(
        RunRequest(
            workflow=workflow(action_step(action_type="fake.not_a_real_action")),
            project_dir=project,
            inputs=RunInputs(output_directory=output),
        ),
        RunControl(),
    )

    assert report.status == "failed"
    assert report.error_code is ErrorCode.ACTION_UNSUPPORTED
    assert report.safe_message != "실행을 시작할 수 없습니다."


def test_validated_fake_workflow_runs_without_native_windows_input(tmp_path: Path) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    project.mkdir()
    output.mkdir()
    adapter = ValidationSpyAdapter()
    registry = registry_with(adapter)
    secrets = MemorySecrets(frozenset())
    service = ExecutionService(
        preflight=PreflightService(
            ValidationService(registry=registry, data_sources=TabularDataSourceProvider()),
            lambda _: runtime_environment(),
        ),
        registry=registry,
        loop_planner=LoopPlanner(TabularDataSourceProvider()),
        variable_preparation=VariablePreparationService(),
        value_resolver=ValueResolver(secrets),
        secret_store=secrets,
        checkpoints=JsonCheckpointStore(tmp_path / "checkpoints"),
        journals=JsonExecutionJournalStore(tmp_path / "journals"),
    )

    report = service.run(
        RunRequest(
            workflow=workflow(), project_dir=project, inputs=RunInputs(output_directory=output)
        ),
        RunControl(),
    )

    assert report.status == "success", report
    assert len(report.results) == 1
    assert report.results[0].status == "success"
