from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from universal_rpa.domain.workflow import (
    ActionStep,
    ProjectRelativePath,
    TargetAppSpec,
    Workflow,
)
from universal_rpa.infrastructure.json_repository import JsonWorkflowRepository
from universal_rpa.ports.repositories import WorkflowRepositoryPort


class ProjectBoundaryError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ProjectSession:
    project_dir: Path
    workflow: Workflow
    loaded_revision: int
    dirty: bool


class ProjectService:
    def __init__(self, repository: WorkflowRepositoryPort | None = None) -> None:
        self._repository = repository or JsonWorkflowRepository()

    def create(self, project_dir: Path, name: str) -> ProjectSession:
        if not name.strip():
            raise ValueError("프로젝트 이름을 입력하세요.")
        project = Path(project_dir).resolve(strict=False)
        if project.exists() and any(project.iterdir()):
            raise ProjectBoundaryError("비어 있지 않은 폴더에는 프로젝트를 만들 수 없습니다.")
        project.mkdir(parents=True, exist_ok=True)
        if self._is_link_like(project):
            raise ProjectBoundaryError("연결된 폴더에는 프로젝트를 만들 수 없습니다.")
        (project / "targets").mkdir(exist_ok=True)
        (project / "inputs").mkdir(exist_ok=True)
        now = datetime.now(UTC)
        workflow = Workflow(
            workflow_id=uuid4(),
            name=name.strip(),
            revision=0,
            target_apps=(
                TargetAppSpec(
                    app_id="target_app",
                    process_executable="선택 필요",
                    window_class="선택 필요",
                ),
            ),
            steps=(
                ActionStep(
                    step_id=uuid4(),
                    label="첫 단계",
                    enabled=False,
                    action_type="windows.activate_window",
                ),
            ),
            created_at=now,
            updated_at=now,
        )
        saved = self._repository.save(project, workflow, expected_revision=0)
        return ProjectSession(project, saved, saved.revision, False)

    def open(self, project_dir: Path) -> ProjectSession:
        project = Path(project_dir).resolve(strict=True)
        workflow = self._repository.load(project)
        return ProjectSession(project, workflow, workflow.revision, False)

    def save(self, session: ProjectSession) -> ProjectSession:
        saved = self._repository.save(
            session.project_dir,
            session.workflow,
            expected_revision=session.loaded_revision,
        )
        return ProjectSession(session.project_dir, saved, saved.revision, False)

    def with_workflow(self, session: ProjectSession, workflow: Workflow) -> ProjectSession:
        return ProjectSession(session.project_dir, workflow, session.loaded_revision, True)

    def import_input_file(
        self,
        session: ProjectSession,
        source: Path,
    ) -> ProjectRelativePath:
        source_path = Path(source)
        if self._is_link_like(source_path):
            raise ProjectBoundaryError("연결된 입력 파일은 가져올 수 없습니다.")
        try:
            source_path = source_path.resolve(strict=True)
            initial_stat = source_path.stat()
        except (FileNotFoundError, OSError):
            raise ProjectBoundaryError("입력 파일을 찾을 수 없습니다.") from None
        if not source_path.is_file() or source_path.suffix.casefold() not in {".csv", ".xlsx"}:
            raise ProjectBoundaryError("CSV 또는 XLSX 일반 파일만 가져올 수 있습니다.")

        inputs_dir = session.project_dir / "inputs"
        self._require_safe_directory(session.project_dir, inputs_dir)
        safe_name = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", source_path.name).strip("._")
        if not safe_name:
            safe_name = f"input{source_path.suffix.casefold()}"
        temporary = inputs_dir / f".import-{uuid4().hex}.tmp"
        digest = hashlib.sha256()
        size = 0
        try:
            with source_path.open("rb") as source_stream, temporary.open("xb") as target_stream:
                while chunk := source_stream.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
                    target_stream.write(chunk)
                target_stream.flush()
                os.fsync(target_stream.fileno())
            final_stat = source_path.stat()
            if (
                size != initial_stat.st_size
                or final_stat.st_size != initial_stat.st_size
                or final_stat.st_mtime_ns != initial_stat.st_mtime_ns
            ):
                raise ProjectBoundaryError("가져오는 동안 입력 파일이 변경되었습니다.")
            copied_digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
            if copied_digest != digest.hexdigest() or temporary.stat().st_size != size:
                raise ProjectBoundaryError("가져온 입력 파일 검증에 실패했습니다.")
            filename = f"{digest.hexdigest()[:12]}-{safe_name}"
            final_path = inputs_dir / filename
            if final_path.exists():
                if (
                    final_path.stat().st_size != size
                    or hashlib.sha256(final_path.read_bytes()).hexdigest() != digest.hexdigest()
                ):
                    raise ProjectBoundaryError("같은 이름의 다른 입력 파일이 이미 있습니다.")
            else:
                os.replace(temporary, final_path)
            return ProjectRelativePath(f"inputs/{filename}")
        finally:
            if temporary.exists():
                temporary.unlink()

    def resolve_input(
        self,
        session: ProjectSession,
        relative: ProjectRelativePath,
    ) -> Path:
        try:
            resolved = relative.resolve_under(session.project_dir)
        except ValueError as error:
            raise ProjectBoundaryError("입력 경로가 프로젝트 경계를 벗어났습니다.") from error
        if self._is_link_like(resolved) or not resolved.is_file():
            raise ProjectBoundaryError("안전한 프로젝트 입력 파일이 아닙니다.")
        return resolved

    @classmethod
    def _require_safe_directory(cls, project: Path, directory: Path) -> None:
        if cls._is_link_like(project) or cls._is_link_like(directory):
            raise ProjectBoundaryError("프로젝트 경로에 연결된 폴더가 있습니다.")
        resolved_project = project.resolve(strict=True)
        resolved_directory = directory.resolve(strict=True)
        if not resolved_directory.is_relative_to(resolved_project):
            raise ProjectBoundaryError("프로젝트 경계를 벗어난 폴더입니다.")

    @staticmethod
    def _is_link_like(path: Path) -> bool:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction is not None and is_junction())


__all__ = ["ProjectBoundaryError", "ProjectService", "ProjectSession"]
