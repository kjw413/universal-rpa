from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import QElapsedTimer, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from universal_rpa.application.editing import EditRejected, ImportCandidates
from universal_rpa.application.normalization import (
    CandidateLiteralValue,
    CandidateSecretValue,
    NormalizationResult,
    NormalizationService,
    StepCandidate,
)
from universal_rpa.application.recording import RecordingService
from universal_rpa.domain.recording import RecordingTarget
from universal_rpa.domain.values import LiteralValue, ValueSpec
from universal_rpa.ports.context import WindowContextPort
from universal_rpa.ports.repositories import RecordingStorePort
from universal_rpa.ui.workers import RecordingWorker, WorkerFailure


class RecorderPage(QWidget):
    recording_completed = Signal(object)
    candidates_reviewed = Signal(object)
    start_requested = Signal(object)
    pause_requested = Signal()
    resume_requested = Signal()
    stop_requested = Signal()

    def __init__(
        self,
        window_catalog: WindowContextPort,
        recording_service: RecordingService,
        normalization_service: NormalizationService,
        recording_store: RecordingStorePort,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._window_catalog = window_catalog
        self._recording_service = recording_service
        self._normalization_service = normalization_service
        self._recording_store = recording_store
        self._targets: tuple[RecordingTarget, ...] = ()
        self._result: NormalizationResult | None = None
        self._candidate_inputs: list[tuple[StepCandidate, QLineEdit, QLineEdit]] = []
        self._thread: QThread | None = None
        self._worker: RecordingWorker | None = None
        self._active = False
        self.recording_worker_start_count = 0

        title = QLabel("업무 기록")
        title.setObjectName("page-title")
        description = QLabel(
            "자동화할 Windows 창을 선택하고 평소처럼 마우스와 키보드로 업무를 수행하세요."
        )
        description.setWordWrap(True)

        self.target_combo = QComboBox()
        self.target_combo.setPlaceholderText("대상 창을 선택하세요")
        self.refresh_button = QPushButton("창 목록 새로고침")
        target_row = QHBoxLayout()
        target_row.addWidget(self.target_combo, 1)
        target_row.addWidget(self.refresh_button)

        self.start_button = QPushButton("기록 시작")
        self.pause_button = QPushButton("일시정지")
        self.stop_button = QPushButton("기록 종료")
        self.pause_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        controls = QHBoxLayout()
        controls.addWidget(self.start_button)
        controls.addWidget(self.pause_button)
        controls.addWidget(self.stop_button)
        controls.addStretch(1)

        self.banner = QLabel()
        self.banner.setObjectName("recording-banner")
        self.banner.setWordWrap(True)
        self.banner.hide()
        self.validation_text = QLabel()
        self.validation_text.setObjectName("validation-text")
        self.validation_text.setWordWrap(True)

        self.review_table = QTableWidget(0, 4)
        self.review_table.setHorizontalHeaderLabels(
            ("감지 동작", "단계 이름", "값/자격증명", "대상")
        )
        self.review_table.setVisible(False)
        self.import_button = QPushButton("검토 내용을 편집기로 가져오기")
        self.import_button.setVisible(False)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(description)
        layout.addLayout(target_row)
        layout.addLayout(controls)
        layout.addWidget(self.banner)
        layout.addWidget(self.validation_text)
        layout.addWidget(self.review_table, 1)
        layout.addWidget(self.import_button)

        self._elapsed = QElapsedTimer()
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(500)
        self._elapsed_timer.timeout.connect(self._update_banner)

        self.refresh_button.clicked.connect(self.refresh_targets)
        self.start_button.clicked.connect(self._start)
        self.pause_button.clicked.connect(self._toggle_pause)
        self.stop_button.clicked.connect(self._stop)
        self.import_button.clicked.connect(self._import_review)
        self.refresh_targets()

    @Slot()
    def refresh_targets(self) -> None:
        if self._active:
            return
        try:
            targets = self._window_catalog.list_recordable_windows()
        except Exception:
            self.validation_text.setText("현재 Windows 창 목록을 불러올 수 없습니다.")
            targets = ()
        self.set_targets(targets)

    def set_targets(self, targets: Sequence[RecordingTarget]) -> None:
        self._targets = tuple(targets)
        self.target_combo.clear()
        for target in self._targets:
            executable = Path(target.process_executable).name
            self.target_combo.addItem(f"{executable} · {target.window_title}", target)
        self.target_combo.setCurrentIndex(-1)

    @Slot()
    def _start(self) -> None:
        target = self.target_combo.currentData()
        if not isinstance(target, RecordingTarget):
            self.validation_text.setText("기록할 대상 창을 선택하세요.")
            return
        self._ensure_worker()
        self.recording_worker_start_count += 1
        self._active = True
        self._result = None
        self.validation_text.clear()
        self.review_table.setVisible(False)
        self.import_button.setVisible(False)
        self.target_combo.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.pause_button.setEnabled(True)
        self.stop_button.setEnabled(True)
        self.banner.setProperty("state", "recording")
        self.banner.show()
        self._elapsed.start()
        self._elapsed_timer.start()
        self._update_banner()
        self.start_requested.emit(target)

    @Slot()
    def _toggle_pause(self) -> None:
        if self.banner.property("state") == "paused":
            self.resume_requested.emit()
        else:
            self.pause_requested.emit()

    @Slot()
    def _stop(self) -> None:
        if not self._active:
            return
        self.stop_button.setEnabled(False)
        self.validation_text.setText("기록을 안전하게 마무리하고 있습니다…")
        self.stop_requested.emit()

    @Slot(str)
    def on_state_changed(self, state: str) -> None:
        if state not in {"recording", "paused", "stopping", "stopped"}:
            return
        self.banner.setProperty("state", state)
        self.pause_button.setText("계속" if state == "paused" else "일시정지")
        self.pause_button.setEnabled(state in {"recording", "paused"})
        if state in {"recording", "paused", "stopping"}:
            self.banner.show()
        self._update_banner()

    @Slot(object)
    def _on_completed(self, result: object) -> None:
        if not isinstance(result, NormalizationResult):
            self._on_failed(WorkerFailure("기록 결과를 확인할 수 없습니다."))
            return
        self._active = False
        self._elapsed_timer.stop()
        self._result = result
        self._reset_controls()
        self.banner.hide()
        self.validation_text.setText(
            f"{len(result.candidates)}개 동작을 감지했습니다. 이름과 변동 값을 검토하세요."
        )
        self._populate_review(result)
        self.recording_completed.emit(result)

    @Slot(object)
    def _on_failed(self, failure: object) -> None:
        self._active = False
        self._elapsed_timer.stop()
        self._reset_controls()
        self.banner.hide()
        message = (
            failure.safe_message
            if isinstance(failure, WorkerFailure)
            else "기록을 완료하지 못했습니다."
        )
        self.validation_text.setText(message)

    def _ensure_worker(self) -> None:
        if self._thread is not None:
            return
        thread = QThread(self)
        worker = RecordingWorker(
            self._recording_service,
            self._normalization_service,
            self._recording_store,
        )
        worker.moveToThread(thread)
        self.start_requested.connect(worker.start)
        self.pause_requested.connect(worker.pause)
        self.resume_requested.connect(worker.resume)
        self.stop_requested.connect(worker.stop)
        worker.state_changed.connect(self.on_state_changed)
        worker.completed.connect(self._on_completed)
        worker.failed.connect(self._on_failed)
        thread.finished.connect(worker.deleteLater)
        thread.start()
        self._thread = thread
        self._worker = worker

    def _populate_review(self, result: NormalizationResult) -> None:
        self.review_table.setRowCount(len(result.candidates))
        self._candidate_inputs.clear()
        for row, candidate in enumerate(result.candidates):
            self.review_table.setItem(row, 0, QTableWidgetItem(candidate.action_type))
            label = QLineEdit(self._default_label(candidate))
            value = QLineEdit()
            if isinstance(candidate.value, CandidateLiteralValue):
                value.setText(candidate.value.display_value or "")
            elif isinstance(candidate.value, CandidateSecretValue):
                value.setPlaceholderText("Windows 자격 증명 참조")
                value.setEchoMode(QLineEdit.EchoMode.Password)
            else:
                value.setEnabled(False)
            self.review_table.setCellWidget(row, 1, label)
            self.review_table.setCellWidget(row, 2, value)
            self.review_table.setItem(row, 3, QTableWidgetItem("미리보기 없음"))
            self._candidate_inputs.append((candidate, label, value))
        self.review_table.setVisible(True)
        self.import_button.setVisible(bool(result.candidates))

    @Slot()
    def _import_review(self) -> None:
        candidates: list[StepCandidate] = []
        labels: list[str] = []
        confirmed: dict[UUID, ValueSpec | None] = {}
        credentials: dict[UUID, str] = {}
        for candidate, label_input, value_input in self._candidate_inputs:
            candidates.append(candidate)
            labels.append(label_input.text())
            if isinstance(candidate.value, CandidateLiteralValue):
                confirmed[candidate.candidate_id] = LiteralValue(value=value_input.text())
            elif isinstance(candidate.value, CandidateSecretValue):
                credentials[candidate.candidate_id] = value_input.text()
        try:
            command = ImportCandidates.from_review(
                candidates,
                labels,
                confirmed_values=confirmed,
                credential_refs=credentials,
            )
        except EditRejected as error:
            self.validation_text.setText(str(error))
            return
        self.validation_text.setText("검토한 동작을 편집기로 전달했습니다.")
        self.candidates_reviewed.emit(command)

    def _reset_controls(self) -> None:
        self.target_combo.setEnabled(True)
        self.refresh_button.setEnabled(True)
        self.start_button.setEnabled(True)
        self.pause_button.setEnabled(False)
        self.pause_button.setText("일시정지")
        self.stop_button.setEnabled(False)

    def _update_banner(self) -> None:
        target = self.target_combo.currentData()
        target_text = target.window_title if isinstance(target, RecordingTarget) else "선택한 창"
        elapsed = max(0, self._elapsed.elapsed()) // 1_000 if self._elapsed.isValid() else 0
        state = self.banner.property("state") or "recording"
        state_text = "일시정지" if state == "paused" else "기록 중"
        if state == "stopping":
            state_text = "종료 중"
        self.banner.setText(
            f"● {state_text} · {target_text} · {elapsed // 60:02d}:{elapsed % 60:02d}\n"
            "Ctrl+Shift+F11 일시정지/계속 · Ctrl+Shift+F12 종료"
        )

    @staticmethod
    def _default_label(candidate: StepCandidate) -> str:
        labels = {
            "windows.click": "클릭",
            "windows.double_click": "더블클릭",
            "windows.drag": "드래그",
            "windows.scroll": "스크롤",
            "windows.set_text": "텍스트 입력",
            "windows.press_key": "키 입력",
            "windows.hotkey": "단축키 입력",
        }
        return labels.get(candidate.action_type, candidate.action_type)

    def closeEvent(self, event: QCloseEvent) -> None:
        thread = self._thread
        if thread is not None and thread.isRunning():
            thread.quit()
            thread.wait(1_000)
        self._thread = None
        self._worker = None
        super().closeEvent(event)


__all__ = ["RecorderPage"]
