from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QFormLayout, QSpinBox, QWidget

from universal_rpa.ports.automation import AdapterDescriptor


class WaitAssertionEditor(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.timeout_ms = QSpinBox()
        self.timeout_ms.setRange(1, 86_400_000)
        self.timeout_ms.setValue(30_000)
        self.assertion_combo = QComboBox()
        layout = QFormLayout(self)
        layout.addRow("대기 시간(ms)", self.timeout_ms)
        layout.addRow("결과 검증", self.assertion_combo)

    def set_action(self, descriptor: AdapterDescriptor, action_type: str) -> None:
        self.assertion_combo.clear()
        compatible = descriptor.assertions_by_action.get(action_type, frozenset())
        for assertion in sorted(compatible):
            kind = descriptor.assertion_input_kind[assertion]
            self.assertion_combo.addItem(f"{assertion} · {kind}", assertion)


__all__ = ["WaitAssertionEditor"]
