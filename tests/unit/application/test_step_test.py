from __future__ import annotations

from pathlib import Path
from uuid import UUID

from tests.helpers.validation_fakes import (
    MemorySecrets,
    ValidationSpyAdapter,
    registry_with,
    runtime_environment,
)
from tests.unit.application.test_validation import action_step, workflow
from universal_rpa.adapters.tabular import TabularDataSourceProvider
from universal_rpa.application.execution import ExecutionService, StepTestRequest
from universal_rpa.application.loops import LoopPlanner
from universal_rpa.application.preflight import PreflightService
from universal_rpa.application.run_control import RunControl
from universal_rpa.application.validation import ValidationService
from universal_rpa.application.value_resolution import ValueResolver
from universal_rpa.application.variable_preparation import VariablePreparationService
from universal_rpa.domain.execution import RunInputs, RunRequest
from universal_rpa.infrastructure.checkpoint_store import JsonCheckpointStore
from universal_rpa.infrastructure.execution_journal import JsonExecutionJournalStore

FIRST_ID = UUID("00000000-0000-0000-0000-000000000901")
SECOND_ID = UUID("00000000-0000-0000-0000-000000000902")


def _service(tmp_path: Path, adapter: ValidationSpyAdapter) -> ExecutionService:
    registry = registry_with(adapter)
    data_sources = TabularDataSourceProvider()
    secrets = MemorySecrets(frozenset())
    return ExecutionService(
        preflight=PreflightService(
            ValidationService(registry=registry, data_sources=data_sources),
            lambda _: runtime_environment(),
        ),
        registry=registry,
        loop_planner=LoopPlanner(data_sources),
        variable_preparation=VariablePreparationService(),
        value_resolver=ValueResolver(secrets),
        secret_store=secrets,
        checkpoints=JsonCheckpointStore(tmp_path / "checkpoints"),
        journals=JsonExecutionJournalStore(tmp_path / "journals"),
    )


def test_step_test_executes_only_selected_action_and_writes_no_run_state(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    project.mkdir()
    output.mkdir()
    first = action_step().model_copy(update={"step_id": FIRST_ID, "label": "첫 단계"})
    second = action_step().model_copy(update={"step_id": SECOND_ID, "label": "둘째 단계"})
    adapter = ValidationSpyAdapter()
    service = _service(tmp_path, adapter)
    request = RunRequest(
        workflow=workflow(first, second),
        project_dir=project,
        inputs=RunInputs(output_directory=output),
    )

    result = service.test_step(
        StepTestRequest(run_request=request, step_id=SECOND_ID), RunControl()
    )

    execute_calls = [call for call in adapter.calls if call.operation == "execute"]
    assert result.step_id == SECOND_ID
    assert result.status == "success"
    assert len(execute_calls) == 1
    assert not tuple((tmp_path / "checkpoints").rglob("*.json"))
    assert not tuple((tmp_path / "journals").rglob("*.json"))
