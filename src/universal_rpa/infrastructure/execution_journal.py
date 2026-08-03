"""Minimal durable journal that never stores values, targets, or secrets."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from universal_rpa.domain.errors import ErrorCode, RpaError
from universal_rpa.domain.results import LoopCursor


class InProgressAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    step_id: UUID
    action_type: str
    idempotent: bool
    state: Literal["inflight", "succeeded"]


class InProgressIterationJournal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    journal_schema_version: Literal["1"] = "1"
    workflow_id: UUID
    run_id: UUID
    cursor: tuple[LoopCursor, ...]
    actions: tuple[InProgressAction, ...] = ()
    started_at: datetime
    updated_at: datetime


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


class JsonExecutionJournalStore:
    def __init__(self, root: Path) -> None:
        candidate = Path(root).absolute()
        if candidate.exists() and _is_link_like(candidate):
            raise ValueError("journal root must not be a link or reparse point")
        candidate.mkdir(parents=True, exist_ok=True)
        self._root = candidate.resolve()

    def _path(self, workflow_id: UUID, run_id: UUID) -> Path:
        directory = self._root / str(workflow_id)
        if directory.exists() and _is_link_like(directory):
            raise RpaError(ErrorCode.CHECKPOINT_INVALID, "실행 journal 폴더가 안전하지 않습니다.")
        directory.mkdir(parents=True, exist_ok=True)
        if directory.resolve().parent != self._root:
            raise RpaError(ErrorCode.CHECKPOINT_INVALID, "실행 journal 경로가 안전하지 않습니다.")
        return directory / f"{run_id}.journal.json"

    def save(self, journal: InProgressIterationJournal) -> None:
        path = self._path(journal.workflow_id, journal.run_id)
        temp = path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(journal.model_dump_json(indent=2))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)

    def load(self, workflow_id: UUID, run_id: UUID) -> InProgressIterationJournal | None:
        path = self._path(workflow_id, run_id)
        if not path.is_file():
            return None
        try:
            return InProgressIterationJournal.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            raise RpaError(
                ErrorCode.CHECKPOINT_INVALID, "실행 중단 기록을 읽을 수 없습니다."
            ) from None

    def clear(self, workflow_id: UUID, run_id: UUID) -> None:
        self._path(workflow_id, run_id).unlink(missing_ok=True)


__all__ = ["InProgressAction", "InProgressIterationJournal", "JsonExecutionJournalStore"]
