from __future__ import annotations

from pathlib import Path
from typing import Protocol

from universal_rpa.domain.workflow import Workflow


class WorkflowRepositoryPort(Protocol):
    def load(self, project_dir: Path) -> Workflow: ...

    def save(
        self,
        project_dir: Path,
        workflow: Workflow,
        expected_revision: int,
    ) -> Workflow: ...


__all__ = ["WorkflowRepositoryPort"]
