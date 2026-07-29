from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    WithJsonSchema,
    field_validator,
    model_validator,
)

from universal_rpa.domain.errors import ErrorCode
from universal_rpa.domain.types import (
    DataCell,
    FrozenJsonObject,
    FrozenMapping,
    JsonValue,
    thaw_json,
)
from universal_rpa.infrastructure.redaction import sanitize_evidence

ActionStatus = Literal["success", "skipped", "failed", "cancelled"]
RunStatus = Literal["success", "partial", "failed", "cancelled"]
SkipReason = Literal["if_present_absent", "disabled", "skip_iteration"]

ImmutableEvidence = Annotated[
    FrozenJsonObject,
    BeforeValidator(sanitize_evidence),
    PlainSerializer(thaw_json, return_type=dict[str, JsonValue]),
    WithJsonSchema({"type": "object", "additionalProperties": True}),
]


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("datetime must use UTC")
    return value.astimezone(UTC)


class LoopCursor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    loop_step_id: UUID
    row_index: int = Field(ge=0)


@dataclass(frozen=True, slots=True)
class TableData:
    headers: tuple[str, ...]
    rows: tuple[tuple[DataCell, ...], ...]

    def __post_init__(self) -> None:
        headers = tuple(self.headers)
        rows = tuple(tuple(row) for row in self.rows)
        if any(not isinstance(header, str) or not header.strip() for header in headers):
            raise ValueError("table headers must be nonblank strings")
        if len(set(headers)) != len(headers):
            raise ValueError("table headers must be unique")
        for row in rows:
            if len(row) != len(headers):
                raise ValueError("table row width must match header width")
            if any(
                cell is not None and not isinstance(cell, (bool, int, float, str)) for cell in row
            ):
                raise ValueError("table cells must be scalar values")
        object.__setattr__(self, "headers", headers)
        object.__setattr__(self, "rows", rows)


class OutputCommit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    destination: Path
    format: Literal["csv", "xlsx"]
    sheet_name: str | None
    row_count: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    headers_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    committed: bool
    producer_step_id: UUID
    producer_cursor: tuple[LoopCursor, ...] = ()

    @model_validator(mode="after")
    def sheet_matches_output_format(self) -> OutputCommit:
        if self.format == "csv" and self.sheet_name is not None:
            raise ValueError("CSV output cannot have a sheet name")
        if self.format == "xlsx" and (self.sheet_name is None or not self.sheet_name.strip()):
            raise ValueError("XLSX output requires a nonblank sheet name")
        return self


class ActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    run_id: UUID
    step_id: UUID
    iteration_path: tuple[int, ...] = ()
    iteration_cursor: tuple[LoopCursor, ...] = ()
    status: ActionStatus
    started_at: datetime
    duration_ms: int = Field(default=0, ge=0)
    attempt_count: int = Field(default=1, ge=1, le=4)
    error_code: ErrorCode | None = None
    safe_message: str = ""
    evidence: ImmutableEvidence = Field(default_factory=FrozenMapping.empty)
    skip_reason: SkipReason | None = None
    output_commit: OutputCommit | None = None

    @field_validator("started_at", mode="after")
    @classmethod
    def started_at_is_utc(cls, value: datetime) -> datetime:
        return _utc_datetime(value)

    @model_validator(mode="after")
    def status_fields_are_consistent(self) -> ActionResult:
        if self.status == "skipped":
            if self.skip_reason is None:
                raise ValueError("skipped result requires a skip reason")
        elif self.skip_reason is not None:
            raise ValueError("only skipped results can have a skip reason")

        if self.status in {"success", "skipped"}:
            if self.error_code is not None:
                raise ValueError("successful and skipped results cannot have an error")
        elif self.status == "failed":
            if self.error_code is None or not self.safe_message.strip():
                raise ValueError("failed result requires a typed error and safe message")
        elif self.error_code is not ErrorCode.CANCELLED or not self.safe_message.strip():
            raise ValueError("cancelled result requires the cancelled error code")
        return self


class RunReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: UUID
    workflow_id: UUID
    workflow_revision: int = Field(ge=0)
    status: RunStatus
    started_at: datetime
    finished_at: datetime
    error_code: ErrorCode | None = None
    safe_message: str = ""
    results: tuple[ActionResult, ...]
    completed_iterations: int = Field(ge=0)
    total_iterations: int | None = Field(default=None, ge=0)
    last_checkpoint_cursor: tuple[LoopCursor, ...] | None = None
    output_commits: tuple[OutputCommit, ...] = ()

    @field_validator("started_at", "finished_at", mode="after")
    @classmethod
    def timestamps_are_utc(cls, value: datetime) -> datetime:
        return _utc_datetime(value)

    @field_validator("output_commits", mode="after")
    @classmethod
    def keep_latest_commit_per_destination(
        cls, commits: tuple[OutputCommit, ...]
    ) -> tuple[OutputCommit, ...]:
        deduplicated: list[OutputCommit] = []
        positions: dict[str, int] = {}
        for commit in commits:
            destination_key = str(commit.destination.resolve()).casefold()
            existing_position = positions.get(destination_key)
            if existing_position is None:
                positions[destination_key] = len(deduplicated)
                deduplicated.append(commit)
            else:
                deduplicated[existing_position] = commit
        return tuple(deduplicated)

    @model_validator(mode="after")
    def run_fields_are_consistent(self) -> RunReport:
        if self.finished_at < self.started_at:
            raise ValueError("run cannot finish before it starts")
        if self.status in {"success", "partial"}:
            if self.error_code is not None:
                raise ValueError("successful and partial runs cannot have an error")
        elif self.status == "failed":
            if self.error_code is None or not self.safe_message.strip():
                raise ValueError("failed run requires a typed error and safe message")
        elif self.error_code is not ErrorCode.CANCELLED or not self.safe_message.strip():
            raise ValueError("cancelled run requires the cancelled error code")
        return self


def aggregate_run_status(results: Sequence[ActionResult]) -> RunStatus:
    if any(result.status == "cancelled" for result in results):
        return "cancelled"
    if any(result.status == "failed" for result in results):
        return "failed"
    if any(
        result.status == "skipped" and result.skip_reason == "skip_iteration" for result in results
    ):
        return "partial"
    return "success"


__all__ = [
    "ActionResult",
    "ActionStatus",
    "LoopCursor",
    "OutputCommit",
    "RunReport",
    "RunStatus",
    "SkipReason",
    "TableData",
    "aggregate_run_status",
]
