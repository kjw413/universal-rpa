from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import pytest

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
from universal_rpa.application.variable_preparation import VariablePreparationService
from universal_rpa.domain.errors import ErrorCode
from universal_rpa.domain.execution import RunInputs, RunRequest
from universal_rpa.domain.results import LoopCursor
from universal_rpa.domain.targets import DateContext
from universal_rpa.domain.values import RowBindingValue
from universal_rpa.domain.workflow import InlineDataSource, LoopStep, Workflow
from universal_rpa.infrastructure.checkpoint_store import (
    Checkpoint,
    JsonCheckpointStore,
    ResumeFingerprint,
)
from universal_rpa.infrastructure.execution_journal import (
    InProgressAction,
    InProgressIterationJournal,
    JsonExecutionJournalStore,
)

LOOP_ID = UUID("00000000-0000-0000-0000-000000000914")
RUN_ID = UUID("00000000-0000-0000-0000-000000000915")
DATE_CONTEXT = DateContext(today=date(2026, 8, 3), run_date=date(2026, 8, 3))
UPDATED_AT = datetime(2026, 8, 3, tzinfo=UTC)


def _loop_workflow() -> Workflow:
    source = InlineDataSource(
        data_source_id="rows",
        label="행",
        headers=("factory",),
        rows=(("F-001",), ("F-002",), ("F-003",)),
    )
    loop = LoopStep(
        step_id=LOOP_ID,
        label="행 반복",
        data_source_id="rows",
        steps=(action_step(value=RowBindingValue(template="{{ row.factory }}")),),
    )
    return workflow(loop, data_sources=(source,))  # type: ignore[arg-type]


def _request(tmp_path: Path) -> RunRequest:
    project = tmp_path / "project"
    output = tmp_path / "output"
    project.mkdir(exist_ok=True)
    output.mkdir(exist_ok=True)
    return RunRequest(
        workflow=_loop_workflow(),
        project_dir=project,
        inputs=RunInputs(output_directory=output),
    )


def _fingerprint(request: RunRequest) -> ResumeFingerprint:
    provider = TabularDataSourceProvider()
    snapshots = LoopPlanner(provider).materialize_snapshots(request.project_dir, request.workflow)
    secrets = MemorySecrets(frozenset())
    prepared = VariablePreparationService().prepare(
        request.workflow,
        request.inputs,
        request.project_dir,
        DATE_CONTEXT,
        snapshots,
        secrets,
    )
    return ResumeFingerprintBuilder().build(
        workflow=request.workflow,
        output_root=request.inputs.output_directory,
        prepared=prepared,
        snapshots=snapshots,
        registry=registry_with(ValidationSpyAdapter()),
        runtime=runtime_environment(),
        secret_store=secrets,
    )


def _save_checkpoint(
    tmp_path: Path,
    request: RunRequest,
    fingerprint: ResumeFingerprint,
    *,
    today: str = "2026-08-03",
) -> None:
    JsonCheckpointStore(tmp_path / "checkpoints").save_active(
        Checkpoint(
            workflow_id=request.workflow.workflow_id,
            run_id=RUN_ID,
            date_context_today=today,
            date_context_run_date="2026-08-03",
            fingerprint=fingerprint,
            completed_cursor=(LoopCursor(loop_step_id=LOOP_ID, row_index=0),),
            updated_at=UPDATED_AT,
        )
    )


def test_discovery_reports_a_matching_checkpoint_as_resumable(tmp_path: Path) -> None:
    request = _request(tmp_path)
    _save_checkpoint(tmp_path, request, _fingerprint(request))

    found = _service(tmp_path, ValidationSpyAdapter()).discover_resumable(request)

    assert len(found) == 1
    assert found[0].resumable is True
    assert found[0].run_id == RUN_ID
    assert found[0].error_code is None
    assert found[0].completed_cursor == (LoopCursor(loop_step_id=LOOP_ID, row_index=0),)
    assert found[0].mismatch_fields == ()


def test_interrupted_non_idempotent_iteration_is_unsafe_and_not_a_mismatch(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    _save_checkpoint(tmp_path, request, _fingerprint(request))
    JsonExecutionJournalStore(tmp_path / "journals").save(
        InProgressIterationJournal(
            workflow_id=request.workflow.workflow_id,
            run_id=RUN_ID,
            cursor=(LoopCursor(loop_step_id=LOOP_ID, row_index=1),),
            actions=(
                InProgressAction(
                    step_id=request.workflow.steps[0].steps[0].step_id,  # type: ignore[union-attr]
                    action_type="fake.write",
                    idempotent=False,
                    state="inflight",
                ),
            ),
            started_at=UPDATED_AT,
            updated_at=UPDATED_AT,
        )
    )

    found = _service(tmp_path, ValidationSpyAdapter()).discover_resumable(request)

    assert found[0].resumable is False
    assert found[0].error_code is ErrorCode.RESUME_UNSAFE
    assert "수동" in found[0].safe_message


@pytest.mark.parametrize(
    "mismatch", ["workflow", "inputs", "data", "adapter", "environment", "output"]
)
def test_every_fingerprint_difference_is_reported_as_resume_mismatch(
    tmp_path: Path, mismatch: str
) -> None:
    request = _request(tmp_path)
    stale = _mutate(_fingerprint(request), mismatch)
    _save_checkpoint(tmp_path, request, stale)

    found = _service(tmp_path, ValidationSpyAdapter()).discover_resumable(request)

    assert found[0].resumable is False
    assert found[0].error_code is ErrorCode.RESUME_MISMATCH
    assert found[0].mismatch_fields == (mismatch,)


def test_unreadable_checkpoint_date_context_is_checkpoint_invalid(tmp_path: Path) -> None:
    request = _request(tmp_path)
    _save_checkpoint(tmp_path, request, _fingerprint(request), today="not-a-date")

    found = _service(tmp_path, ValidationSpyAdapter()).discover_resumable(request)

    assert found[0].resumable is False
    assert found[0].error_code is ErrorCode.CHECKPOINT_INVALID


def test_discovery_returns_nothing_without_a_stored_checkpoint(tmp_path: Path) -> None:
    request = _request(tmp_path)

    assert _service(tmp_path, ValidationSpyAdapter()).discover_resumable(request) == ()


_OTHER_DIGEST = "f" * 64


def _mutate(fingerprint: ResumeFingerprint, mismatch: str) -> ResumeFingerprint:
    if mismatch == "data":
        stale = fingerprint.data_sources[0].model_copy(update={"row_count": 99})
        return fingerprint.model_copy(
            update={"data_sources": (stale, *fingerprint.data_sources[1:])}
        )
    if mismatch == "adapter":
        stale = fingerprint.adapters[0].model_copy(update={"implementation_version": "0.0"})
        return fingerprint.model_copy(update={"adapters": (stale, *fingerprint.adapters[1:])})
    field = {
        "workflow": "workflow_sha256",
        "inputs": "resolved_inputs_sha256",
        "environment": "environment_sha256",
        "output": "output_root_sha256",
    }[mismatch]
    return fingerprint.model_copy(update={field: _OTHER_DIGEST})
