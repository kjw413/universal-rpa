from __future__ import annotations

from pydantic import TypeAdapter, ValidationError
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
)

from universal_rpa.domain.workflow import DataSourceSpec

_DATA_SOURCE = TypeAdapter[DataSourceSpec](DataSourceSpec)


class LoopDialog(QDialog):
    """Collects the one data source a loop repeats over, plus the run limits.

    A loop is meaningless without a source of rows, so the dialog produces the
    :class:`DataSourceSpec` too rather than leaving the caller to invent one.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("반복 실행 설정")
        self.label_input = QLineEdit()
        self.data_source_id = QLineEdit()
        self.source_type = QComboBox()
        self.source_type.addItems(("csv", "xlsx", "inline"))
        self.path_input = QLineEdit()
        self.path_input.setPlaceholderText("프로젝트 폴더 기준 상대 경로")
        self.encoding = QComboBox()
        self.encoding.addItems(("utf-8-sig", "utf-8", "cp949"))
        self.sheet_name = QLineEdit()
        self.inline_headers = QLineEdit()
        self.inline_headers.setPlaceholderText("쉼표로 구분한 열 이름")
        self.inline_rows = QLineEdit()
        self.inline_rows.setPlaceholderText("행은 세미콜론, 값은 쉼표로 구분")
        self.max_iterations = QSpinBox()
        self.max_iterations.setRange(1, 10_000)
        self.max_iterations.setValue(1_000)
        self.max_runtime_seconds = QSpinBox()
        self.max_runtime_seconds.setRange(1, 86_400)
        self.max_runtime_seconds.setValue(7_200)
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self._validate_accept)
        self.buttons.rejected.connect(self.reject)
        layout = QFormLayout(self)
        layout.addRow("반복 이름", self.label_input)
        layout.addRow("데이터 소스 ID", self.data_source_id)
        layout.addRow("소스 종류", self.source_type)
        layout.addRow("파일 경로", self.path_input)
        layout.addRow("CSV 인코딩", self.encoding)
        layout.addRow("시트 이름", self.sheet_name)
        layout.addRow("인라인 열 이름", self.inline_headers)
        layout.addRow("인라인 행", self.inline_rows)
        layout.addRow("최대 반복 횟수", self.max_iterations)
        layout.addRow("최대 실행 시간(초)", self.max_runtime_seconds)
        layout.addRow(self.error_label)
        layout.addRow(self.buttons)

    def loop_label(self) -> str:
        return self.label_input.text().strip()

    def data_source(self) -> DataSourceSpec | None:
        source_type = self.source_type.currentText()
        payload: dict[str, object] = {
            "source_type": source_type,
            "data_source_id": self.data_source_id.text().strip(),
            "label": self.loop_label(),
        }
        if source_type == "csv":
            payload["path"] = self.path_input.text().strip()
            payload["encoding"] = self.encoding.currentText()
        elif source_type == "xlsx":
            payload["path"] = self.path_input.text().strip()
            payload["sheet_name"] = self.sheet_name.text().strip()
        else:
            headers = tuple(
                item.strip() for item in self.inline_headers.text().split(",") if item.strip()
            )
            rows = tuple(
                tuple(cell.strip() for cell in row.split(","))
                for row in self.inline_rows.text().split(";")
                if row.strip()
            )
            payload["headers"] = headers
            payload["rows"] = rows
        try:
            return _DATA_SOURCE.validate_python(payload)
        except (ValidationError, ValueError):
            return None

    def _validate_accept(self) -> None:
        if not self.loop_label() or self.data_source() is None:
            self.error_label.setText("반복 이름과 데이터 소스를 확인하세요.")
            return
        self.accept()


__all__ = ["LoopDialog"]
