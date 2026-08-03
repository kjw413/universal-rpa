from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from universal_rpa.application.projects import ProjectService, ProjectSession


class ProjectHome(QWidget):
    session_opened = Signal(object)
    failed = Signal(str)

    def __init__(self, project_service: ProjectService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._project_service = project_service
        self.setObjectName("project-home")

        title = QLabel("Universal RPA 프로젝트")
        title.setObjectName("page-title")
        description = QLabel(
            "자동화 업무를 새로 만들거나 기존 프로젝트 폴더를 여세요. "
            "프로젝트에는 workflow.json, targets, inputs만 저장됩니다."
        )
        description.setWordWrap(True)
        self.new_project_button = QPushButton("새 프로젝트")
        self.open_project_button = QPushButton("프로젝트 열기")
        self.project_status = QLabel("열린 프로젝트가 없습니다.")
        self.project_status.setWordWrap(True)

        buttons = QHBoxLayout()
        buttons.addWidget(self.new_project_button)
        buttons.addWidget(self.open_project_button)
        buttons.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(description)
        layout.addLayout(buttons)
        layout.addWidget(self.project_status)
        layout.addStretch(1)

        self.new_project_button.clicked.connect(self._new_project)
        self.open_project_button.clicked.connect(self._open_project)

    def request_project_directory(self) -> Path | None:
        selected = QFileDialog.getExistingDirectory(self, "비어 있는 프로젝트 폴더 선택")
        return Path(selected) if selected else None

    def request_project_name(self) -> str | None:
        name, accepted = QInputDialog.getText(self, "새 프로젝트", "프로젝트 이름")
        return name if accepted else None

    def request_open_directory(self) -> Path | None:
        selected = QFileDialog.getExistingDirectory(self, "프로젝트 폴더 열기")
        return Path(selected) if selected else None

    def show_session(self, session: ProjectSession) -> None:
        self.project_status.setText(
            f"{session.workflow.name}\n{session.project_dir}\n리비전 {session.workflow.revision}"
        )

    @Slot()
    def _new_project(self) -> None:
        project_dir = self.request_project_directory()
        if project_dir is None:
            return
        name = self.request_project_name()
        if name is None:
            return
        try:
            session = self._project_service.create(project_dir, name)
        except Exception:
            self.failed.emit("프로젝트를 만들 수 없습니다. 폴더와 이름을 확인하세요.")
            return
        self.show_session(session)
        self.session_opened.emit(session)

    @Slot()
    def _open_project(self) -> None:
        project_dir = self.request_open_directory()
        if project_dir is None:
            return
        try:
            session = self._project_service.open(project_dir)
        except Exception:
            self.failed.emit("워크플로를 열 수 없습니다. 프로젝트 파일을 확인하세요.")
            return
        self.show_session(session)
        self.session_opened.emit(session)


__all__ = ["ProjectHome"]
