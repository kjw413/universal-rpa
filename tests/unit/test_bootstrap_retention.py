from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from tests.helpers.recording_fakes import (
    FakeInputCapture,
    InMemoryRecordingStore,
    StaticWindowContext,
)
from universal_rpa.bootstrap import build_services
from universal_rpa.infrastructure.artifact_store import ArtifactRetentionSummary
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


class SpyArtifactRetentionService:
    def __init__(self) -> None:
        self.prune_calls: list[tuple[datetime, timedelta]] = []

    def prune(
        self,
        now: datetime,
        retention: timedelta = timedelta(days=30),
    ) -> ArtifactRetentionSummary:
        self.prune_calls.append((now, retention))
        return ArtifactRetentionSummary()


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


def test_bootstrap_prunes_artifacts_once_without_following_reparse_points(
    tmp_path: Path,
) -> None:
    retention = SpyArtifactRetentionService()

    build_services(
        recording_store=RetentionStore(),
        capture=FakeInputCapture(),
        window_context=StaticWindowContext(),
        source_repository_root=tmp_path / "source",
        artifact_retention=retention,
        now=NOW,
    )

    assert retention.prune_calls == [(NOW, timedelta(days=30))]


def test_artifact_retention_failures_become_startup_warnings(tmp_path: Path) -> None:
    class FailingRetention(SpyArtifactRetentionService):
        def prune(
            self,
            now: datetime,
            retention: timedelta = timedelta(days=30),
        ) -> ArtifactRetentionSummary:
            super().prune(now, retention)
            return ArtifactRetentionSummary(removed=0, failures=("locked-run",))

    services = build_services(
        recording_store=RetentionStore(),
        capture=FakeInputCapture(),
        window_context=StaticWindowContext(),
        source_repository_root=tmp_path / "source",
        artifact_retention=FailingRetention(),
        now=NOW,
    )

    assert any("실행 기록" in warning for warning in services.startup_warnings)
