from __future__ import annotations

from pathlib import Path

from tests.helpers.validation_fakes import (
    MemorySecrets,
    ValidationSpyAdapter,
    registry_with,
    runtime_environment,
)
from tests.unit.application.test_validation import workflow
from universal_rpa.adapters.tabular import TabularDataSourceProvider
from universal_rpa.application.execution import ExecutionService
from universal_rpa.application.loops import LoopPlanner
from universal_rpa.application.preflight import PreflightService
from universal_rpa.application.run_control import RunControl
from universal_rpa.application.validation import ValidationService
from universal_rpa.application.value_resolution import ValueResolver
from universal_rpa.application.variable_preparation import VariablePreparationService
from universal_rpa.domain.execution import RunInputs, RunRequest
from universal_rpa.infrastructure.checkpoint_store import JsonCheckpointStore
from universal_rpa.infrastructure.execution_journal import JsonExecutionJournalStore


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
