from __future__ import annotations

import sys
from collections.abc import Sequence

from PySide6.QtCore import QLocale
from PySide6.QtWidgets import QApplication

from universal_rpa.adapters.windows.dpi import enable_per_monitor_v2_dpi_awareness
from universal_rpa.bootstrap import AppServices, build_services
from universal_rpa.ui.main_window import MainWindow


def _configure_application(application: QApplication) -> QApplication:
    application.setApplicationName("Universal RPA Studio")
    application.setOrganizationName("Universal RPA")
    QLocale.setDefault(QLocale(QLocale.Language.Korean, QLocale.Country.SouthKorea))
    return application


def create_application(argv: Sequence[str]) -> QApplication:
    existing = QApplication.instance()
    if existing is not None:
        if not isinstance(existing, QApplication):
            raise RuntimeError("QApplication보다 먼저 QCoreApplication이 생성되었습니다.")
        return _configure_application(existing)
    enable_per_monitor_v2_dpi_awareness()
    return _configure_application(QApplication(list(argv)))


def build_main_window(services: AppServices) -> MainWindow:
    return MainWindow(services)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = tuple(sys.argv if argv is None else argv)
    application = create_application(arguments)
    window = build_main_window(build_services())
    window.show()
    return application.exec()


__all__ = ["build_main_window", "create_application", "main"]
