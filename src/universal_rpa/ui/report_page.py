"""The Report page: renders and exports one already-redacted run document.

The page holds a :class:`SafeRunReportDocument` and nothing else.  It never sees
a ``RunReport``, a target, a selector, an input value, or a clipboard body, so
there is no path by which unredacted material can reach the screen or an export
file — the projector is the only producer of what is shown here.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from PySide6.QtCore import Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from universal_rpa.application.reports import SafeRunReportDocument
from universal_rpa.domain.types import JsonValue, thaw_json

_STATUS_LABELS = {
    "success": "성공",
    "partial": "부분 성공",
    "failed": "실패",
    "cancelled": "중지됨",
}

_OUTPUT_HEADERS = ("산출물 경로", "형식", "시트", "행 수", "내용 해시", "머리글 해시")
_FAILURE_HEADERS = ("단계", "반복 위치", "시도", "오류 코드", "안내")


def _read_only_table(headers: tuple[str, ...], name: str) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setObjectName(name)
    table.setHorizontalHeaderLabels(list(headers))
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.verticalHeader().setVisible(False)
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    return table


def _text(value: JsonValue | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "예" if value else "아니오"
    return str(value)


def _cursor_text(value: JsonValue | None) -> str:
    if not isinstance(value, (list, tuple)):
        return ""
    parts: list[str] = []
    for item in value:
        if isinstance(item, dict):
            parts.append(f"{item.get('loop_step_id')}#{item.get('row_index')}")
    return " / ".join(parts)


class ReportPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._document: SafeRunReportDocument | None = None

        title = QLabel("실행 보고서")
        title.setObjectName("page-title")

        self.workflow_label = QLabel("")
        self.workflow_label.setObjectName("reportWorkflowLabel")
        self.status_label = QLabel("")
        self.status_label.setObjectName("reportStatusLabel")
        self.run_id_label = QLabel("")
        self.run_id_label.setObjectName("reportRunIdLabel")
        self.total_iterations_label = QLabel("0")
        self.successful_iterations_label = QLabel("0")
        self.failed_iterations_label = QLabel("0")
        self.skipped_iterations_label = QLabel("0")
        self.action_count_label = QLabel("0")
        self.last_checkpoint_label = QLabel("")
        self.last_checkpoint_label.setObjectName("reportCheckpointLabel")
        self.safe_message_label = QLabel("")
        self.safe_message_label.setObjectName("reportMessageLabel")
        self.safe_message_label.setWordWrap(True)
        self.environment_label = QLabel("")
        self.environment_label.setObjectName("reportEnvironmentLabel")
        self.environment_label.setWordWrap(True)

        summary = QGroupBox("요약")
        summary_layout = QFormLayout(summary)
        summary_layout.addRow("업무", self.workflow_label)
        summary_layout.addRow("실행 ID", self.run_id_label)
        summary_layout.addRow("상태", self.status_label)
        summary_layout.addRow("전체 반복", self.total_iterations_label)
        summary_layout.addRow("성공 반복", self.successful_iterations_label)
        summary_layout.addRow("실패 반복", self.failed_iterations_label)
        summary_layout.addRow("건너뛴 반복", self.skipped_iterations_label)
        summary_layout.addRow("실행한 단계 수", self.action_count_label)
        summary_layout.addRow("마지막 체크포인트", self.last_checkpoint_label)
        summary_layout.addRow("실행 환경", self.environment_label)
        summary_layout.addRow("안내", self.safe_message_label)

        self.output_table = _read_only_table(_OUTPUT_HEADERS, "outputTable")
        self.failure_table = _read_only_table(_FAILURE_HEADERS, "failureTable")

        self.export_button = QPushButton("보고서 내보내기")
        self.export_button.setObjectName("exportReportButton")
        self.export_button.setEnabled(False)
        actions = QHBoxLayout()
        actions.addWidget(self.export_button)
        actions.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(summary)
        layout.addWidget(QLabel("산출물"))
        layout.addWidget(self.output_table, 1)
        layout.addWidget(QLabel("실패한 단계"))
        layout.addWidget(self.failure_table, 1)
        layout.addLayout(actions)

    @property
    def document(self) -> SafeRunReportDocument | None:
        return self._document

    @Slot(object)
    def set_report(self, report: object) -> None:
        if not isinstance(report, SafeRunReportDocument):
            return
        self._document = report
        self.workflow_label.setText(f"{report.workflow_name} (rev {report.workflow_revision})")
        self.run_id_label.setText(str(report.run_id))
        self.status_label.setText(_STATUS_LABELS.get(report.status, report.status))
        self.total_iterations_label.setText(str(report.total_iterations))
        self.successful_iterations_label.setText(str(report.successful_iterations))
        self.failed_iterations_label.setText(str(report.failed_iterations))
        self.skipped_iterations_label.setText(str(report.skipped_iterations))
        self.action_count_label.setText(str(report.action_count))
        self.last_checkpoint_label.setText(report.last_checkpoint or "")
        self.safe_message_label.setText(report.safe_message)
        self.environment_label.setText(self._environment_text(report))
        self._fill_outputs(report)
        self._fill_failures(report)
        self.export_button.setEnabled(True)

    def export_report(self, destination: Path) -> bool:
        """Write the safe document atomically; refuse when there is nothing safe."""

        document = self._document
        if document is None:
            return False
        target = Path(destination)
        temporary = target.with_suffix(target.suffix + ".tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                stream.write(document.model_dump_json(indent=2))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        except OSError:
            temporary.unlink(missing_ok=True)
            return False
        return True

    @staticmethod
    def _environment_text(report: SafeRunReportDocument) -> str:
        environment = thaw_json(report.environment)
        if not isinstance(environment, dict):
            return ""
        return " · ".join(f"{key}={_text(value)}" for key, value in sorted(environment.items()))

    def _fill_outputs(self, report: SafeRunReportDocument) -> None:
        self.output_table.setRowCount(len(report.outputs))
        for row, frozen in enumerate(report.outputs):
            output = thaw_json(frozen)
            if not isinstance(output, dict):
                continue
            values = (
                _text(output.get("relative_path")),
                _text(output.get("format")),
                _text(output.get("sheet_name")),
                _text(output.get("row_count")),
                _text(output.get("sha256")),
                _text(output.get("headers_sha256")),
            )
            for column, value in enumerate(values):
                self.output_table.setItem(row, column, QTableWidgetItem(value))

    def _fill_failures(self, report: SafeRunReportDocument) -> None:
        self.failure_table.setRowCount(len(report.failures))
        for row, frozen in enumerate(report.failures):
            failure = thaw_json(frozen)
            if not isinstance(failure, dict):
                continue
            values = (
                _text(failure.get("step_label")),
                _cursor_text(failure.get("iteration_cursor")),
                _text(failure.get("attempt_count")),
                _text(failure.get("error_code")),
                _text(failure.get("safe_message")),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 4:
                    item.setToolTip(value)
                self.failure_table.setItem(row, column, item)

    def report_json(self) -> str:
        document = self._document
        return (
            ""
            if document is None
            else json.dumps(json.loads(document.model_dump_json()), ensure_ascii=False, indent=2)
        )


__all__ = ["ReportPage"]
