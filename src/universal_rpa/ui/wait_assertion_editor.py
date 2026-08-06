"""Builds the wait and assertion a step is verified with.

The adapter descriptor decides what may be offered here: a condition or
assertion the running adapter does not implement would pass edit-time and then
fail the run, so the combos are filled from the descriptor rather than from a
list typed into the UI.
"""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QFormLayout, QLineEdit, QSpinBox, QWidget

from universal_rpa.domain.conditions import AssertionSpec, ConditionSpec, WaitSpec
from universal_rpa.domain.targets import TargetSpec
from universal_rpa.ports.automation import AdapterDescriptor

#: Shown when the user wants no wait or no assertion on this step.
NONE_LABEL = "사용 안 함"


class WaitAssertionEditor(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.condition_combo = QComboBox()
        self.expected_input = QLineEdit()
        self.expected_input.setPlaceholderText("조건이 기대하는 값 (선택)")
        self.timeout_ms = QSpinBox()
        self.timeout_ms.setRange(1, 86_400_000)
        self.timeout_ms.setValue(30_000)
        self.assertion_combo = QComboBox()
        self.assertion_expected = QLineEdit()
        self.assertion_expected.setPlaceholderText("검증이 기대하는 값 (선택)")
        layout = QFormLayout(self)
        layout.addRow("대기 조건", self.condition_combo)
        layout.addRow("조건 기대값", self.expected_input)
        layout.addRow("대기 시간(ms)", self.timeout_ms)
        layout.addRow("결과 검증", self.assertion_combo)
        layout.addRow("검증 기대값", self.assertion_expected)

    def set_action(self, descriptor: AdapterDescriptor, action_type: str) -> None:
        self.condition_combo.clear()
        self.condition_combo.addItem(NONE_LABEL, None)
        for condition in sorted(descriptor.conditions):
            self.condition_combo.addItem(condition, condition)
        self.assertion_combo.clear()
        self.assertion_combo.addItem(NONE_LABEL, None)
        compatible = descriptor.assertions_by_action.get(action_type, frozenset())
        for assertion in sorted(compatible):
            kind = descriptor.assertion_input_kind[assertion]
            self.assertion_combo.addItem(f"{assertion} · {kind}", assertion)

    def set_wait(self, wait: WaitSpec | None) -> None:
        if wait is None:
            self.condition_combo.setCurrentIndex(0)
            self.expected_input.clear()
            return
        index = self.condition_combo.findData(wait.condition.condition_type)
        self.condition_combo.setCurrentIndex(max(index, 0))
        self.timeout_ms.setValue(wait.timeout_ms)
        expected = wait.condition.expected
        self.expected_input.setText("" if expected is None else str(expected))

    def set_assertions(self, assertions: tuple[AssertionSpec, ...]) -> None:
        if not assertions:
            self.assertion_combo.setCurrentIndex(0)
            self.assertion_expected.clear()
            return
        first = assertions[0]
        index = self.assertion_combo.findData(first.assertion_type)
        self.assertion_combo.setCurrentIndex(max(index, 0))
        self.assertion_expected.setText("" if first.expected is None else str(first.expected))

    def pending_wait(self, target: TargetSpec | None) -> WaitSpec | None:
        condition_type = self.condition_combo.currentData()
        if not isinstance(condition_type, str):
            return None
        expected = self.expected_input.text().strip()
        return WaitSpec(
            condition=ConditionSpec(
                condition_type=condition_type,
                target=target,
                expected=expected or None,
            ),
            timeout_ms=self.timeout_ms.value(),
        )

    def pending_assertions(self) -> tuple[AssertionSpec, ...]:
        assertion_type = self.assertion_combo.currentData()
        if not isinstance(assertion_type, str):
            return ()
        expected = self.assertion_expected.text().strip()
        return (AssertionSpec(assertion_type=assertion_type, expected=expected or None),)


__all__ = ["NONE_LABEL", "WaitAssertionEditor"]
