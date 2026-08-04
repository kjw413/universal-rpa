"""The Runner page: guarded preflight, execution, step retest, and resume.

Every prerequisite the plan calls out is enforced here rather than inside the
worker, so a run that cannot be made safe simply never starts: the user must have
chosen an output directory, preflight must have passed, and each credential the
*workflow* configured must already exist in the Windows credential store.  The
page can display a credential reference; it can never select, substitute, read,
or reveal a secret.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import QDate, Qt, QThread, Signal, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from universal_rpa.application.execution import (
    ExecutionService,
    RunObserver,
    StepTestEligibility,
    StepTestRequest,
)
from universal_rpa.application.projects import ProjectSession
from universal_rpa.application.reports import ReportProjector, SafeRunReportDocument
from universal_rpa.application.resume import ResumeCompatibility
from universal_rpa.application.run_control import RunControl
from universal_rpa.domain.errors import ErrorCode, ValidationReport
from universal_rpa.domain.execution import ResumeRequest, RunInputs, RunRequest
from universal_rpa.domain.results import ActionResult, RunReport
from universal_rpa.domain.types import DataCell, FrozenMapping
from universal_rpa.domain.values import (
    CredentialSource,
    DataColumnSource,
    InlineChoiceSource,
    RunInputSource,
    SecretRefValue,
    VariableDefinition,
)
from universal_rpa.domain.workflow import (
    ActionStep,
    IfPresentStep,
    LoopStep,
    Step,
    Workflow,
)
from universal_rpa.ports.credentials import SecretStorePort
from universal_rpa.ui.workers import (
    CANCEL,
    TOGGLE_PAUSE,
    ControlHotkeyListener,
    ExecutionWorker,
    FunctionWorker,
    RunProgress,
    WorkerFailure,
)

#: Editors the typed run form builds, keyed by variable value type.
RunFormEditor = QLineEdit | QDateEdit | QSpinBox | QDoubleSpinBox | QComboBox


def _is_link_like(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if is_junction is not None and is_junction():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & 0x400)


def _credential_references(workflow: Workflow) -> tuple[str, ...]:
    """Collect every credential reference the *workflow* itself configured."""

    found: list[str] = []

    def visit(steps: Sequence[Step]) -> None:
        for step in steps:
            if isinstance(step, ActionStep):
                if isinstance(step.value, SecretRefValue):
                    found.append(step.value.credential_ref)
            elif isinstance(step, (LoopStep, IfPresentStep)):
                visit(step.steps)

    for variable in workflow.variables:
        if isinstance(variable.source, CredentialSource):
            found.append(variable.source.credential_ref)
    visit(workflow.steps)
    ordered: list[str] = []
    for reference in found:
        if reference not in ordered:
            ordered.append(reference)
    return tuple(ordered)


class RunnerPage(QWidget):
    run_finished = Signal(object)
    preflight_finished = Signal(object)
    step_test_finished = Signal(object)
    resume_discovered = Signal(object)
    open_credential_manager_requested = Signal(str)
    _start_requested = Signal(object)

    def __init__(
        self,
        execution_service: ExecutionService | None,
        secret_store: SecretStorePort,
        *,
        artifact_store: RunObserver | None = None,
        report_projector: ReportProjector | None = None,
        control_listener: ControlHotkeyListener | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.execution_service = execution_service
        self.secret_store = secret_store
        self._artifact_store = artifact_store
        self._projector = report_projector or ReportProjector()
        self.session: ProjectSession | None = None
        self.worker_start_count = 0

        self._output_root: Path | None = None
        self._preflight: ValidationReport | None = None
        self._credential_refs: tuple[str, ...] = ()
        self._missing_credentials: tuple[str, ...] = ()
        self._resume: ResumeCompatibility | None = None
        self._retest: tuple[StepTestRequest, StepTestEligibility] | None = None
        self._editors: dict[str, RunFormEditor] = {}
        self._running = False
        self._worker: ExecutionWorker | None = None
        self._thread: QThread | None = None
        # A worker moved onto a QThread has no parent, so the page must hold the
        # only strong reference until the thread finishes; otherwise Python
        # collects the wrapper and the queued run never happens.
        self._helpers: list[tuple[QThread, FunctionWorker]] = []
        self._control_listener = control_listener or ControlHotkeyListener(parent=self)
        self._control_listener.command.connect(self.on_control_command)

        title = QLabel("업무 실행")
        title.setObjectName("page-title")

        self.output_root_button = QPushButton("출력 폴더 선택")
        self.output_root_button.setObjectName("outputRootButton")
        self.output_root_label = QLabel("출력 폴더를 선택하세요.")
        self.output_root_label.setObjectName("outputRootLabel")
        self.output_root_label.setWordWrap(True)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_root_button)
        output_row.addWidget(self.output_root_label, 1)

        self.credential_reference_label = QLabel("")
        self.credential_reference_label.setObjectName("credentialReferenceLabel")
        self.credential_reference_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.open_credential_manager_button = QPushButton("자격 증명 관리자 열기")
        self.open_credential_manager_button.setObjectName("openCredentialManagerButton")
        self.open_credential_manager_button.setVisible(False)
        credential_row = QHBoxLayout()
        credential_row.addWidget(QLabel("자격 증명 참조"))
        credential_row.addWidget(self.credential_reference_label, 1)
        credential_row.addWidget(self.open_credential_manager_button)

        self.run_form = QGroupBox("실행 입력")
        self.run_form_layout = QFormLayout(self.run_form)

        self.preflight_button = QPushButton("사전 검증")
        self.run_button = QPushButton("실행")
        self.pause_button = QPushButton("일시정지")
        self.cancel_button = QPushButton("중지")
        self.retest_button = QPushButton("실패 단계 재시도")
        self.resume_button = QPushButton("이어서 실행")
        for button in (
            self.run_button,
            self.pause_button,
            self.cancel_button,
            self.retest_button,
            self.resume_button,
        ):
            button.setEnabled(False)
        controls = QHBoxLayout()
        for button in (
            self.preflight_button,
            self.run_button,
            self.pause_button,
            self.cancel_button,
        ):
            controls.addWidget(button)
        controls.addStretch(1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(False)
        self.progress_label = QLabel("")
        self.progress_label.setObjectName("runProgressLabel")
        self.validation_text = QLabel("")
        self.validation_text.setObjectName("validation-text")
        self.validation_text.setWordWrap(True)
        self.retest_reason = QLabel("")
        self.retest_reason.setObjectName("retestReason")
        self.retest_reason.setWordWrap(True)
        self.resume_error = QLabel("")
        self.resume_error.setObjectName("resumeError")
        self.resume_error.setWordWrap(True)
        self.hotkey_hint = QLabel("실행 중 Ctrl+Shift+F11 일시정지/계속 · Ctrl+Shift+F12 중지")

        recovery = QHBoxLayout()
        recovery.addWidget(self.retest_button)
        recovery.addWidget(self.resume_button)
        recovery.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addLayout(output_row)
        layout.addLayout(credential_row)
        layout.addWidget(self.run_form)
        layout.addLayout(controls)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.progress_label)
        layout.addWidget(self.validation_text)
        layout.addLayout(recovery)
        layout.addWidget(self.retest_reason)
        layout.addWidget(self.resume_error)
        layout.addWidget(self.hotkey_hint)
        layout.addStretch(1)

        self.output_root_button.clicked.connect(self._choose_output_root)
        self.open_credential_manager_button.clicked.connect(self._open_credential_manager)
        self.preflight_button.clicked.connect(self.preflight)
        self.run_button.clicked.connect(self.start)
        self.pause_button.clicked.connect(self.toggle_pause)
        self.cancel_button.clicked.connect(self.cancel)
        self.retest_button.clicked.connect(self.retest_failed_step)
        self.resume_button.clicked.connect(self.resume_run)

    # -- configuration ---------------------------------------------------
    def set_session(self, session: ProjectSession) -> None:
        self.session = session
        self._preflight = None
        self._resume = None
        self._retest = None
        self.resume_error.setText("")
        self.resume_error.setProperty("errorCode", None)
        self.retest_reason.setText("")
        self._build_run_form(session.workflow)
        self._refresh_credentials(session.workflow)
        self._update_enabled()

    def set_output_root(self, root: Path) -> bool:
        """Normalize and remember the one directory every output must stay under."""

        candidate = Path(root)
        if not candidate.is_dir() or _is_link_like(candidate):
            self.output_root_label.setText("사용할 수 있는 출력 폴더가 아닙니다.")
            self._output_root = None
            self._update_enabled()
            return False
        resolved = candidate.resolve()
        if _is_link_like(resolved):
            self.output_root_label.setText("연결된 폴더는 출력 폴더로 사용할 수 없습니다.")
            self._output_root = None
            self._update_enabled()
            return False
        probe = resolved / f".universal-rpa-write-probe-{UUID(int=0).hex}"
        try:
            probe.write_bytes(b"")
            probe.unlink()
        except OSError:
            self.output_root_label.setText("출력 폴더에 파일을 만들 수 없습니다.")
            self._output_root = None
            self._update_enabled()
            return False
        self._output_root = resolved
        self.output_root_label.setText(str(resolved))
        self._update_enabled()
        return True

    @property
    def output_root(self) -> Path | None:
        return self._output_root

    def set_preflight_report(self, report: ValidationReport) -> None:
        self._preflight = report
        if report.is_valid:
            self.validation_text.setText("사전 검증을 통과했습니다.")
        else:
            self.validation_text.setText("\n".join(issue.safe_message for issue in report.errors))
        self._update_enabled()

    def set_resume_compatibility(self, compatibility: ResumeCompatibility | None) -> None:
        self._resume = compatibility
        if compatibility is None:
            self.resume_error.setText("")
            self.resume_error.setProperty("errorCode", None)
        elif compatibility.resumable:
            self.resume_error.setText("")
            self.resume_error.setProperty("errorCode", None)
        else:
            message = compatibility.safe_message
            if compatibility.error_code is ErrorCode.RESUME_UNSAFE:
                message = f"{message} 자동 재개를 사용할 수 없으니 수동으로 확인하세요."
            self.resume_error.setText(message)
            self.resume_error.setProperty("errorCode", compatibility.error_code)
        self._update_enabled()

    def set_report(self, report: RunReport | None) -> None:
        """Remember the last run so a failed step can be retested in place."""

        self._retest = None
        self.retest_reason.setText("")
        failure = self._last_failure(report)
        if failure is None or self.session is None or self.execution_service is None:
            self._update_enabled()
            return
        if self._output_root is None:
            self.retest_reason.setText("출력 폴더를 선택한 뒤 다시 시도할 수 있습니다.")
            self._update_enabled()
            return
        request = StepTestRequest(
            run_request=self.build_request(),
            step_id=failure.step_id,
            cursor=failure.iteration_cursor,
        )
        eligibility = self.execution_service.step_test_eligibility(request)
        if not eligibility.enabled:
            self.retest_reason.setText(eligibility.safe_message)
        self._retest = (request, eligibility)
        self._update_enabled()

    def run_form_editors(self) -> dict[str, RunFormEditor]:
        return dict(self._editors)

    # -- request construction --------------------------------------------
    def build_request(self, *, resume: bool = False, validation_only: bool = False) -> RunRequest:
        session = self.session
        if session is None:
            raise RuntimeError("실행할 업무가 선택되지 않았습니다.")
        if self._output_root is None:
            raise RuntimeError("출력 폴더가 선택되지 않았습니다.")
        resume_request: ResumeRequest | None = None
        if resume:
            compatibility = self._resume
            if compatibility is None or not compatibility.resumable:
                raise RuntimeError("이어서 실행할 수 있는 실행 상태가 없습니다.")
            resume_request = ResumeRequest(run_id=compatibility.run_id)
        return RunRequest(
            workflow=session.workflow,
            project_dir=session.project_dir,
            inputs=RunInputs(
                variable_values=self._form_values(),
                output_directory=self._output_root,
            ),
            resume=resume_request,
            validation_only=validation_only,
        )

    # -- actions ----------------------------------------------------------
    @Slot()
    def preflight(self) -> None:
        service = self.execution_service
        if service is None or not self._can_build_request():
            return
        request = self.build_request(validation_only=True)
        self._run_in_worker(
            lambda _: service.preflight(request),
            self._on_preflight_done,
            "사전 검증을 완료하지 못했습니다.",
        )

    @Slot()
    def discover_resume(self) -> None:
        service = self.execution_service
        if service is None or not self._can_build_request():
            return
        request = self.build_request()
        self._run_in_worker(
            lambda _: service.discover_resumable(request),
            self._on_resume_discovered,
            "이어서 실행할 상태를 확인하지 못했습니다.",
        )

    @Slot()
    def start(self) -> None:
        self._start(resume=False)

    @Slot()
    def resume_run(self) -> None:
        self._start(resume=True)

    @Slot()
    def toggle_pause(self) -> None:
        worker = self._worker
        if worker is None or not self._running:
            return
        worker.toggle_pause()
        self.pause_button.setText("계속" if worker.is_paused else "일시정지")

    @Slot()
    def pause(self) -> None:
        if self._worker is not None and self._running:
            self._worker.pause()
            self.pause_button.setText("계속")

    @Slot()
    def resume(self) -> None:
        if self._worker is not None and self._running:
            self._worker.resume()
            self.pause_button.setText("일시정지")

    @Slot()
    def cancel(self) -> None:
        if self._worker is not None and self._running:
            self._worker.cancel()
            self.progress_label.setText("실행을 중지하는 중입니다…")

    @Slot(str)
    def on_control_command(self, command: str) -> None:
        """Handle exactly the two run-control chords, and only while running."""

        if not self._running:
            return
        if command == TOGGLE_PAUSE:
            self.toggle_pause()
        elif command == CANCEL:
            self.cancel()

    @Slot()
    def retest_failed_step(self) -> None:
        service = self.execution_service
        retest = self._retest
        if service is None or retest is None or not retest[1].enabled:
            return
        request = retest[0]
        self._run_in_worker(
            lambda _: service.test_step(request, RunControl()),
            self._on_step_test_done,
            "단계를 다시 시도하지 못했습니다.",
        )

    # -- internals --------------------------------------------------------
    def _start(self, *, resume: bool) -> None:
        service = self.execution_service
        if service is None or self._running:
            return
        if resume:
            if not self.resume_button.isEnabled():
                return
        elif not self.run_button.isEnabled():
            return
        request = self.build_request(resume=resume)
        observers = (self._artifact_store,) if self._artifact_store is not None else ()
        worker = ExecutionWorker(
            service,
            projector=self._projector,
            observers=observers,
            output_root=self._output_root,
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        self._start_requested.connect(worker.start)
        worker.progress.connect(self._on_progress)
        worker.completed.connect(self._on_completed)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.start()
        self._worker = worker
        self._thread = thread
        self._running = True
        self.worker_start_count += 1
        self.progress_bar.setVisible(True)
        self.progress_label.setText("실행을 시작했습니다.")
        self._control_listener.start()
        self._update_enabled()
        self._start_requested.emit(request)

    def _run_in_worker(
        self,
        operation: Callable[[threading.Event], object],
        on_completed: Callable[[object], None],
        failure_message: str,
    ) -> None:
        worker = FunctionWorker(operation)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(on_completed)
        worker.failed.connect(lambda _: self._on_helper_failed(failure_message))
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(lambda: self._forget_helper(thread))
        self._helpers.append((thread, worker))
        thread.start()

    def _forget_helper(self, thread: QThread) -> None:
        self._helpers = [entry for entry in self._helpers if entry[0] is not thread]

    @Slot(object)
    def _on_preflight_done(self, report: object) -> None:
        if isinstance(report, ValidationReport):
            self.set_preflight_report(report)
        self.preflight_finished.emit(report)

    @Slot(object)
    def _on_resume_discovered(self, found: object) -> None:
        latest: ResumeCompatibility | None = None
        if isinstance(found, tuple) and found:
            candidate = found[0]
            if isinstance(candidate, ResumeCompatibility):
                latest = candidate
        self.set_resume_compatibility(latest)
        self.resume_discovered.emit(latest)

    @Slot(object)
    def _on_step_test_done(self, result: object) -> None:
        if isinstance(result, ActionResult):
            self.validation_text.setText(
                "단계 재시도 성공"
                if result.status == "success"
                else f"단계 재시도 실패: {result.safe_message}"
            )
        self.step_test_finished.emit(result)

    def _on_helper_failed(self, message: str) -> None:
        self.validation_text.setText(message)

    @Slot(object)
    def _on_progress(self, progress: object) -> None:
        if not isinstance(progress, RunProgress):
            return
        state = "일시정지" if progress.paused else "실행 중"
        self.progress_label.setText(
            f"{state} · 완료 {progress.completed_actions} · 실패 {progress.failed_actions}"
            f" · 최근 단계 {progress.last_step_label}".rstrip()
        )

    @Slot(object)
    def _on_completed(self, document: object) -> None:
        self._finish_run()
        if isinstance(document, SafeRunReportDocument):
            self.progress_label.setText(f"실행이 끝났습니다: {document.status}")
            self.run_finished.emit(document)

    @Slot(object)
    def _on_failed(self, failure: object) -> None:
        self._finish_run()
        message = (
            failure.safe_message
            if isinstance(failure, WorkerFailure)
            else "실행을 완료하지 못했습니다."
        )
        self.validation_text.setText(message)

    def _finish_run(self) -> None:
        self._running = False
        self._worker = None
        self.progress_bar.setVisible(False)
        self.pause_button.setText("일시정지")
        self._control_listener.stop()
        self._update_enabled()

    def _build_run_form(self, workflow: Workflow) -> None:
        while self.run_form_layout.count():
            item = self.run_form_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._editors.clear()
        for variable in workflow.variables:
            editor = self._editor_for(variable)
            if editor is None:
                continue
            editor.setObjectName(f"runInput_{variable.variable_id}")
            self.run_form_layout.addRow(variable.label, editor)
            self._editors[variable.variable_id] = editor
        self.run_form.setVisible(bool(self._editors))

    @staticmethod
    def _editor_for(variable: VariableDefinition) -> RunFormEditor | None:
        source = variable.source
        if isinstance(source, InlineChoiceSource):
            combo = QComboBox()
            combo.addItems(list(source.options))
            return combo
        if isinstance(source, DataColumnSource):
            # Row values are only known once the data source is read at run time,
            # so the user types the approved key instead of picking a stale list.
            return QLineEdit()
        if not isinstance(source, RunInputSource):
            return None
        if variable.value_type == "date":
            editor = QDateEdit()
            editor.setDisplayFormat("yyyy-MM-dd")
            editor.setCalendarPopup(True)
            editor.setDate(QDate.currentDate())
            return editor
        if variable.value_type == "integer":
            spin = QSpinBox()
            spin.setRange(-1_000_000_000, 1_000_000_000)
            return spin
        if variable.value_type == "decimal":
            decimal_spin = QDoubleSpinBox()
            decimal_spin.setDecimals(4)
            decimal_spin.setRange(-1_000_000_000.0, 1_000_000_000.0)
            return decimal_spin
        return QLineEdit()

    def _form_values(self) -> FrozenMapping[str, DataCell]:
        values: dict[str, DataCell] = {}
        for variable_id, editor in self._editors.items():
            if isinstance(editor, QComboBox):
                values[variable_id] = editor.currentText()
            elif isinstance(editor, QDateEdit):
                values[variable_id] = editor.date().toString("yyyy-MM-dd")
            elif isinstance(editor, QSpinBox):
                values[variable_id] = editor.value()
            elif isinstance(editor, QDoubleSpinBox):
                values[variable_id] = float(editor.value())
            else:
                values[variable_id] = editor.text()
        return FrozenMapping(tuple(values.items()))

    def _refresh_credentials(self, workflow: Workflow) -> None:
        self._credential_refs = _credential_references(workflow)
        missing: list[str] = []
        for reference in self._credential_refs:
            try:
                present = self.secret_store.exists(reference)
            except Exception:
                present = False
            if not present:
                missing.append(reference)
        self._missing_credentials = tuple(missing)
        self.credential_reference_label.setText(", ".join(self._credential_refs))
        self.open_credential_manager_button.setVisible(bool(missing))

    def _can_build_request(self) -> bool:
        return self.session is not None and self._output_root is not None

    def _update_enabled(self) -> None:
        ready = (
            not self._running
            and self._can_build_request()
            and self._preflight is not None
            and self._preflight.is_valid
            and not self._missing_credentials
        )
        self.run_button.setEnabled(ready)
        self.preflight_button.setEnabled(not self._running and self._can_build_request())
        self.pause_button.setEnabled(self._running)
        self.cancel_button.setEnabled(self._running)
        self.resume_button.setEnabled(ready and self._resume is not None and self._resume.resumable)
        retest = self._retest
        self.retest_button.setEnabled(
            not self._running and retest is not None and retest[1].enabled
        )

    @staticmethod
    def _last_failure(report: RunReport | None) -> ActionResult | None:
        if report is None:
            return None
        for result in reversed(report.results):
            if result.status in {"failed", "cancelled"}:
                return result
        return None

    @Slot()
    def _choose_output_root(self) -> None:  # pragma: no cover - requires a native dialog
        chosen = QFileDialog.getExistingDirectory(self, "출력 폴더 선택")
        if chosen:
            self.set_output_root(Path(chosen))

    @Slot()
    def _open_credential_manager(self) -> None:
        reference = self._missing_credentials[0] if self._missing_credentials else ""
        self.open_credential_manager_requested.emit(reference)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._control_listener.stop()
        if self._worker is not None:
            self._worker.cancel()
        for thread in (self._thread, *(entry[0] for entry in self._helpers)):
            if thread is not None and thread.isRunning():
                thread.quit()
                thread.wait(2_000)
        self._thread = None
        self._helpers.clear()
        super().closeEvent(event)


__all__ = ["RunFormEditor", "RunnerPage"]
