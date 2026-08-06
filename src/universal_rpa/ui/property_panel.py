from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from universal_rpa.application.editing import (
    EditCommand,
    PatchActionStep,
    RenameStep,
    SetStepValue,
)
from universal_rpa.domain.conditions import AssertionSpec
from universal_rpa.domain.values import (
    LiteralValue,
    RowBindingValue,
    SecretRefValue,
    ValueSpec,
    VariableValue,
)
from universal_rpa.domain.workflow import ActionStep, Step
from universal_rpa.ports.automation import AdapterDescriptor
from universal_rpa.ui.action_parameter_editor import ActionParameterEditor
from universal_rpa.ui.wait_assertion_editor import WaitAssertionEditor

_MODE_LABELS = {
    "literal": "고정값",
    "variable": "실행 변수",
    "row_binding": "반복 열",
    "secret_ref": "비밀값",
    "none": "값 없음",
}


class PropertyPanel(QWidget):
    command_ready = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.step_id: UUID | None = None
        self._step: Step | None = None
        self._credential_ref: str | None = None
        self._descriptors: dict[str, AdapterDescriptor] = {}
        self.label_input = QLineEdit()
        self.enabled_check = QCheckBox("실행")
        self.action_label = QLabel("-")
        self.target_label = QLabel("-")
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(tuple(_MODE_LABELS.values()))
        self.value_input = QLineEdit()
        self.failure_combo = QComboBox()
        self.failure_combo.addItems(("stop", "retry", "skip_iteration"))
        self.retry_count = QSpinBox()
        self.retry_count.setRange(0, 3)
        self.action_parameter_editor = ActionParameterEditor()
        self.wait_editor = WaitAssertionEditor()
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.apply_button = QPushButton("변경 적용")

        form = QFormLayout()
        form.addRow("단계 이름", self.label_input)
        form.addRow("활성 상태", self.enabled_check)
        form.addRow("작업", self.action_label)
        form.addRow("대상", self.target_label)
        form.addRow("값 종류", self.mode_combo)
        form.addRow("값/ID/열", self.value_input)
        form.addRow("실패 처리", self.failure_combo)
        form.addRow("재시도 횟수", self.retry_count)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.action_parameter_editor)
        layout.addWidget(self.wait_editor)
        layout.addWidget(self.error_label)
        layout.addWidget(self.apply_button)
        layout.addStretch(1)

        self.mode_combo.currentTextChanged.connect(self._mode_changed)
        self.apply_button.clicked.connect(self._emit_pending)
        self.set_step(None)

    def set_adapter_descriptors(self, descriptors: Mapping[str, AdapterDescriptor]) -> None:
        """Supply what each adapter can wait for, so only real conditions appear."""

        self._descriptors = dict(descriptors)
        self._refresh_wait_editor(self._step)

    def adapter_descriptors(self) -> Mapping[str, AdapterDescriptor]:
        return dict(self._descriptors)

    def _refresh_wait_editor(self, step: Step | None) -> None:
        if not isinstance(step, ActionStep):
            self.wait_editor.setEnabled(False)
            return
        descriptor = self._descriptors.get(step.action_type.split(".", 1)[0])
        self.wait_editor.setEnabled(descriptor is not None)
        if descriptor is None:
            return
        self.wait_editor.set_action(descriptor, step.action_type)
        self.wait_editor.set_wait(step.wait)
        self.wait_editor.set_assertions(
            tuple(item for item in step.assertions if isinstance(item, AssertionSpec))
        )

    def set_step(self, step: Step | None) -> None:
        self._step = step
        self.step_id = step.step_id if step is not None else None
        self._credential_ref = None
        enabled = step is not None
        self.label_input.setEnabled(enabled)
        self.enabled_check.setEnabled(enabled)
        self.mode_combo.setEnabled(isinstance(step, ActionStep))
        self.value_input.setEnabled(isinstance(step, ActionStep))
        self.action_parameter_editor.setEnabled(isinstance(step, ActionStep))
        if step is None:
            self.label_input.clear()
            self.action_label.setText("-")
            self.target_label.setText("-")
            return
        self.label_input.setText(step.label)
        self.enabled_check.setChecked(step.enabled)
        if not isinstance(step, ActionStep):
            self.action_label.setText(step.kind)
            self.target_label.setText("그룹 단계")
            return
        self.action_label.setText(step.action_type)
        self.target_label.setText(
            step.target.adapter_id if step.target is not None else "대상 없음"
        )
        mode = step.value.mode if step.value is not None else "none"
        self.mode_combo.blockSignals(True)
        self.mode_combo.setCurrentText(_MODE_LABELS[mode])
        self.mode_combo.blockSignals(False)
        if isinstance(step.value, LiteralValue):
            self.value_input.setText("" if step.value.value is None else str(step.value.value))
        elif isinstance(step.value, VariableValue):
            self.value_input.setText(step.value.variable_id)
        elif isinstance(step.value, RowBindingValue):
            self.value_input.setText(step.value.column_name)
        elif isinstance(step.value, SecretRefValue):
            self._credential_ref = step.value.credential_ref
            self.value_input.clear()
        else:
            self.value_input.clear()
        self.failure_combo.setCurrentText(step.failure_policy.mode)
        self.retry_count.setValue(step.failure_policy.retry_count)
        self.action_parameter_editor.set_action(step.action_type, step.parameters)
        self._refresh_wait_editor(step)
        self.error_label.clear()

    def select_credential_reference(self, reference: str) -> None:
        self._credential_ref = reference.strip() or None
        self.value_input.clear()

    def pending_command(self) -> EditCommand | None:
        step = self._step
        if step is None or self.step_id is None:
            return None
        label = self.label_input.text().strip()
        if not label:
            self.error_label.setText("단계 이름을 입력하세요.")
            return None
        if isinstance(step, ActionStep):
            parameters = self.action_parameter_editor.pending_parameters()
            if parameters is None:
                self.error_label.setText(self.action_parameter_editor.error_text)
                return None
            mode = self.mode_combo.currentText()
            current_mode = _MODE_LABELS[step.value.mode if step.value is not None else "none"]
            if mode != current_mode:
                value_text = self.value_input.text().strip()
                value: ValueSpec | None
                try:
                    if mode == "고정값":
                        value = LiteralValue(value=value_text)
                    elif mode == "실행 변수":
                        value = VariableValue(variable_id=value_text)
                    elif mode == "반복 열":
                        value = RowBindingValue(template=f"{{{{ row.{value_text} }}}}")
                    elif mode == "비밀값" and self._credential_ref is not None:
                        value = SecretRefValue(credential_ref=self._credential_ref)
                    elif mode == "값 없음":
                        value = None
                    else:
                        self.error_label.setText("값 종류에 필요한 값을 완성하세요.")
                        return None
                except ValueError:
                    self.error_label.setText("값 형식을 확인하세요.")
                    return None
                return SetStepValue(step.step_id, value)
            changes: dict[str, object] = {}
            if step.enabled != self.enabled_check.isChecked():
                changes["enabled"] = self.enabled_check.isChecked()
            if parameters != step.parameters:
                changes["parameters"] = parameters
            if self.failure_combo.currentText() != step.failure_policy.mode or (
                self.failure_combo.currentText() == "retry"
                and self.retry_count.value() != step.failure_policy.retry_count
            ):
                changes["failure_policy"] = {
                    "mode": self.failure_combo.currentText(),
                    "retry_count": self.retry_count.value()
                    if self.failure_combo.currentText() == "retry"
                    else 0,
                }
            wait = self.wait_editor.pending_wait(step.target)
            if wait != step.wait:
                changes["wait"] = wait
            assertions = self.wait_editor.pending_assertions()
            if assertions != step.assertions:
                changes["assertions"] = assertions
            if changes:
                return PatchActionStep(step.step_id, changes)
        if label != step.label:
            return RenameStep(step.step_id, label)
        return None

    def _mode_changed(self, label: str) -> None:
        self._credential_ref = None
        self.value_input.clear()
        self.value_input.setEchoMode(
            QLineEdit.EchoMode.Password if label == "비밀값" else QLineEdit.EchoMode.Normal
        )

    def _emit_pending(self) -> None:
        command = self.pending_command()
        if command is not None:
            self.error_label.clear()
            self.command_ready.emit(command)


__all__ = ["PropertyPanel"]
