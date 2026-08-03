from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Protocol
from uuid import UUID

from universal_rpa.domain.recording import (
    RawInputEvent,
    RecordingSession,
    RecordingSessionSummary,
)
from universal_rpa.domain.workflow import Workflow


class WorkflowRepositoryPort(Protocol):
    def load(self, project_dir: Path) -> Workflow: ...

    def save(
        self,
        project_dir: Path,
        workflow: Workflow,
        expected_revision: int,
    ) -> Workflow: ...


class RecordingStorePort(Protocol):
    def create_session(self, session: RecordingSession) -> None: ...

    def append(self, event: RawInputEvent) -> None: ...

    def finalize(
        self,
        session_id: UUID,
        *,
        retained: bool,
        incomplete: bool,
        dropped_event_count: int = 0,
    ) -> RecordingSessionSummary: ...

    def load_summary(self, session_id: UUID) -> RecordingSessionSummary: ...

    def iter_events(self, session_id: UUID) -> Iterator[RawInputEvent]: ...

    def delete_session(self, session_id: UUID, *, reason: str) -> None: ...


__all__ = ["RecordingStorePort", "WorkflowRepositoryPort"]
