from __future__ import annotations

import json
from pathlib import Path

import pytest

from universal_rpa.self_check import SelfCheckReport, run_self_check

EXPECTED_CHECKS = {
    "workflow_schema_v1",
    "builtin_adapters",
    "app_data_write",
    "dpi_awareness",
}


def test_self_check_verifies_schema_adapters_appdata_and_dpi(tmp_path: Path) -> None:
    report = run_self_check(app_data_root=tmp_path)

    assert report.ok
    assert {item.name for item in report.checks} == EXPECTED_CHECKS
    assert all(item.ok for item in report.checks)


def test_self_check_names_exactly_the_three_builtin_adapters(tmp_path: Path) -> None:
    report = run_self_check(app_data_root=tmp_path)
    adapters = next(item for item in report.checks if item.name == "builtin_adapters")

    assert adapters.detail == "windows, clipboard, tabular"


def test_self_check_json_is_path_free(tmp_path: Path) -> None:
    report = run_self_check(app_data_root=tmp_path)

    encoded = report.to_json()
    payload = json.loads(encoded)

    assert str(tmp_path) not in encoded
    assert tmp_path.name not in encoded
    assert ":\\" not in encoded
    assert payload["ok"] is True
    assert sorted(check["name"] for check in payload["checks"]) == sorted(EXPECTED_CHECKS)


def test_self_check_fails_closed_when_app_data_cannot_be_written(tmp_path: Path) -> None:
    unusable = tmp_path / "not-a-directory"
    unusable.write_bytes(b"file, not a directory")

    report = run_self_check(app_data_root=unusable)

    failed = next(item for item in report.checks if item.name == "app_data_write")
    assert not report.ok
    assert not failed.ok
    assert ":\\" not in report.to_json()


def test_self_check_leaves_no_probe_file_behind(tmp_path: Path) -> None:
    run_self_check(app_data_root=tmp_path)

    assert not list((tmp_path / "UniversalRPAStudio").rglob("*self-check*"))


def test_report_is_immutable(tmp_path: Path) -> None:
    report = run_self_check(app_data_root=tmp_path)

    assert isinstance(report, SelfCheckReport)
    with pytest.raises((AttributeError, TypeError)):
        report.ok = False  # type: ignore[misc]
