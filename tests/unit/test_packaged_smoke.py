from __future__ import annotations

import re
from pathlib import Path

import pytest

from universal_rpa.packaged_smoke import (
    SmokeRejected,
    distribution_file_names,
    run_packaged_smoke,
)

FORBIDDEN_DISTRIBUTION_ROOTS = {"recordings", "artifacts", "projects", ".superpowers"}


def test_packaged_smoke_builds_window_validates_runs_and_reports(tmp_path: Path) -> None:
    report = run_packaged_smoke(tmp_path)

    assert report.ok
    assert report.main_window_created
    assert report.workflow_action == "windows.wait"
    assert report.validation_error_count == 0
    assert report.run_status == "success"
    assert report.safe_report_created


def test_packaged_smoke_uses_the_three_real_adapters(tmp_path: Path) -> None:
    report = run_packaged_smoke(tmp_path)

    assert report.adapter_ids == ("clipboard", "tabular", "windows")


def test_packaged_smoke_report_is_path_free(tmp_path: Path) -> None:
    encoded = run_packaged_smoke(tmp_path).to_json()

    assert str(tmp_path) not in encoded
    assert not re.search(r"[A-Za-z]:\\\\", encoded)


def test_packaged_smoke_refuses_a_nonempty_root(tmp_path: Path) -> None:
    (tmp_path / "leftover.txt").write_text("x", encoding="utf-8")

    with pytest.raises(SmokeRejected, match="empty"):
        run_packaged_smoke(tmp_path)


def test_packaged_smoke_refuses_a_missing_root(tmp_path: Path) -> None:
    with pytest.raises(SmokeRejected):
        run_packaged_smoke(tmp_path / "absent")


def test_distribution_manifest_excludes_sensitive_roots() -> None:
    assert FORBIDDEN_DISTRIBUTION_ROOTS.isdisjoint(distribution_file_names())


def test_distribution_manifest_lists_the_product_package() -> None:
    names = distribution_file_names()

    assert "universal_rpa" in names
    assert "samples" not in names
    assert "tests" not in names
