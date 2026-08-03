from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
)

from universal_rpa.domain.values import VariableDefinition


class VariableDialog(QDialog):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("실행 변수")
        self.variable_id = QLineEdit()
        self.label_input = QLineEdit()
        self.value_type = QComboBox()
        self.value_type.addItems(("text", "date", "integer", "decimal", "path", "choice", "secret"))
        self.source_type = QComboBox()
        self.source_type.addItems(
            (
                "run_input",
                "fixed_default",
                "inline_options",
                "csv_column",
                "xlsx_column",
                "date_rule",
                "credential_ref",
            )
        )
        self.source_value = QLineEdit()
        self.data_source_id = QLineEdit()
        self.column_name = QLineEdit()
        self.error_label = QLabel()
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self._validate_accept)
        self.buttons.rejected.connect(self.reject)
        layout = QFormLayout(self)
        layout.addRow("변수 ID", self.variable_id)
        layout.addRow("화면 이름", self.label_input)
        layout.addRow("값 종류", self.value_type)
        layout.addRow("입력 방식", self.source_type)
        layout.addRow("기본값/선택지/자격증명", self.source_value)
        layout.addRow("데이터 소스 ID", self.data_source_id)
        layout.addRow("열 이름", self.column_name)
        layout.addRow(self.error_label)
        layout.addRow(self.buttons)

    def variable_definition(self) -> VariableDefinition | None:
        source_type = self.source_type.currentText()
        source: dict[str, object] = {"source_type": source_type}
        raw = self.source_value.text().strip()
        if source_type == "run_input":
            source["required"] = True
        elif source_type == "fixed_default":
            value_type = self.value_type.currentText()
            if value_type == "integer":
                try:
                    value: object = int(raw)
                except ValueError:
                    return None
            elif value_type == "decimal":
                try:
                    value = float(raw)
                except ValueError:
                    return None
            else:
                value = raw
            source["value"] = value
        elif source_type == "inline_options":
            source["options"] = tuple(item.strip() for item in raw.split(",") if item.strip())
        elif source_type in {"csv_column", "xlsx_column"}:
            source["data_source_id"] = self.data_source_id.text().strip()
            source["column_name"] = self.column_name.text().strip()
            source["required"] = True
        elif source_type == "date_rule":
            source["expression"] = {"operation": raw or "today"}
        else:
            source["credential_ref"] = raw
        try:
            return VariableDefinition.model_validate(
                {
                    "variable_id": self.variable_id.text().strip(),
                    "label": self.label_input.text().strip(),
                    "value_type": self.value_type.currentText(),
                    "source": source,
                }
            )
        except ValueError:
            return None

    def _validate_accept(self) -> None:
        if self.variable_definition() is None:
            self.error_label.setText("변수 종류와 입력 방식을 확인하세요.")
            return
        self.accept()


__all__ = ["VariableDialog"]
