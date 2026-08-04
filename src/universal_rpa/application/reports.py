"""Projection of a completed run into a deeply frozen, redacted document.

The projector is the only supported way to expose run outcomes outside the
process.  It keeps identifiers, counts, digests, and curated safe messages, and
drops selectors, typed input, clipboard bodies, credentials, window titles, and
absolute customer paths.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Annotated
from uuid import UUID

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    WithJsonSchema,
)

from universal_rpa.application.execution import RunStarted
from universal_rpa.domain.results import (
    ActionResult,
    LoopCursor,
    OutputCommit,
    RunReport,
    RunStatus,
)
from universal_rpa.domain.targets import RuntimeEnvironment
from universal_rpa.domain.types import (
    FrozenJsonObject,
    FrozenMapping,
    JsonValue,
    thaw_json,
)
from universal_rpa.infrastructure.redaction import redact_evidence

SafeJsonObject = Annotated[
    FrozenJsonObject,
    BeforeValidator(redact_evidence),
    PlainSerializer(thaw_json, return_type=dict[str, JsonValue]),
    WithJsonSchema({"type": "object", "additionalProperties": True}),
]


class SafeRunReportDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    report_schema_version: str = "1"
    run_id: UUID
    workflow_id: UUID
    workflow_name: str
    workflow_revision: int = Field(ge=0)
    status: RunStatus
    started_at: datetime
    finished_at: datetime
    environment: SafeJsonObject
    total_iterations: int = Field(ge=0)
    successful_iterations: int = Field(ge=0)
    failed_iterations: int = Field(ge=0)
    skipped_iterations: int = Field(ge=0)
    action_count: int = Field(ge=0)
    outputs: tuple[SafeJsonObject, ...] = ()
    failures: tuple[SafeJsonObject, ...] = ()
    last_checkpoint: str | None = None
    error_code: str | None = None
    safe_message: str = ""


def _cursor_label(cursor: Sequence[LoopCursor]) -> str | None:
    if not cursor:
        return None
    return "/".join(f"{item.loop_step_id}#{item.row_index}" for item in cursor)


def _cursor_json(cursor: Sequence[LoopCursor]) -> tuple[JsonValue, ...]:
    return tuple(
        {"loop_step_id": str(item.loop_step_id), "row_index": item.row_index} for item in cursor
    )


def _safe_environment(runtime: RuntimeEnvironment) -> dict[str, JsonValue]:
    return {
        "interactive_desktop": runtime.interactive_desktop,
        "process_executable": Path(runtime.process_executable).name,
        "window_class": runtime.window_class,
        "dpi_x": runtime.dpi_x,
        "dpi_y": runtime.dpi_y,
        "client_width": runtime.client_width,
        "client_height": runtime.client_height,
        "monitor_scale": runtime.monitor_scale,
    }


def _relative_output_path(destination: Path, output_root: Path | None) -> str:
    if output_root is not None:
        try:
            relative = destination.resolve().relative_to(Path(output_root).resolve())
        except (OSError, ValueError):
            return destination.name
        return relative.as_posix()
    return destination.name


class ReportProjector:
    def project(
        self,
        started: RunStarted,
        report: RunReport,
        output_root: Path | None = None,
    ) -> SafeRunReportDocument:
        successful, failed, skipped = self._iteration_counts(report.results)
        total = report.total_iterations
        if total is None:
            total = successful + failed + skipped
        return SafeRunReportDocument(
            run_id=report.run_id,
            workflow_id=report.workflow_id,
            workflow_name=started.workflow_name,
            workflow_revision=report.workflow_revision,
            status=report.status,
            started_at=report.started_at,
            finished_at=report.finished_at,
            environment=_safe_environment(started.runtime),  # type: ignore[arg-type]
            total_iterations=total,
            successful_iterations=successful,
            failed_iterations=failed,
            skipped_iterations=skipped,
            action_count=len(report.results),
            outputs=tuple(
                self._output(commit, output_root)  # type: ignore[misc]
                for commit in report.output_commits
            ),
            failures=tuple(
                self._failure(result, started.step_labels)  # type: ignore[misc]
                for result in report.results
                if result.status in {"failed", "cancelled"}
            ),
            last_checkpoint=_cursor_label(report.last_checkpoint_cursor or ()),
            error_code=report.error_code.value if report.error_code is not None else None,
            safe_message=report.safe_message,
        )

    @staticmethod
    def _iteration_counts(results: Sequence[ActionResult]) -> tuple[int, int, int]:
        states: dict[tuple[LoopCursor, ...], str] = {}
        for result in results:
            cursor = result.iteration_cursor
            current = states.get(cursor, "success")
            if result.status in {"failed", "cancelled"}:
                states[cursor] = "failed"
            elif result.skip_reason == "skip_iteration" and current != "failed":
                states[cursor] = "skipped"
            else:
                states.setdefault(cursor, "success")
        successful = sum(1 for state in states.values() if state == "success")
        failed = sum(1 for state in states.values() if state == "failed")
        skipped = sum(1 for state in states.values() if state == "skipped")
        return successful, failed, skipped

    @staticmethod
    def _output(commit: OutputCommit, output_root: Path | None) -> dict[str, JsonValue]:
        return {
            "relative_path": _relative_output_path(commit.destination, output_root),
            "format": commit.format,
            "sheet_name": commit.sheet_name,
            "row_count": commit.row_count,
            "sha256": commit.sha256,
            "headers_sha256": commit.headers_sha256,
            "committed": commit.committed,
            "producer_step_id": str(commit.producer_step_id),
            "producer_cursor": list(_cursor_json(commit.producer_cursor)),
        }

    @staticmethod
    def _failure(
        result: ActionResult,
        step_labels: FrozenMapping[UUID, str],
    ) -> dict[str, JsonValue]:
        return {
            "step_id": str(result.step_id),
            "step_label": step_labels.get(result.step_id, ""),
            "iteration_cursor": list(_cursor_json(result.iteration_cursor)),
            "status": result.status,
            "attempt_count": result.attempt_count,
            "error_code": result.error_code.value if result.error_code is not None else None,
            "safe_message": result.safe_message,
            "evidence": thaw_json(result.evidence),
        }


__all__ = ["ReportProjector", "SafeJsonObject", "SafeRunReportDocument"]
