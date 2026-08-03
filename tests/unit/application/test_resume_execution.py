from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

from tests.helpers.validation_fakes import (
    MemorySecrets,
    ValidationSpyAdapter,
    registry_with,
    runtime_environment,
)
from tests.unit.application.test_step_test import _service
from tests.unit.application.test_validation import action_step, workflow
from universal_rpa.adapters.tabular import TabularDataSourceProvider
from universal_rpa.application.loops import LoopPlanner
from universal_rpa.application.resume import ResumeFingerprintBuilder
from universal_rpa.application.run_control import RunControl
from universal_rpa.application.variable_preparation import VariablePreparationService
from universal_rpa.domain.execution import ResumeRequest, RunInputs, RunRequest
from universal_rpa.domain.results import LoopCursor
from universal_rpa.domain.targets import DateContext
from universal_rpa.domain.values import RowBindingValue
from universal_rpa.domain.workflow import InlineDataSource, LoopStep
from universal_rpa.infrastructure.checkpoint_store import Checkpoint, JsonCheckpointStore

LOOP_ID = UUID("00000000-0000-0000-0000-000000000904")
RUN_ID = UUID("00000000-0000-0000-0000-000000000905")


def test_resume_starts_after_exact_matching_loop_cursor(tmp_path: Path) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    project.mkdir()
    output.mkdir()
    source = InlineDataSource(
        data_source_id="rows",
        label="행",
        headers=("factory",),
        rows=(("F-001",), ("F-002",), ("F-003",)),
    )
    row_action = action_step(value=RowBindingValue(template="{{ row.factory }}"))
    loop = LoopStep(
        step_id=LOOP_ID,
        label="행 반복",
        data_source_id="rows",
        steps=(row_action,),
    )
    run_workflow = workflow(loop, data_sources=(source,))  # type: ignore[arg-type]
    inputs = RunInputs(output_directory=output)
    request = RunRequest(
        workflow=run_workflow,
        project_dir=project,
        inputs=inputs,
        resume=ResumeRequest(run_id=RUN_ID),
    )
    provider = TabularDataSourceProvider()
    snapshots = LoopPlanner(provider).materialize_snapshots(project, run_workflow)
    secrets = MemorySecrets(frozenset())
    date_context = DateContext(today=date(2026, 8, 3), run_date=date(2026, 8, 3))
    prepared = VariablePreparationService().prepare(
        run_workflow, inputs, project, date_context, snapshots, secrets
    )
    fingerprint = ResumeFingerprintBuilder().build(
        workflow=run_workflow,
        output_root=output,
        prepared=prepared,
        snapshots=snapshots,
        registry=registry_with(ValidationSpyAdapter()),
        runtime=runtime_environment(),
        secret_store=secrets,
    )
    JsonCheckpointStore(tmp_path / "checkpoints").save_active(
        Checkpoint(
            workflow_id=run_workflow.workflow_id,
            run_id=RUN_ID,
            date_context_today=date_context.today.isoformat(),
            date_context_run_date=date_context.run_date.isoformat(),
            fingerprint=fingerprint,
            completed_cursor=(LoopCursor(loop_step_id=LOOP_ID, row_index=0),),
            updated_at=datetime(2026, 8, 3, tzinfo=UTC),
        )
    )
    adapter = ValidationSpyAdapter()

    report = _service(tmp_path, adapter).run(request, RunControl())

    contexts = [call.payload[1] for call in adapter.calls if call.operation == "execute"]
    assert report.status == "success"
    assert [context.iteration_path for context in contexts] == [(1,), (2,)]
    assert [context.row_stack[-1]["factory"] for context in contexts] == ["F-002", "F-003"]
