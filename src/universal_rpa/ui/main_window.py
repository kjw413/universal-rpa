from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
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
from universal_rpa.ports.credentials import SecretStorePort, SecretValue
from universal_rpa.ui.editor_page import WorkflowEditor
from universal_rpa.ui.project_home import ProjectHome
from universal_rpa.ui.recorder_page import RecorderPage
from universal_rpa.ui.report_page import ReportPage
from universal_rpa.ui.runner_page import RunnerPage
from universal_rpa.ui.target_picker import TargetPicker


class _NoCredentialStore:
    """Fail-closed stand-in when no platform credential store is available."""

    def exists(self, reference: str) -> bool:
        del reference
        return False

    def read(self, reference: str) -> SecretValue:
        raise KeyError(reference)


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
        self.editor_page = WorkflowEditor(
            services.editing_service,
            validation_service=services.validation_service,
            project_service=services.project_service,
            preview_store=services.preview_store,
            privacy_service=services.recording_privacy,
            adapter_registry=services.adapter_registry,
        )
        secret_store: SecretStorePort = services.secret_store or _NoCredentialStore()
        self.runner_page = RunnerPage(
            services.execution_service,
            secret_store,
            artifact_store=services.artifact_store,
            report_projector=services.report_projector,
        )
        self.report_page = ReportPage()
        # Captured by value: a bound method here would make the child page own a
        # reference back to this window, and that cycle outlives Qt's teardown.
        context = services.window_context
        request_factory = services.capture_request_factory
        self.editor_page.target_picker_factory = lambda: TargetPicker(
            context, request_factory=request_factory
        )

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

        self.save_action = QAction("프로젝트 저장", self)
        self.save_action.setShortcut(QKeySequence.StandardKey.Save)
        self.save_action.setStatusTip("편집한 내용을 프로젝트 폴더에 저장합니다.")
        self.save_action.triggered.connect(self.save_project)
        file_menu = self.menuBar().addMenu("파일")
        file_menu.addAction(self.save_action)

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
        self.recorder_page.recording_completed.connect(self.editor_page.remember_recording_result)
        self.recorder_page.candidates_reviewed.connect(self.editor_page.apply_command)
        self.editor_page.edit_requested.connect(self._editor_changed)
        self.runner_page.run_finished.connect(self._show_report)
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

    def save_project(self) -> bool:
        """Persist the open session and hand the saved workflow to every page."""

        session = self.session
        if session is None:
            return False
        if not session.dirty:
            return True
        try:
            saved = self.services.project_service.save(session)
        except Exception:
            self._show_safe_error("프로젝트를 저장할 수 없습니다.")
            return False
        self._adopt(saved)
        self.statusBar().showMessage(f"프로젝트를 저장했습니다: {saved.workflow.name}")
        return True

    def _resolve_unsaved_changes(self) -> bool:
        """Ask about pending edits. False means the caller must not continue."""

        session = self.session
        if session is None or not session.dirty:
            return True
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
            return self.save_project()
        return True

    @Slot(object)
    def open_session(self, session: ProjectSession) -> bool:
        if not self._resolve_unsaved_changes():
            return False
        self._adopt(session)
        self.statusBar().showMessage(f"프로젝트 열림: {session.workflow.name}")
        return True

    def _adopt(self, session: ProjectSession) -> None:
        """Point every page at one session so a run cannot execute a stale workflow."""

        self.session = session
        self.project_page.show_session(session)
        self.editor_page.set_session(session)
        self.runner_page.set_session(session)

    @Slot(object)
    def _show_report(self, document: object) -> None:
        self.report_page.set_report(document)
        self.navigation.setCurrentRow(len(self._PAGE_DEFINITIONS) - 1)
        self.statusBar().showMessage("실행 보고서를 확인하세요.")

    def show_validation(self, report: ValidationReport) -> None:
        self.editor_page.show_validation(report)
        if report.is_valid:
            self.statusBar().showMessage("사전 검증을 통과했습니다.")
            return
        self.statusBar().showMessage(f"수정이 필요한 항목이 {len(report.errors)}개 있습니다.")

    @Slot(object)
    def _editor_changed(self, command: object) -> None:
        del command
        session = self.editor_page.session
        if session is None:
            return
        self.session = session
        # The Runner holds its own copy, so without this a run replays the
        # workflow as it was opened and silently ignores every edit.
        self.runner_page.set_session(session)
        message = (
            "프로젝트에 저장되지 않은 변경이 있습니다."
            if session.dirty
            else f"프로젝트를 저장했습니다: {session.workflow.name}"
        )
        self.statusBar().showMessage(message)

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._resolve_unsaved_changes():
            event.ignore()
            return
        super().closeEvent(event)

    @Slot(str)
    def _show_safe_error(self, message: str) -> None:
        self.statusBar().showMessage(message)


__all__ = ["MainWindow"]
