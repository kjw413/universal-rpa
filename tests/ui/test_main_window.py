from __future__ import annotations

from pathlib import Path

from universal_rpa.bootstrap import AppServices
from universal_rpa.ui.main_window import MainWindow


def test_all_pages_live_in_one_main_window(qtbot: object, app_services: AppServices) -> None:
    window = MainWindow(app_services)
    qtbot.addWidget(window)  # type: ignore[attr-defined]

    assert [window.page_name(index) for index in range(window.page_count())] == [
        "project",
        "recorder",
        "editor",
        "runner",
        "report",
    ]


def test_corrupt_project_shows_safe_korean_error(
    qtbot: object,
    tmp_path: Path,
    app_services: AppServices,
) -> None:
    project = tmp_path / "corrupt"
    project.mkdir()
    (project / "workflow.json").write_text("not json", encoding="utf-8")
    window = MainWindow(app_services)
    qtbot.addWidget(window)  # type: ignore[attr-defined]

    assert not window.open_project(project)
    assert "워크플로를 열 수 없습니다" in window.status_message()
