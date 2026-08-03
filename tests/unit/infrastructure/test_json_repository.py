from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from universal_rpa.domain.workflow import ActionStep, TargetAppSpec, Workflow
from universal_rpa.infrastructure.json_repository import JsonWorkflowRepository, RevisionConflict

NOW = datetime(2026, 7, 27, tzinfo=UTC)


def workflow(*, name: str = "원본", revision: int = 0) -> Workflow:
    return Workflow(
        workflow_id=UUID("00000000-0000-0000-0000-000000000801"),
        name=name,
        revision=revision,
        target_apps=(
            TargetAppSpec(
                app_id="erp",
                process_executable="erp.exe",
                window_class="ERPMain",
            ),
        ),
        steps=(
            ActionStep(
                step_id=UUID("00000000-0000-0000-0000-000000000802"),
                label="실행",
                action_type="windows.activate_window",
            ),
        ),
        created_at=NOW,
        updated_at=NOW,
    )


def test_stale_revision_never_overwrites_newer_workflow(tmp_path: Path) -> None:
    repository = JsonWorkflowRepository()
    project = tmp_path / "project"
    project.mkdir()
    original = repository.save(project, workflow(), expected_revision=0)
    newer = repository.save(
        project,
        original.model_copy(update={"name": "최신"}),
        expected_revision=original.revision,
    )

    with pytest.raises(RevisionConflict):
        repository.save(
            project,
            original.model_copy(update={"name": "오래된 변경"}),
            expected_revision=original.revision,
        )

    assert repository.load(project).name == "최신"
    assert repository.load(project).revision == newer.revision == 2
    assert not tuple(project.glob("*.tmp"))


def test_save_increments_revision_exactly_once(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    saved = JsonWorkflowRepository().save(project, workflow(revision=99), expected_revision=0)

    assert saved.revision == 1
    assert JsonWorkflowRepository().load(project) == saved
