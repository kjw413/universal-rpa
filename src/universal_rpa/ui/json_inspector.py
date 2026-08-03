from __future__ import annotations

from PySide6.QtWidgets import QDialog, QPlainTextEdit, QVBoxLayout, QWidget

from universal_rpa.application.workflow_codec import dump_workflow
from universal_rpa.domain.workflow import Workflow


class JsonInspector(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("워크플로 JSON 검사")
        self.resize(760, 620)
        self.text_edit = QPlainTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout = QVBoxLayout(self)
        layout.addWidget(self.text_edit)

    def set_workflow(self, workflow: Workflow) -> None:
        self.text_edit.setPlainText(dump_workflow(workflow).decode("utf-8"))


__all__ = ["JsonInspector"]
