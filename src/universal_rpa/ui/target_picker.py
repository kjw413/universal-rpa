from __future__ import annotations

import threading

from PySide6.QtCore import QThread
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
)

from universal_rpa.domain.targets import NormalizedRect, TargetSpec, WindowsTarget
from universal_rpa.ports.automation import (
    CancellationToken,
    TargetCapturePort,
    TargetCaptureRequest,
    TargetCaptureResult,
)
from universal_rpa.ui.workers import FunctionWorker, WorkerFailure


class SensitiveRegionEditor(QListWidget):
    def __init__(self) -> None:
        super().__init__()
        self._mandatory: tuple[NormalizedRect, ...] = ()
        self._user: list[NormalizedRect] = []

    @property
    def mandatory_regions(self) -> tuple[NormalizedRect, ...]:
        return self._mandatory

    @property
    def user_regions(self) -> tuple[NormalizedRect, ...]:
        return tuple(self._user)

    def set_regions(
        self,
        mandatory: tuple[NormalizedRect, ...],
        user: tuple[NormalizedRect, ...],
    ) -> None:
        self._mandatory = tuple(mandatory)
        self._user = list(user)
        self._refresh()

    def add_region(self, region: NormalizedRect) -> None:
        if region not in self._user and region not in self._mandatory:
            self._user.append(region)
            self._refresh()

    def remove_region(self, region: NormalizedRect) -> bool:
        if region in self._mandatory or region not in self._user:
            return False
        self._user.remove(region)
        self._refresh()
        return True

    def _refresh(self) -> None:
        self.clear()
        for region in self._mandatory:
            self.addItem(f"필수 잠금 · {region.x:.3f}, {region.y:.3f}")
        for region in self._user:
            self.addItem(f"사용자 마스킹 · {region.x:.3f}, {region.y:.3f}")


class TargetPicker(QDialog):
    def __init__(self, capture_port: TargetCapturePort | None = None) -> None:
        super().__init__()
        self.setWindowTitle("대상과 미리보기 다시 캡처")
        self._capture_port = capture_port
        self._capture_result: TargetCaptureResult | None = None
        self._selected_target: TargetSpec | None = None
        self._thread: QThread | None = None
        self._token: CancellationToken | None = None
        self.candidate_combo = QComboBox()
        self.status_label = QLabel("캡처할 화면 위치를 선택하세요.")
        self.region_editor = SensitiveRegionEditor()
        self.capture_button = QPushButton("대상 캡처")
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self._accept_if_complete)
        self.buttons.rejected.connect(self.reject)
        self.candidate_combo.currentIndexChanged.connect(self._candidate_changed)
        layout = QVBoxLayout(self)
        layout.addWidget(self.status_label)
        layout.addWidget(self.candidate_combo)
        layout.addWidget(self.region_editor)
        layout.addWidget(self.capture_button)
        layout.addWidget(self.buttons)

    def start_capture(self, request: TargetCaptureRequest) -> None:
        if self._capture_port is None or self._thread is not None:
            return
        token = CancellationToken()
        worker = FunctionWorker(lambda cancelled: self._capture(request, token, cancelled))
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self.set_capture_result)
        worker.failed.connect(self._capture_failed)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._thread_finished)
        self._thread = thread
        self._token = token
        thread.start()

    def set_capture_result(self, result: object) -> None:
        if not isinstance(result, TargetCaptureResult):
            return
        self._capture_result = result
        self.candidate_combo.clear()
        for index, candidate in enumerate(result.candidates):
            self.candidate_combo.addItem(f"대상 후보 {index + 1}", candidate)
        if result.target is not None:
            self._selected_target = result.target
            self.candidate_combo.setCurrentIndex(result.candidates.index(result.target))
        else:
            self._selected_target = None
            self.candidate_combo.setCurrentIndex(-1)
        self._load_regions()
        if result.issues:
            self.status_label.setText(" · ".join(issue.safe_message for issue in result.issues))
        else:
            self.status_label.setText("대상과 민감 영역을 확인하세요.")

    def captured_result(self) -> TargetCaptureResult | None:
        result = self._capture_result
        target = self._selected_target
        if result is None or target is None:
            return None
        if target.adapter_id == "windows":
            windows = WindowsTarget.model_validate(target.payload)
            windows = windows.model_copy(
                update={"user_sensitive_regions": self.region_editor.user_regions}
            )
            target = TargetSpec.model_validate(
                {"adapter_id": "windows", "payload": windows.model_dump(mode="json")}
            )
        candidates = tuple(
            target if item == self._selected_target else item for item in result.candidates
        )
        return TargetCaptureResult(
            target=target,
            candidates=candidates,
            preview_png=result.preview_png,
            issues=result.issues,
        )

    def reject(self) -> None:
        if self._token is not None:
            self._token.cancel()
        super().reject()

    def _candidate_changed(self, index: int) -> None:
        target = self.candidate_combo.itemData(index) if index >= 0 else None
        self._selected_target = target if isinstance(target, TargetSpec) else None
        self._load_regions()

    def _load_regions(self) -> None:
        target = self._selected_target
        if target is None or target.adapter_id != "windows":
            self.region_editor.set_regions((), ())
            return
        windows = WindowsTarget.model_validate(target.payload)
        self.region_editor.set_regions(
            windows.mandatory_sensitive_regions,
            windows.user_sensitive_regions,
        )

    def _accept_if_complete(self) -> None:
        if self.captured_result() is None:
            self.status_label.setText("대상 후보를 명시적으로 선택하세요.")
            return
        self.accept()

    def _capture(
        self,
        request: TargetCaptureRequest,
        token: CancellationToken,
        cancelled: threading.Event,
    ) -> TargetCaptureResult:
        if cancelled.is_set():
            token.cancel()
        if self._capture_port is None:
            raise RuntimeError
        return self._capture_port.capture_target(request, token)

    def _capture_failed(self, failure: object) -> None:
        self.status_label.setText(
            failure.safe_message
            if isinstance(failure, WorkerFailure)
            else "대상을 캡처하지 못했습니다."
        )

    def _thread_finished(self) -> None:
        self._thread = None
        self._token = None


__all__ = ["SensitiveRegionEditor", "TargetPicker"]
