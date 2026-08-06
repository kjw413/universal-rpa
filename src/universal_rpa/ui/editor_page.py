from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from PySide6.QtCore import QModelIndex, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QPushButton,
    QSplitter,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from universal_rpa.adapters.registry import AdapterRegistry
from universal_rpa.application.editing import (
    EditRejected,
    ReplaceTarget,
    SetStepValue,
    UpsertDataSource,
    UpsertVariable,
    WorkflowEditingService,
    WrapInLoop,
)
from universal_rpa.application.normalization import NormalizationResult
from universal_rpa.application.projects import ProjectService, ProjectSession
from universal_rpa.application.recording_privacy import RecordingPrivacyService
from universal_rpa.application.validation import ValidationService
from universal_rpa.domain.errors import ValidationReport
from universal_rpa.domain.values import SecretRefValue
from universal_rpa.domain.workflow import ActionStep, Step
from universal_rpa.infrastructure.target_preview_store import (
    MaskedPreviewVariant,
    TargetPreviewStore,
)
from universal_rpa.ports.automation import TargetCaptureResult
from universal_rpa.ui.json_inspector import JsonInspector
from universal_rpa.ui.loop_dialog import LoopDialog
from universal_rpa.ui.property_panel import PropertyPanel
from universal_rpa.ui.step_tree_model import WorkflowTreeModel
from universal_rpa.ui.target_picker import TargetPicker
from universal_rpa.ui.target_preview import (
    MissingTargetPreviewResolver,
    TargetPreview,
    TargetPreviewResolver,
)
from universal_rpa.ui.variable_dialog import VariableDialog


class ProjectSaveFailed(RuntimeError):
    pass


class WorkflowEditor(QWidget):
    edit_requested = Signal(object)
    step_test_requested = Signal(object)
    retarget_requested = Signal(object)
    validate_requested = Signal()

    def __init__(
        self,
        editing_service: WorkflowEditingService,
        preview_resolver: TargetPreviewResolver | None = None,
        *,
        validation_service: ValidationService | None = None,
        project_service: ProjectService | None = None,
        preview_store: TargetPreviewStore | None = None,
        privacy_service: RecordingPrivacyService | None = None,
        adapter_registry: AdapterRegistry | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._editing_service = editing_service
        self._validation_service = validation_service
        self._project_service = project_service
        self.preview_store = preview_store
        self._privacy_service = privacy_service
        self._source_session_ids: tuple[UUID, ...] = ()
        self.session: ProjectSession | None = None
        self.target_picker_factory: Callable[[], TargetPicker] | None = None
        self.variable_dialog_factory: Callable[[], VariableDialog] = VariableDialog
        self.loop_dialog_factory: Callable[[], LoopDialog] = LoopDialog
        self.tree_model = WorkflowTreeModel()
        self.tree_view = QTreeView()
        self.tree_view.setModel(self.tree_model)
        self.tree_view.setHeaderHidden(True)
        # A loop normally covers several consecutive steps, so the tree has to
        # let the user pick more than one.
        self.tree_view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        resolver = preview_store or preview_resolver or MissingTargetPreviewResolver()
        self.target_preview = TargetPreview(resolver)
        self.property_panel = PropertyPanel()
        if adapter_registry is not None:
            self.property_panel.set_adapter_descriptors(
                {descriptor.adapter_id: descriptor for descriptor in adapter_registry.descriptors()}
            )
        self.json_inspector = JsonInspector(self)

        self.json_button = QPushButton("JSON 보기")
        self.retarget_button = QPushButton("대상 다시 지정")
        self.test_button = QPushButton("선택 단계 테스트")
        self.test_button.setEnabled(False)
        self.validation_button = QPushButton("환경 검사")
        self.variable_button = QPushButton("실행 변수 추가")
        self.loop_button = QPushButton("선택 단계 반복 만들기")
        buttons = QHBoxLayout()
        buttons.addWidget(self.json_button)
        buttons.addWidget(self.retarget_button)
        buttons.addWidget(self.test_button)
        buttons.addWidget(self.validation_button)
        buttons.addWidget(self.variable_button)
        buttons.addWidget(self.loop_button)
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
        self.property_panel.command_ready.connect(self.apply_command)
        self.json_button.clicked.connect(self.show_json_inspector)
        self.retarget_button.clicked.connect(self.retarget_selected_step)
        self.test_button.clicked.connect(self._request_step_test)
        self.validation_button.clicked.connect(self.validate_requested)
        self.variable_button.clicked.connect(self.add_variable)
        self.loop_button.clicked.connect(self.wrap_selection_in_loop)

    def set_session(self, session: ProjectSession) -> None:
        self.session = session
        self._refresh_workflow(selected_step_id=None)

    @Slot(object)
    def apply_command(self, command: object) -> bool:
        session = self.session
        if session is None:
            return False
        if isinstance(command, SetStepValue) and isinstance(command.value, SecretRefValue):
            return self._apply_secret_command(command)
        try:
            updated = self._editing_service.apply(session.workflow, command)  # type: ignore[arg-type]
        except (EditRejected, TypeError):
            return False
        selected = getattr(command, "step_id", None)
        self.session = ProjectSession(
            project_dir=session.project_dir,
            workflow=updated,
            loaded_revision=session.loaded_revision,
            dirty=True,
        )
        self._refresh_workflow(selected_step_id=selected if isinstance(selected, UUID) else None)
        self.edit_requested.emit(command)
        return True

    def _apply_secret_command(self, command: SetStepValue) -> bool:
        session = self.session
        privacy = self._privacy_service
        project_service = self._project_service
        store = self.preview_store
        step = self.tree_model.step(self.tree_model.index_for_step(command.step_id))
        if (
            session is None
            or privacy is None
            or project_service is None
            or store is None
            or not isinstance(step, ActionStep)
        ):
            return False
        variant: MaskedPreviewVariant | None = None
        try:
            privacy.purge_before_secret_mode(
                self._source_session_ids or None,
                allow_retained=True,
            )
            secured_target = step.target
            if secured_target is not None:
                secured_target, variant = store.stage_secret_mask(
                    session.project_dir,
                    step.step_id,
                    secured_target,
                )
            updated = session.workflow
            if secured_target != step.target:
                updated = self._editing_service.apply(
                    updated,
                    ReplaceTarget(step.step_id, secured_target),
                )
            updated = self._editing_service.apply(updated, command)
            if variant is not None:
                store.commit_variant(variant)
            else:
                store.delete_variants(session.project_dir, step.step_id)
            saved = project_service.save(project_service.with_workflow(session, updated))
        except Exception:
            if variant is not None:
                store.discard_variant(variant)
            try:
                store.delete_variants(session.project_dir, step.step_id)
            except Exception:
                pass
            return False
        self._source_session_ids = ()
        self.session = saved
        self._refresh_workflow(selected_step_id=step.step_id)
        self.edit_requested.emit(command)
        return True

    def apply_capture(self, capture: TargetCaptureResult) -> None:
        session = self.session
        step = self.selected_step()
        store = self.preview_store
        project_service = self._project_service
        if (
            session is None
            or not isinstance(step, ActionStep)
            or capture.target is None
            or store is None
            or project_service is None
        ):
            raise ProjectSaveFailed("대상 변경을 저장할 준비가 되지 않았습니다.")
        variant: MaskedPreviewVariant | None = None
        try:
            variant = store.stage_masked(session.project_dir, step.step_id, capture)
            updated = self._editing_service.apply(
                session.workflow,
                ReplaceTarget(step.step_id, capture.target),
            )
            candidate = project_service.with_workflow(session, updated)
            saved = project_service.save(candidate)
            store.commit_variant(variant)
        except Exception:
            if variant is not None:
                store.discard_variant(variant)
            raise ProjectSaveFailed("새 대상과 미리보기를 저장하지 못했습니다.") from None
        self.session = saved
        self._refresh_workflow(selected_step_id=step.step_id)
        self.edit_requested.emit(ReplaceTarget(step.step_id, capture.target))

    def remember_recording_result(self, result: object) -> None:
        if isinstance(result, NormalizationResult):
            self._source_session_ids = (result.session_id,)

    def show_validation(self, report: ValidationReport) -> None:
        self.tree_model.set_validation(report)

    def show_json_inspector(self) -> None:
        if self.session is not None:
            self.json_inspector.set_workflow(self.session.workflow)
        self.json_inspector.show()
        self.json_inspector.raise_()

    def selected_step(self) -> Step | None:
        return self.tree_model.step(self.tree_view.currentIndex())

    def change_selected_value_mode(self, mode: str) -> None:
        labels = {
            "literal": "고정값",
            "variable": "실행 변수",
            "row_binding": "반복 열",
            "secret_ref": "비밀값",
            "none": "값 없음",
        }
        label = labels.get(mode)
        if label is not None:
            self.property_panel.mode_combo.setCurrentText(label)

    @Slot()
    def add_variable(self) -> None:
        """Define a run variable so a step's value can change per run."""

        if self.session is None:
            return
        dialog = self.variable_dialog_factory()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        variable = dialog.variable_definition()
        if variable is None:
            return
        self.apply_command(UpsertVariable(variable))

    @Slot()
    def wrap_selection_in_loop(self) -> None:
        """Repeat the selected steps once per row of a data source."""

        step_ids = self.selected_step_ids()
        if self.session is None or not step_ids:
            return
        dialog = self.loop_dialog_factory()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        data_source = dialog.data_source()
        if data_source is None:
            return
        # The source has to exist before the loop names it, so this is two
        # commands: a rejected wrap must not leave an orphan source behind.
        if not self.apply_command(UpsertDataSource(data_source)):
            return
        if not self.apply_command(
            WrapInLoop(step_ids, data_source.data_source_id, dialog.loop_label())
        ):
            self._rollback_data_source(data_source.data_source_id)

    def selected_step_ids(self) -> tuple[UUID, ...]:
        found: list[UUID] = []
        for index in self.tree_view.selectionModel().selectedIndexes():
            step_id = self.tree_model.step_id(index)
            if step_id is not None and step_id not in found:
                found.append(step_id)
        return tuple(found)

    def _rollback_data_source(self, data_source_id: str) -> None:
        session = self.session
        if session is None:
            return
        remaining = tuple(
            source
            for source in session.workflow.data_sources
            if source.data_source_id != data_source_id
        )
        self.session = ProjectSession(
            project_dir=session.project_dir,
            workflow=session.workflow.model_copy(update={"data_sources": remaining}),
            loaded_revision=session.loaded_revision,
            dirty=session.dirty,
        )
        self._refresh_workflow(selected_step_id=None)

    @Slot()
    def retarget_selected_step(self) -> None:
        step = self.selected_step()
        if not isinstance(step, ActionStep):
            return
        factory = self.target_picker_factory
        if factory is None:
            self.retarget_requested.emit(step.step_id)
            return
        picker = factory()
        if picker.exec() != TargetPicker.DialogCode.Accepted:
            return
        capture = picker.captured_result()
        if capture is not None:
            self.apply_capture(capture)

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

    def _refresh_workflow(self, selected_step_id: UUID | None) -> None:
        session = self.session
        if session is None:
            return
        self.tree_model.set_workflow(session.workflow)
        self.tree_view.expandAll()
        if self._validation_service is not None:
            self.tree_model.set_validation(
                self._validation_service.validate_static(session.workflow)
            )
        selected = (
            self.tree_model.index_for_step(selected_step_id)
            if selected_step_id is not None
            else self.tree_model.index(0, 0)
        )
        if selected.isValid():
            self.tree_view.setCurrentIndex(selected)
        self.json_inspector.set_workflow(session.workflow)


__all__ = ["ProjectSaveFailed", "WorkflowEditor"]
