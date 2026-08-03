from __future__ import annotations

from PySide6.QtWidgets import QApplication

from universal_rpa.bootstrap import AppServices
from universal_rpa.ui.app import build_main_window, create_application


def test_create_application_sets_studio_identity(qapp: QApplication) -> None:
    application = create_application(("universal-rpa-studio",))

    assert application is qapp
    assert application.applicationName() == "Universal RPA Studio"


def test_build_main_window_does_not_start_event_loop(
    qtbot: object,
    app_services: AppServices,
) -> None:
    window = build_main_window(app_services)
    qtbot.addWidget(window)  # type: ignore[attr-defined]

    assert not window.isVisible()
    assert window.page_count() == 5
