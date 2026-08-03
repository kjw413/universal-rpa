from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt

from universal_rpa.application.projects import ProjectService
from universal_rpa.ui.project_home import ProjectHome


def test_cancelled_new_project_creates_nothing(qtbot: object, tmp_path: Path) -> None:
    page = ProjectHome(ProjectService())
    qtbot.addWidget(page)  # type: ignore[attr-defined]
    page.request_project_directory = lambda: None  # type: ignore[method-assign]

    qtbot.mouseClick(  # type: ignore[attr-defined]
        page.new_project_button,
        Qt.MouseButton.LeftButton,
    )

    assert list(tmp_path.iterdir()) == []


def test_new_project_emits_session_only_after_both_inputs(
    qtbot: object,
    tmp_path: Path,
) -> None:
    page = ProjectHome(ProjectService())
    qtbot.addWidget(page)  # type: ignore[attr-defined]
    project = tmp_path / "project"
    page.request_project_directory = lambda: project  # type: ignore[method-assign]
    page.request_project_name = lambda: "반복 조회"  # type: ignore[method-assign]
    opened: list[object] = []
    page.session_opened.connect(opened.append)

    qtbot.mouseClick(  # type: ignore[attr-defined]
        page.new_project_button,
        Qt.MouseButton.LeftButton,
    )

    assert len(opened) == 1
    assert (project / "workflow.json").is_file()
