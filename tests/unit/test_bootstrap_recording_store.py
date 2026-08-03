from __future__ import annotations

from pathlib import Path

from universal_rpa.bootstrap import build_services


def test_bootstrap_uses_default_recording_store_not_project_directory(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    services = build_services(
        active_project_dir=project,
        local_app_data=tmp_path / "local-app-data",
    )
    assert not services.recording_store.root.is_relative_to(project)
    assert services.recording_store.root.name == "recordings"
