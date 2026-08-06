from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QMessageBox

from tests.helpers.validation_fakes import ValidationSpyAdapter, registry_with
from universal_rpa.application.editing import RenameStep
from universal_rpa.application.projects import ProjectSession
from universal_rpa.bootstrap import AppServices
from universal_rpa.domain.workflow import ActionStep, TargetAppSpec, Workflow
from universal_rpa.ui.main_window import MainWindow

STEP_ID = UUID("00000000-0000-0000-0000-000000000841")
NOW = datetime(2026, 7, 27, tzinfo=UTC)


def saved_session(services: AppServices, project_dir: Path) -> ProjectSession:
    """Create a project on disk holding one renameable step."""

    project_dir.mkdir(parents=True, exist_ok=True)
    session = services.project_service.create(project_dir, "편집 반영 테스트")
    workflow = Workflow(
        workflow_id=session.workflow.workflow_id,
        name=session.workflow.name,
        revision=session.workflow.revision,
        target_apps=(
            TargetAppSpec(app_id="erp", process_executable="erp.exe", window_class="ERPMain"),
        ),
        steps=(
            ActionStep(
                step_id=STEP_ID,
                label="기록된 이름",
                action_type="windows.click",
            ),
        ),
        created_at=NOW,
        updated_at=NOW,
    )
    return services.project_service.save(services.project_service.with_workflow(session, workflow))


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


def test_editor_change_reaches_the_runner(
    qtbot: object,
    tmp_path: Path,
    app_services: AppServices,
) -> None:
    """A run must execute what the editor shows, not the workflow as first opened."""

    window = MainWindow(app_services)
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    window.open_session(saved_session(app_services, tmp_path / "project"))

    window.editor_page.apply_command(RenameStep(STEP_ID, "고친 이름"))

    runner_session = window.runner_page.session
    assert runner_session is not None
    assert runner_session.workflow.steps[0].label == "고친 이름"


def test_save_project_persists_editor_changes(
    qtbot: object,
    tmp_path: Path,
    app_services: AppServices,
) -> None:
    project_dir = tmp_path / "project"
    window = MainWindow(app_services)
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    window.open_session(saved_session(app_services, project_dir))
    window.editor_page.apply_command(RenameStep(STEP_ID, "저장된 이름"))

    assert window.save_project()

    reopened = app_services.project_service.open(project_dir)
    assert reopened.workflow.steps[0].label == "저장된 이름"
    assert window.session is not None
    assert not window.session.dirty


def test_the_property_panel_learns_what_the_adapters_can_wait_for(
    qtbot: object,
    app_services: AppServices,
) -> None:
    """The wait editor can only offer conditions the registry actually reports."""

    adapter = ValidationSpyAdapter()
    services = replace(app_services, adapter_registry=registry_with(adapter))
    window = MainWindow(services)
    qtbot.addWidget(window)  # type: ignore[attr-defined]

    descriptors = window.editor_page.property_panel.adapter_descriptors()
    assert adapter.adapter_id in descriptors


def test_retarget_gets_a_picker_wired_to_the_capture_port(
    qtbot: object,
    app_services: AppServices,
) -> None:
    """Unwired, '대상 다시 지정' emits a signal nobody listens to."""

    window = MainWindow(app_services)
    qtbot.addWidget(window)  # type: ignore[attr-defined]

    factory = window.editor_page.target_picker_factory
    assert factory is not None
    picker = factory()
    qtbot.addWidget(picker)  # type: ignore[attr-defined]
    assert picker.capture_port is app_services.window_context


def test_the_file_menu_saves_the_project(
    qtbot: object,
    tmp_path: Path,
    app_services: AppServices,
) -> None:
    """Saving needs an affordance a non-developer can find, not a reopen trick."""

    project_dir = tmp_path / "project"
    window = MainWindow(app_services)
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    window.open_session(saved_session(app_services, project_dir))
    window.editor_page.apply_command(RenameStep(STEP_ID, "메뉴로 저장"))

    assert window.save_action.shortcut() == QKeySequence(QKeySequence.StandardKey.Save)
    window.save_action.trigger()

    reopened = app_services.project_service.open(project_dir)
    assert reopened.workflow.steps[0].label == "메뉴로 저장"


def test_closing_with_unsaved_changes_saves_when_the_user_asks(
    qtbot: object,
    tmp_path: Path,
    app_services: AppServices,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Closing must not silently discard edits the status bar said were unsaved."""

    project_dir = tmp_path / "project"
    window = MainWindow(app_services)
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    window.open_session(saved_session(app_services, project_dir))
    window.editor_page.apply_command(RenameStep(STEP_ID, "닫기 전 이름"))
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *_args, **_kwargs: QMessageBox.StandardButton.Save),
    )

    assert window.close()

    reopened = app_services.project_service.open(project_dir)
    assert reopened.workflow.steps[0].label == "닫기 전 이름"
