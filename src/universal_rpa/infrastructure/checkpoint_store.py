"""Atomic local checkpoint persistence for resumable runs."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from universal_rpa.domain.errors import ErrorCode, RpaError
from universal_rpa.domain.results import LoopCursor, OutputCommit


class DataSourceFingerprint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    data_source_id: str
    source_type: str
    row_count: int = Field(ge=0)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AdapterFingerprint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    adapter_id: str
    implementation_version: str
    descriptor_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ResumeFingerprint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["1"] = "1"
    workflow_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    resolved_inputs_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_root_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    data_sources: tuple[DataSourceFingerprint, ...]
    adapters: tuple[AdapterFingerprint, ...]
    environment_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class Checkpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    checkpoint_schema_version: Literal["1"] = "1"
    workflow_id: UUID
    run_id: UUID
    date_context_today: str
    date_context_run_date: str
    fingerprint: ResumeFingerprint
    completed_cursor: tuple[LoopCursor, ...] = ()
    output_commits: tuple[OutputCommit, ...] = ()
    updated_at: datetime

    @field_validator("output_commits", mode="after")
    @classmethod
    def keep_latest_commit_per_destination(
        cls, commits: tuple[OutputCommit, ...]
    ) -> tuple[OutputCommit, ...]:
        latest: dict[str, OutputCommit] = {}
        order: list[str] = []
        for commit in commits:
            key = str(commit.destination.resolve()).casefold()
            if key not in latest:
                order.append(key)
            latest[key] = commit
        return tuple(latest[key] for key in order)


class TerminalRunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    workflow_id: UUID
    run_id: UUID
    terminal_schema_version: Literal["1"] = "1"
    status: Literal["success", "partial"]
    finished_at: datetime


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


class JsonCheckpointStore:
    def __init__(self, root: Path) -> None:
        candidate = Path(root).absolute()
        if candidate.exists() and _is_link_like(candidate):
            raise ValueError("checkpoint root must not be a link or reparse point")
        candidate.mkdir(parents=True, exist_ok=True)
        self._root = candidate.resolve()

    def _directory(self, workflow_id: UUID) -> Path:
        directory = self._root / str(workflow_id)
        if directory.exists() and _is_link_like(directory):
            raise RpaError(ErrorCode.CHECKPOINT_INVALID, "실행 상태 폴더가 안전하지 않습니다.")
        directory.mkdir(parents=True, exist_ok=True)
        if directory.resolve().parent != self._root:
            raise RpaError(ErrorCode.CHECKPOINT_INVALID, "실행 상태 경로가 안전하지 않습니다.")
        return directory

    def _active_path(self, workflow_id: UUID, run_id: UUID) -> Path:
        return self._directory(workflow_id) / f"{run_id}.active.json"

    def _terminal_path(self, workflow_id: UUID, run_id: UUID) -> Path:
        return self._directory(workflow_id) / f"{run_id}.terminal.json"

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)

    def save_active(self, checkpoint: Checkpoint) -> None:
        self._atomic_write(
            self._active_path(checkpoint.workflow_id, checkpoint.run_id),
            checkpoint.model_dump_json(indent=2),
        )

    def load_active(self, workflow_id: UUID, run_id: UUID) -> Checkpoint:
        path = self._active_path(workflow_id, run_id)
        try:
            return Checkpoint.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            raise RpaError(
                ErrorCode.CHECKPOINT_INVALID, "재개할 실행 상태를 읽을 수 없습니다."
            ) from None

    def discover_active(self, workflow_id: UUID) -> tuple[Checkpoint, ...]:
        found: list[Checkpoint] = []
        for path in self._directory(workflow_id).glob("*.active.json"):
            try:
                found.append(Checkpoint.model_validate_json(path.read_text(encoding="utf-8")))
            except Exception:
                continue
        return tuple(sorted(found, key=lambda item: item.updated_at, reverse=True))

    def mark_terminal(self, record: TerminalRunRecord) -> None:
        active = self._active_path(record.workflow_id, record.run_id)
        terminal = self._terminal_path(record.workflow_id, record.run_id)
        # First replace the active checkpoint with terminal-shaped content. A crash
        # before the final rename therefore cannot expose a resumable checkpoint.
        self._atomic_write(active, record.model_dump_json(indent=2))
        os.replace(active, terminal)


__all__ = [
    "AdapterFingerprint",
    "Checkpoint",
    "DataSourceFingerprint",
    "JsonCheckpointStore",
    "ResumeFingerprint",
    "TerminalRunRecord",
]
