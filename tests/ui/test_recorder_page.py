from __future__ import annotations

import shutil
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import Qt

from tests.helpers.recording_fakes import recording_target
from universal_rpa.application.editing import ImportCandidates
from universal_rpa.application.normalization import NormalizationService
from universal_rpa.application.recording import RecordingState
from universal_rpa.bootstrap import AppServices
from universal_rpa.infrastructure.recording_store import JsonlRecordingStore
from universal_rpa.ui.recorder_page import RecorderPage

SESSION_ID = UUID("00000000-0000-0000-0000-000000000702")


def page_for(services: AppServices) -> RecorderPage:
    return RecorderPage(
        services.window_context,
        services.recording_service,
        services.normalization_service,
        services.recording_store,
    )


def test_start_requires_an_explicit_target(qtbot: object, app_services: AppServices) -> None:
    page = page_for(app_services)
    qtbot.addWidget(page)  # type: ignore[attr-defined]
    page.show()

    qtbot.mouseClick(page.start_button, Qt.MouseButton.LeftButton)  # type: ignore[attr-defined]

    assert page.recording_worker_start_count == 0
    assert "대상 창을 선택" in page.validation_text.text()


def test_recording_banner_never_disappears_while_active(
    qtbot: object,
    app_services: AppServices,
) -> None:
    page = page_for(app_services)
    qtbot.addWidget(page)  # type: ignore[attr-defined]
    page.show()
    page.set_targets((recording_target(),))
    page.target_combo.setCurrentIndex(0)
    completed: list[object] = []
    page.recording_completed.connect(completed.append)

    qtbot.mouseClick(page.start_button, Qt.MouseButton.LeftButton)  # type: ignore[attr-defined]
    qtbot.waitUntil(  # type: ignore[attr-defined]
        lambda: app_services.recording_service.state is RecordingState.RECORDING,
        timeout=2_000,
    )

    assert page.banner.isVisible()
    assert "Ctrl+Shift+F12" in page.banner.text()

    qtbot.mouseClick(page.stop_button, Qt.MouseButton.LeftButton)  # type: ignore[attr-defined]
    qtbot.waitUntil(lambda: bool(completed), timeout=3_000)  # type: ignore[attr-defined]
    page.close()


def test_paused_state_updates_banner_and_control(
    qtbot: object,
    app_services: AppServices,
) -> None:
    page = page_for(app_services)
    qtbot.addWidget(page)  # type: ignore[attr-defined]

    page.on_state_changed("paused")

    assert page.banner.property("state") == "paused"
    assert page.pause_button.text() == "계속"


def test_finalized_recording_review_emits_one_import_command(
    qtbot: object,
    tmp_path: Path,
    app_services: AppServices,
) -> None:
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "recordings" / "ctrl-a-date-enter"
    session_dir = tmp_path / str(SESSION_ID)
    session_dir.mkdir()
    for filename in ("manifest.json", "events.jsonl"):
        shutil.copy2(fixture / filename, session_dir / filename)
    result = NormalizationService().normalize_session(
        JsonlRecordingStore.for_test(tmp_path),
        SESSION_ID,
    )
    page = page_for(app_services)
    qtbot.addWidget(page)  # type: ignore[attr-defined]
    reviewed: list[object] = []
    page.candidates_reviewed.connect(reviewed.append)

    page._on_completed(result)
    qtbot.mouseClick(page.import_button, Qt.MouseButton.LeftButton)  # type: ignore[attr-defined]

    assert len(reviewed) == 1
    assert isinstance(reviewed[0], ImportCandidates)
    assert [candidate.action_type for candidate in reviewed[0].candidates] == [
        "windows.hotkey",
        "windows.set_text",
        "windows.press_key",
    ]
