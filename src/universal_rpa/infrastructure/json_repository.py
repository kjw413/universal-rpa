from __future__ import annotations

import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from universal_rpa.application.workflow_codec import dump_workflow, load_workflow
from universal_rpa.domain.workflow import Workflow


class WorkflowRepositoryError(RuntimeError):
    pass


class RevisionConflict(WorkflowRepositoryError):
    pass


def _is_link_like(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction is not None and is_junction())


class JsonWorkflowRepository:
    _write_lock = threading.Lock()

    def load(self, project_dir: Path) -> Workflow:
        project = self._safe_project_dir(project_dir)
        workflow_path = project / "workflow.json"
        if _is_link_like(workflow_path):
            raise WorkflowRepositoryError("프로젝트 workflow 파일이 안전하지 않습니다.")
        try:
            payload = workflow_path.read_bytes()
            return load_workflow(payload)
        except FileNotFoundError:
            raise WorkflowRepositoryError("프로젝트 workflow 파일을 찾을 수 없습니다.") from None
        except (OSError, TypeError, ValueError):
            raise WorkflowRepositoryError("프로젝트 workflow 파일을 읽을 수 없습니다.") from None

    def save(
        self,
        project_dir: Path,
        workflow: Workflow,
        expected_revision: int,
    ) -> Workflow:
        if expected_revision < 0:
            raise ValueError("expected_revision must be nonnegative")
        project = self._safe_project_dir(project_dir)
        workflow_path = project / "workflow.json"
        if _is_link_like(workflow_path):
            raise WorkflowRepositoryError("프로젝트 workflow 파일이 안전하지 않습니다.")

        with self._write_lock:
            if workflow_path.exists():
                current = self.load(project)
                current_revision = current.revision
            else:
                current_revision = 0
            if current_revision != expected_revision:
                raise RevisionConflict("다른 변경이 먼저 저장되어 프로젝트를 다시 열어야 합니다.")

            saved = Workflow.model_validate(
                workflow.model_dump(
                    mode="python",
                    exclude={"revision", "updated_at"},
                )
                | {
                    "revision": expected_revision + 1,
                    "updated_at": datetime.now(UTC),
                }
            )
            temporary = project / f".workflow.json.{uuid4().hex}.tmp"
            try:
                with temporary.open("xb") as stream:
                    stream.write(dump_workflow(saved))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, workflow_path)
            except OSError as error:
                raise WorkflowRepositoryError("프로젝트를 저장하지 못했습니다.") from error
            finally:
                if temporary.exists():
                    temporary.unlink()
            return saved

    @staticmethod
    def _safe_project_dir(project_dir: Path) -> Path:
        project = Path(project_dir)
        if _is_link_like(project):
            raise WorkflowRepositoryError("프로젝트 폴더가 안전하지 않습니다.")
        try:
            resolved = project.resolve(strict=True)
        except (FileNotFoundError, OSError):
            raise WorkflowRepositoryError("프로젝트 폴더를 찾을 수 없습니다.") from None
        if not resolved.is_dir():
            raise WorkflowRepositoryError("프로젝트 경로가 폴더가 아닙니다.")
        return resolved


__all__ = ["JsonWorkflowRepository", "RevisionConflict", "WorkflowRepositoryError"]
