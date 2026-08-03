from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from tests.helpers.recording_fakes import (
    FakeInputCapture,
    InMemoryRecordingStore,
    StaticWindowContext,
)
from universal_rpa.bootstrap import build_services
from universal_rpa.infrastructure.recording_store import RetentionSummary

NOW = datetime(2026, 7, 27, tzinfo=UTC)


class RetentionStore(InMemoryRecordingStore):
    def __init__(self) -> None:
        super().__init__()
        self.purge_calls: list[tuple[datetime, timedelta]] = []

    def purge_expired(
        self,
        *,
        now: datetime,
        retention: timedelta = timedelta(days=7),
    ) -> RetentionSummary:
        self.purge_calls.append((now, retention))
        return RetentionSummary()


def test_bootstrap_runs_recording_retention_once(tmp_path: Path) -> None:
    store = RetentionStore()

    services = build_services(
        recording_store=store,
        capture=FakeInputCapture(),
        window_context=StaticWindowContext(),
        source_repository_root=tmp_path / "source",
        now=NOW,
    )

    assert services.recording_store is store
    assert store.purge_calls == [(NOW, timedelta(days=7))]
