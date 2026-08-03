from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from universal_rpa.application.projects import ProjectSession
from universal_rpa.bootstrap import AppServices
from universal_rpa.domain.errors import ValidationReport
from universal_rpa.ui.editor_page import WorkflowEditor
from universal_rpa.ui.project_home import ProjectHome
from universal_rpa.ui.recorder_page import RecorderPage


def _placeholder(title: str, message: str) -> QWidget:
    page = QWidget()
    layout = QVBoxLayout(page)
    heading = QLabel(title)
    heading.setObjectName("page-title")
    body = QLabel(message)
    body.setWordWrap(True)
    body.setEnabled(False)
    layout.addWidget(heading)
    layout.addWidget(body)
    layout.addStretch(1)
    return page


class MainWindow(QMainWindow):
    _PAGE_DEFINITIONS = (
        ("project", "프로젝트"),
        ("recorder", "기록"),
        ("editor", "편집"),
        ("runner", "실행"),
        ("report", "보고서"),
    )

    def __init__(self, services: AppServices, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.services = services
        self.session: ProjectSession | None = None
        self.setWindowTitle("Universal RPA Studio")
        self.resize(1180, 760)

        self.navigation = QListWidget()
        self.navigation.setObjectName("navigation")
        self.navigation.setFixedWidth(150)
        self.pages = QStackedWidget()
        self.pages.setObjectName("pages")

        self.project_page = ProjectHome(services.project_service)
        self.recorder_page = RecorderPage(
            services.window_context,
            services.recording_service,
            services.normalization_service,
            services.recording_store,
        )
        self.editor_page = WorkflowEditor(services.editing_service)
        self.runner_page = _placeholder("실행", "실행 기능은 M4에서 활성화됩니다.")
        self.report_page = _placeholder("보고서", "실행 보고서는 M5에서 활성화됩니다.")

        page_widgets = (
            self.project_page,
            self.recorder_page,
            self.editor_page,
            self.runner_page,
            self.report_page,
        )
        for (name, label), page in zip(self._PAGE_DEFINITIONS, page_widgets, strict=True):
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.navigation.addItem(item)
            self.pages.addWidget(page)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar_layout = QVBoxLayout(sidebar)
        product = QLabel("Universal\nRPA Studio")
        product.setObjectName("product-name")
        sidebar_layout.addWidget(product)
        sidebar_layout.addWidget(self.navigation, 1)

        central = QWidget()
        central_layout = QHBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.addWidget(sidebar)
        central_layout.addWidget(self.pages, 1)
        self.setCentralWidget(central)
        self.statusBar().showMessage("프로젝트를 만들거나 여세요.")

        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.project_page.session_opened.connect(self.open_session)
        self.project_page.failed.connect(self._show_safe_error)
        self.recorder_page.candidates_reviewed.connect(self.editor_page.apply_command)
        self.editor_page.edit_requested.connect(self._editor_changed)
        self.navigation.setCurrentRow(0)

        if services.startup_warnings:
            self.statusBar().showMessage(services.startup_warnings[0])

    def page_count(self) -> int:
        return self.pages.count()

    def page_name(self, index: int) -> str:
        item = self.navigation.item(index)
        value = item.data(Qt.ItemDataRole.UserRole)
        return str(value)

    def status_message(self) -> str:
        return self.statusBar().currentMessage()

    def open_project(self, project_dir: Path) -> bool:
        try:
            session = self.services.project_service.open(project_dir)
        except Exception:
            self._show_safe_error("워크플로를 열 수 없습니다. 프로젝트 파일을 확인하세요.")
            return False
        return self.open_session(session)

    @Slot(object)
    def open_session(self, session: ProjectSession) -> bool:
        if self.session is not None and self.session.dirty:
            choice = QMessageBox.question(
                self,
                "저장되지 않은 변경",
                "현재 프로젝트 변경 내용을 저장할까요?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if choice == QMessageBox.StandardButton.Cancel:
                return False
            if choice == QMessageBox.StandardButton.Save:
                try:
                    self.session = self.services.project_service.save(self.session)
                except Exception:
                    self._show_safe_error("프로젝트를 저장할 수 없습니다.")
                    return False
        self.session = session
        self.project_page.show_session(session)
        self.editor_page.set_session(session)
        self.statusBar().showMessage(f"프로젝트 열림: {session.workflow.name}")
        return True

    def show_validation(self, report: ValidationReport) -> None:
        self.editor_page.show_validation(report)
        if report.is_valid:
            self.statusBar().showMessage("사전 검증을 통과했습니다.")
            return
        self.statusBar().showMessage(f"수정이 필요한 항목이 {len(report.errors)}개 있습니다.")

    @Slot(object)
    def _editor_changed(self, command: object) -> None:
        del command
        if self.editor_page.session is not None:
            self.session = self.editor_page.session
            self.statusBar().showMessage("프로젝트에 저장되지 않은 변경이 있습니다.")

    @Slot(str)
    def _show_safe_error(self, message: str) -> None:
        self.statusBar().showMessage(message)


__all__ = ["MainWindow"]
