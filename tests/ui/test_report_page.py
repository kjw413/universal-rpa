from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from universal_rpa.application.reports import SafeRunReportDocument
from universal_rpa.ui.report_page import ReportPage

RUN_ID = UUID("00000000-0000-0000-0000-000000000951")
WORKFLOW_ID = UUID("00000000-0000-0000-0000-000000000952")
LOOP_ID = UUID("00000000-0000-0000-0000-000000000953")
NOW = datetime(2026, 8, 3, 9, 30, tzinfo=UTC)


def _document(**overrides: object) -> SafeRunReportDocument:
    payload: dict[str, object] = {
        "run_id": RUN_ID,
        "workflow_id": WORKFLOW_ID,
        "workflow_name": "월간 실적 집계",
        "workflow_revision": 4,
        "status": "partial",
        "started_at": NOW,
        "finished_at": NOW,
        "environment": {
            "interactive_desktop": True,
            "process_executable": "mis.exe",
            "window_class": "MisMain",
            "dpi_x": 96,
            "dpi_y": 96,
            "client_width": 1280,
            "client_height": 720,
            "monitor_scale": 1.0,
        },
        "total_iterations": 20,
        "successful_iterations": 18,
        "failed_iterations": 1,
        "skipped_iterations": 1,
        "action_count": 63,
        "outputs": (
            {
                "relative_path": "exports/out.csv",
                "format": "csv",
                "sheet_name": None,
                "row_count": 240,
                "sha256": "a" * 64,
                "headers_sha256": "b" * 64,
                "committed": True,
                "producer_step_id": str(UUID(int=7)),
                "producer_cursor": [{"loop_step_id": str(LOOP_ID), "row_index": 17}],
            },
        ),
        "failures": (
            {
                "step_id": str(UUID(int=9)),
                "step_label": "조회 버튼 클릭",
                "iteration_cursor": [{"loop_step_id": str(LOOP_ID), "row_index": 18}],
                "status": "failed",
                "attempt_count": 3,
                "error_code": "target_not_found",
                "safe_message": "대상 요소를 찾지 못했습니다.",
                "evidence": {"match_count": 0},
            },
        ),
        "last_checkpoint": f"{LOOP_ID}#17",
        "error_code": "target_not_found",
        "safe_message": "대상 요소를 찾지 못했습니다.",
    }
    payload.update(overrides)
    return SafeRunReportDocument.model_validate(payload)


@pytest.fixture
def page(qtbot: QtBot) -> ReportPage:
    widget = ReportPage()
    qtbot.addWidget(widget)
    widget.show()
    return widget


def _all_text(page: QWidget) -> str:
    parts: list[str] = []
    for child in page.findChildren(QWidget):
        reader = getattr(child, "text", None)
        if callable(reader):
            parts.append(str(reader()))
    table = page.findChild(QWidget, "failureTable")
    del table
    return "\n".join(parts)


def test_report_page_shows_safe_totals_outputs_and_failure_context(page: ReportPage) -> None:
    page.set_report(_document())

    text = _all_text(page)
    assert "월간 실적 집계" in text
    assert "20" in page.total_iterations_label.text()
    assert "18" in page.successful_iterations_label.text()
    assert "1" in page.failed_iterations_label.text()
    assert page.last_checkpoint_label.text() == f"{LOOP_ID}#17"
    assert page.output_table.rowCount() == 1
    assert page.output_table.item(0, 0).text() == "exports/out.csv"
    assert page.output_table.item(0, 3).text() == "240"
    assert page.failure_table.rowCount() == 1
    assert page.failure_table.item(0, 0).text() == "조회 버튼 클릭"
    assert "3" in page.failure_table.item(0, 2).text()
    assert page.failure_table.item(0, 3).text() == "target_not_found"


def test_report_page_clears_previous_content_between_runs(page: ReportPage) -> None:
    page.set_report(_document())
    page.set_report(_document(outputs=(), failures=(), status="success", failed_iterations=0))

    assert page.output_table.rowCount() == 0
    assert page.failure_table.rowCount() == 0
    assert "성공" in page.status_label.text()


def test_export_writes_only_the_safe_document(page: ReportPage, tmp_path: Path) -> None:
    document = _document()
    page.set_report(document)
    destination = tmp_path / "report.json"

    assert page.export_report(destination) is True

    written = json.loads(destination.read_text(encoding="utf-8"))
    assert written == json.loads(document.model_dump_json())
    assert "selector" not in destination.read_text(encoding="utf-8")


def test_export_is_rejected_before_a_report_exists(page: ReportPage, tmp_path: Path) -> None:
    assert page.export_report(tmp_path / "report.json") is False
    assert not (tmp_path / "report.json").exists()
    assert not page.export_button.isEnabled()


def test_report_page_never_renders_a_raw_evidence_secret(page: ReportPage) -> None:
    page.set_report(
        _document(
            failures=(
                {
                    "step_id": str(UUID(int=9)),
                    "step_label": "로그인",
                    "iteration_cursor": [],
                    "status": "failed",
                    "attempt_count": 1,
                    "error_code": "secret_missing",
                    "safe_message": "자격 증명을 찾을 수 없습니다.",
                    "evidence": {"password": "actual-password", "safe": {"match_count": 0}},
                },
            )
        )
    )

    assert "actual-password" not in _all_text(page)
