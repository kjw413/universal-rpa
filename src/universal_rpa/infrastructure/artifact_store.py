"""Per-run local artifacts: one safe report plus masked failure screenshots."""

from __future__ import annotations

import os
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol
from uuid import UUID

from universal_rpa.application.execution import RunActionObserved, RunStarted
from universal_rpa.application.reports import ReportProjector, SafeRunReportDocument
from universal_rpa.domain.results import RunReport
from universal_rpa.domain.targets import RuntimeEnvironment, TargetSpec
from universal_rpa.domain.types import FrozenMapping

DEFAULT_ARTIFACT_RETENTION = timedelta(days=30)


class FailureCapturePort(Protocol):
    def capture_failure(
        self,
        target: TargetSpec | None,
        expected_runtime: RuntimeEnvironment,
        destination: Path,
    ) -> Path | None: ...


@dataclass(frozen=True, slots=True)
class ArtifactRetentionSummary:
    removed: int = 0
    failures: tuple[str, ...] = ()


def _is_link_like(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if is_junction is not None and is_junction():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & 0x400)


class RunArtifactStore:
    """A :class:`RunObserver` that persists only projected, safe artifacts."""

    def __init__(
        self,
        *,
        root: Path,
        projector: ReportProjector | None = None,
        screenshots: FailureCapturePort | None = None,
        output_root: Path | None = None,
    ) -> None:
        self._root = Path(root)
        self._projector = projector or ReportProjector()
        self._screenshots = screenshots
        self._output_root = output_root
        self._started: RunStarted | None = None
        self._workflow_ids: dict[UUID, UUID] = {}
        self._screenshot_paths: list[Path] = []
        self._failure_index = 0

    @property
    def screenshot_paths(self) -> tuple[Path, ...]:
        return tuple(self._screenshot_paths)

    def on_run_started(self, event: RunStarted) -> None:
        self._started = event
        self._workflow_ids[event.run_id] = event.workflow_id
        self._screenshot_paths.clear()
        self._failure_index = 0
        with suppress(OSError):
            self._run_directory(event.workflow_id, event.run_id).mkdir(parents=True, exist_ok=True)

    def on_action_result(self, event: RunActionObserved) -> None:
        if event.result.status not in {"failed", "cancelled"}:
            return
        if self._screenshots is None:
            return
        workflow_id = self._workflow_ids.get(event.result.run_id)
        if workflow_id is None:
            return
        self._failure_index += 1
        destination = (
            self._run_directory(workflow_id, event.result.run_id)
            / f"failure-{self._failure_index:03d}.png"
        )
        try:
            written = self._screenshots.capture_failure(
                event.target, event.runtime, destination
            )
        except Exception:
            return
        if written is not None:
            self._screenshot_paths.append(written)

    def on_run_finished(self, report: RunReport) -> None:
        self._workflow_ids[report.run_id] = report.workflow_id
        document = self.project(report)
        path = self._run_directory(report.workflow_id, report.run_id) / "report.json"
        with suppress(OSError):
            path.parent.mkdir(parents=True, exist_ok=True)
            self._atomic_write(path, document.model_dump_json(indent=2))

    def project(self, report: RunReport) -> SafeRunReportDocument:
        started = self._started
        if started is None or started.run_id != report.run_id:
            started = RunStarted(
                run_id=report.run_id,
                workflow_id=report.workflow_id,
                workflow_name="",
                workflow_revision=report.workflow_revision,
                step_labels=FrozenMapping.empty(),
                started_at=report.started_at,
                runtime=_unknown_runtime(),
            )
        return self._projector.project(started, report, self._output_root)

    def report_path(self, run_id: UUID) -> Path:
        workflow_id = self._workflow_ids.get(run_id)
        if workflow_id is None:
            raise KeyError(run_id)
        return self._run_directory(workflow_id, run_id) / "report.json"

    def _run_directory(self, workflow_id: UUID, run_id: UUID) -> Path:
        return self._root / str(workflow_id) / str(run_id)

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)


def _unknown_runtime() -> RuntimeEnvironment:
    return RuntimeEnvironment(
        interactive_desktop=False,
        process_id=1,
        process_executable="",
        top_level_hwnd=0,
        window_title="",
        window_class="",
        foreground_hwnd=0,
        dpi_x=96,
        dpi_y=96,
        client_width=1,
        client_height=1,
        monitor_scale=1.0,
    )


class ArtifactRetentionService:
    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def prune(
        self,
        now: datetime,
        retention: timedelta = DEFAULT_ARTIFACT_RETENTION,
    ) -> ArtifactRetentionSummary:
        if not self._root.is_dir() or _is_link_like(self._root):
            return ArtifactRetentionSummary()
        cutoff = (now - retention).timestamp()
        removed = 0
        failures: list[str] = []
        for workflow_dir in sorted(self._root.iterdir()):
            if not workflow_dir.is_dir() or _is_link_like(workflow_dir):
                if _is_link_like(workflow_dir):
                    failures.append(str(workflow_dir))
                continue
            removed_here, workflow_failures = self._prune_workflow(workflow_dir, cutoff)
            removed += removed_here
            failures.extend(workflow_failures)
            self._remove_if_empty(workflow_dir)
        return ArtifactRetentionSummary(removed=removed, failures=tuple(failures))

    @staticmethod
    def _prune_workflow(workflow_dir: Path, cutoff: float) -> tuple[int, list[str]]:
        removed = 0
        failures: list[str] = []
        for run_dir in sorted(workflow_dir.iterdir()):
            if _is_link_like(run_dir):
                failures.append(str(run_dir))
                continue
            if not run_dir.is_dir():
                continue
            try:
                modified = run_dir.stat().st_mtime
            except OSError:
                failures.append(str(run_dir))
                continue
            if modified >= cutoff:
                continue
            try:
                _remove_tree(run_dir)
            except OSError:
                failures.append(str(run_dir))
            else:
                removed += 1
        return removed, failures

    @staticmethod
    def _remove_if_empty(directory: Path) -> None:
        with suppress(OSError):
            if not any(directory.iterdir()):
                directory.rmdir()


def _remove_tree(directory: Path) -> None:
    for child in sorted(directory.iterdir()):
        if _is_link_like(child):
            child.unlink()
            continue
        if child.is_dir():
            _remove_tree(child)
        else:
            child.unlink()
    directory.rmdir()


__all__ = [
    "DEFAULT_ARTIFACT_RETENTION",
    "ArtifactRetentionService",
    "ArtifactRetentionSummary",
    "FailureCapturePort",
    "RunArtifactStore",
]
