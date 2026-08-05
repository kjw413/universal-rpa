"""Run artifacts expire on a fixed schedule without following reparse points."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from universal_rpa.infrastructure.artifact_store import ArtifactRetentionService

NOW = datetime(2026, 8, 3, tzinfo=UTC)
WORKFLOW_DIR = "00000000-0000-0000-0000-000000000901"


def _run_directory(root: Path, run_name: str, *, age_days: int) -> Path:
    directory = root / WORKFLOW_DIR / run_name
    directory.mkdir(parents=True)
    (directory / "report.json").write_text("{}", encoding="utf-8")
    stamp = (NOW - timedelta(days=age_days)).timestamp()
    os.utime(directory, (stamp, stamp))
    return directory


def test_expired_run_directories_are_removed(tmp_path: Path) -> None:
    expired = _run_directory(tmp_path, "expired", age_days=31)
    fresh = _run_directory(tmp_path, "fresh", age_days=2)

    summary = ArtifactRetentionService(tmp_path).prune(NOW, timedelta(days=30))

    assert not expired.exists()
    assert fresh.exists()
    assert summary.removed == 1
    assert summary.failures == ()


def test_retention_boundary_keeps_a_run_that_is_exactly_at_the_limit(tmp_path: Path) -> None:
    boundary = _run_directory(tmp_path, "boundary", age_days=30)

    ArtifactRetentionService(tmp_path).prune(NOW, timedelta(days=30))

    assert boundary.exists()


def test_missing_root_is_not_an_error(tmp_path: Path) -> None:
    summary = ArtifactRetentionService(tmp_path / "absent").prune(NOW, timedelta(days=30))

    assert summary.removed == 0
    assert summary.failures == ()


def test_reparse_point_run_directories_are_reported_and_never_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    workflow_dir = tmp_path / "runs" / WORKFLOW_DIR
    workflow_dir.mkdir(parents=True)
    link = workflow_dir / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"directory symlink privilege unavailable: {error}")
    # Age the *target*, not the link. Windows has no os.utime that can touch a
    # link without following it, and the link's own age would prove nothing:
    # pruning reports a reparse point before it ever reads a timestamp. An
    # expired target is what makes the assertions below bite -- a service that
    # followed the link would find an expired directory and delete keep.txt.
    stamp = (NOW - timedelta(days=99)).timestamp()
    os.utime(outside, (stamp, stamp))

    summary = ArtifactRetentionService(tmp_path / "runs").prune(NOW, timedelta(days=30))

    assert (outside / "keep.txt").exists()
    assert summary.removed == 0
    assert summary.failures == (str(link),)


def test_empty_workflow_directories_are_pruned_after_their_runs(tmp_path: Path) -> None:
    _run_directory(tmp_path, "expired", age_days=99)

    ArtifactRetentionService(tmp_path).prune(NOW, timedelta(days=30))

    assert not (tmp_path / WORKFLOW_DIR).exists()
