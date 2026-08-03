from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QSpinBox


class LoopDialog(QDialog):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("반복 실행 설정")
        self.max_iterations = QSpinBox()
        self.max_iterations.setRange(1, 10_000)
        self.max_iterations.setValue(1_000)
        self.max_runtime_seconds = QSpinBox()
        self.max_runtime_seconds.setRange(1, 86_400)
        self.max_runtime_seconds.setValue(7_200)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout = QFormLayout(self)
        layout.addRow("최대 반복 횟수", self.max_iterations)
        layout.addRow("최대 실행 시간(초)", self.max_runtime_seconds)
        layout.addRow(self.buttons)


__all__ = ["LoopDialog"]
