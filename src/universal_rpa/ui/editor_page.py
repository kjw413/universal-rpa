from __future__ import annotations

from uuid import UUID

from PySide6.QtCore import QModelIndex, Signal, Slot
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from universal_rpa.application.editing import EditRejected, WorkflowEditingService
from universal_rpa.application.projects import ProjectSession
from universal_rpa.domain.errors import ValidationReport
from universal_rpa.domain.workflow import ActionStep, Step
from universal_rpa.ui.json_inspector import JsonInspector
from universal_rpa.ui.step_tree_model import WorkflowTreeModel
from universal_rpa.ui.target_preview import (
    MissingTargetPreviewResolver,
    TargetPreview,
    TargetPreviewResolver,
)


class StepPropertySummary(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.step_id: UUID | None = None
        self.label_value = QLabel("단계를 선택하세요")
        self.kind_value = QLabel("-")
        self.action_value = QLabel("-")
        form = QFormLayout(self)
        form.addRow("단계 이름", self.label_value)
        form.addRow("종류", self.kind_value)
        form.addRow("작업", self.action_value)

    def set_step(self, step: Step | None) -> None:
        self.step_id = step.step_id if step is not None else None
        self.label_value.setText(step.label if step is not None else "단계를 선택하세요")
        self.kind_value.setText(step.kind if step is not None else "-")
        self.action_value.setText(step.action_type if isinstance(step, ActionStep) else "-")


class WorkflowEditor(QWidget):
    edit_requested = Signal(object)
    step_test_requested = Signal(object)
    retarget_requested = Signal(object)
    validate_requested = Signal()

    def __init__(
        self,
        editing_service: WorkflowEditingService,
        preview_resolver: TargetPreviewResolver | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._editing_service = editing_service
        self.session: ProjectSession | None = None
        self.tree_model = WorkflowTreeModel()
        self.tree_view = QTreeView()
        self.tree_view.setModel(self.tree_model)
        self.tree_view.setHeaderHidden(True)
        self.target_preview = TargetPreview(preview_resolver or MissingTargetPreviewResolver())
        self.property_panel = StepPropertySummary()
        self.json_inspector = JsonInspector(self)

        self.json_button = QPushButton("JSON 보기")
        self.retarget_button = QPushButton("대상 다시 지정")
        self.test_button = QPushButton("선택 단계 테스트")
        self.test_button.setEnabled(False)
        self.validation_button = QPushButton("환경 검사")
        buttons = QHBoxLayout()
        buttons.addWidget(self.json_button)
        buttons.addWidget(self.retarget_button)
        buttons.addWidget(self.test_button)
        buttons.addWidget(self.validation_button)
        buttons.addStretch(1)

        splitter = QSplitter()
        splitter.addWidget(self.tree_view)
        splitter.addWidget(self.target_preview)
        splitter.addWidget(self.property_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 5)
        splitter.setStretchFactor(2, 3)
        splitter.setSizes((300, 500, 300))

        layout = QVBoxLayout(self)
        layout.addLayout(buttons)
        layout.addWidget(splitter, 1)

        self.tree_view.selectionModel().currentChanged.connect(self._selection_changed)
        self.json_button.clicked.connect(self.show_json_inspector)
        self.retarget_button.clicked.connect(self.retarget_selected_step)
        self.test_button.clicked.connect(self._request_step_test)
        self.validation_button.clicked.connect(self.validate_requested)

    def set_session(self, session: ProjectSession) -> None:
        self.session = session
        self.tree_model.set_workflow(session.workflow)
        self.tree_view.expandAll()
        if self.tree_model.rowCount() > 0:
            self.tree_view.setCurrentIndex(self.tree_model.index(0, 0))
        self.json_inspector.set_workflow(session.workflow)

    @Slot(object)
    def apply_command(self, command: object) -> bool:
        session = self.session
        if session is None:
            return False
        try:
            updated = self._editing_service.apply(session.workflow, command)  # type: ignore[arg-type]
        except (EditRejected, TypeError):
            return False
        self.session = ProjectSession(
            project_dir=session.project_dir,
            workflow=updated,
            loaded_revision=session.loaded_revision,
            dirty=True,
        )
        self.tree_model.set_workflow(updated)
        self.json_inspector.set_workflow(updated)
        self.edit_requested.emit(command)
        return True

    def show_validation(self, report: ValidationReport) -> None:
        self.tree_model.set_validation(report)

    def show_json_inspector(self) -> None:
        if self.session is not None:
            self.json_inspector.set_workflow(self.session.workflow)
        self.json_inspector.show()
        self.json_inspector.raise_()

    def selected_step(self) -> Step | None:
        return self.tree_model.step(self.tree_view.currentIndex())

    @Slot()
    def retarget_selected_step(self) -> None:
        step = self.selected_step()
        if isinstance(step, ActionStep):
            self.retarget_requested.emit(step.step_id)

    @Slot(QModelIndex, QModelIndex)
    def _selection_changed(self, current: QModelIndex, previous: QModelIndex) -> None:
        del previous
        step = self.tree_model.step(current)
        self.property_panel.set_step(step)
        session = self.session
        if session is None or step is None:
            return
        target = step.target if isinstance(step, ActionStep) else None
        self.target_preview.set_target(session.project_dir, step.step_id, target)

    @Slot()
    def _request_step_test(self) -> None:
        step_id = self.tree_model.step_id(self.tree_view.currentIndex())
        if step_id is not None:
            self.step_test_requested.emit(step_id)


__all__ = ["StepPropertySummary", "WorkflowEditor"]
