from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QComboBox,
    QDateEdit,
    QLineEdit,
    QWidget,
)
from pytestqt.qtbot import QtBot

from tests.unit.application.test_validation import action_step, workflow
from universal_rpa.application.execution import StepTestEligibility, StepTestRequest
from universal_rpa.application.projects import ProjectSession
from universal_rpa.application.resume import ResumeCompatibility
from universal_rpa.domain.errors import ErrorCode, ValidationIssue, ValidationReport
from universal_rpa.domain.results import ActionResult, LoopCursor, RunReport
from universal_rpa.domain.values import (
    CredentialSource,
    InlineChoiceSource,
    RowBindingValue,
    RunInputSource,
    SecretRefValue,
    VariableDefinition,
)
from universal_rpa.domain.workflow import InlineDataSource, LoopStep, Workflow
from universal_rpa.ui.runner_page import RunnerPage

WORKFLOW_ID = UUID("00000000-0000-0000-0000-000000000820")
STEP_ID = UUID("00000000-0000-0000-0000-000000000821")
LOOP_ID = UUID("00000000-0000-0000-0000-000000000941")
RUN_ID = UUID("00000000-0000-0000-0000-000000000942")
NOW = datetime(2026, 8, 3, tzinfo=UTC)


class FakeSecretStore:
    def __init__(self, known: frozenset[str] = frozenset()) -> None:
        self._known = known
        self.exists_calls: list[str] = []

    def exists(self, reference: str) -> bool:
        self.exists_calls.append(reference)
        return reference in self._known

    def read(self, reference: str) -> Any:  # pragma: no cover - the runner must never read
        raise AssertionError("the runner must never read a secret")


class FakeExecutionService:
    def __init__(
        self,
        *,
        preflight_report: ValidationReport | None = None,
        eligibility: StepTestEligibility | None = None,
        resumable: tuple[ResumeCompatibility, ...] = (),
    ) -> None:
        self._preflight_report = preflight_report or ValidationReport()
        self._eligibility = eligibility or StepTestEligibility(True)
        self._resumable = resumable
        self.preflight_calls: list[Any] = []
        self.test_step_calls: list[StepTestRequest] = []
        self.eligibility_calls: list[StepTestRequest] = []
        self.discover_calls: list[Any] = []

    def preflight(self, request: Any) -> ValidationReport:
        self.preflight_calls.append(request)
        return self._preflight_report

    def step_test_eligibility(self, request: StepTestRequest) -> StepTestEligibility:
        self.eligibility_calls.append(request)
        return self._eligibility

    def test_step(self, request: StepTestRequest, control: Any) -> ActionResult:
        del control
        self.test_step_calls.append(request)
        return _result("success")

    def discover_resumable(self, request: Any) -> tuple[ResumeCompatibility, ...]:
        self.discover_calls.append(request)
        return self._resumable


def _workflow(
    *,
    variables: tuple[VariableDefinition, ...] = (),
    secret_step: bool = False,
    loop: bool = False,
) -> Workflow:
    if loop:
        source = InlineDataSource(
            data_source_id="rows",
            label="행",
            headers=("factory",),
            rows=(("F-001",), ("F-002",), ("F-003",), ("F-004",)),
        )
        step = LoopStep(
            step_id=LOOP_ID,
            label="행 반복",
            data_source_id="rows",
            steps=(
                action_step(value=RowBindingValue(template="{{ row.factory }}")).model_copy(
                    update={"step_id": STEP_ID}
                ),
            ),
        )
        return workflow(step, variables=variables, data_sources=(source,))  # type: ignore[arg-type]
    step = action_step(
        value=SecretRefValue(credential_ref="erp/password") if secret_step else None
    ).model_copy(update={"step_id": STEP_ID})
    return workflow(step, variables=variables)


def _session(tmp_path: Path, run_workflow: Workflow) -> ProjectSession:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    return ProjectSession(project.resolve(), run_workflow, run_workflow.revision, False)


def _result(status: str, cursor: tuple[LoopCursor, ...] = ()) -> ActionResult:
    return ActionResult(
        run_id=RUN_ID,
        step_id=STEP_ID,
        iteration_path=tuple(item.row_index for item in cursor),
        iteration_cursor=cursor,
        status=status,  # type: ignore[arg-type]
        started_at=NOW,
        error_code=ErrorCode.ACTION_FAILED if status == "failed" else None,
        safe_message="단계를 완료하지 못했습니다." if status == "failed" else "",
    )


def _failed_report(cursor: tuple[LoopCursor, ...] = ()) -> RunReport:
    return RunReport(
        run_id=RUN_ID,
        workflow_id=WORKFLOW_ID,
        workflow_revision=1,
        status="failed",
        started_at=NOW,
        finished_at=NOW,
        error_code=ErrorCode.ACTION_FAILED,
        safe_message="단계를 완료하지 못했습니다.",
        results=(_result("failed", cursor),),
        completed_iterations=0,
    )


def _invalid_preflight() -> ValidationReport:
    return ValidationReport(
        issues=(
            ValidationIssue(
                code=ErrorCode.ENVIRONMENT_MISMATCH,
                path="target_apps[0]",
                safe_message="대상 프로그램을 찾을 수 없습니다.",
            ),
        )
    )


def _resume_ready() -> ResumeCompatibility:
    return ResumeCompatibility(
        workflow_id=WORKFLOW_ID,
        run_id=RUN_ID,
        resumable=True,
        completed_cursor=(LoopCursor(loop_step_id=LOOP_ID, row_index=1),),
        updated_at=NOW,
    )


def _resume_unsafe() -> ResumeCompatibility:
    return ResumeCompatibility(
        workflow_id=WORKFLOW_ID,
        run_id=RUN_ID,
        resumable=False,
        error_code=ErrorCode.RESUME_UNSAFE,
        safe_message="중단된 비멱등 작업이 있어 수동 확인이 필요합니다.",
    )


def _resume_mismatch(field: str) -> ResumeCompatibility:
    return ResumeCompatibility(
        workflow_id=WORKFLOW_ID,
        run_id=RUN_ID,
        resumable=False,
        error_code=ErrorCode.RESUME_MISMATCH,
        safe_message="업무 정의·실행 입력·데이터 또는 실행 환경이 바뀌어 재개할 수 없습니다.",
        mismatch_fields=(field,),
    )


def all_widget_and_model_text(page: QWidget) -> str:
    parts: list[str] = []
    for child in page.findChildren(QWidget):
        for reader in ("text", "currentText", "placeholderText", "toolTip"):
            value = getattr(child, reader, None)
            if callable(value):
                try:
                    parts.append(str(value()))
                except TypeError:  # pragma: no cover - defensive
                    continue
    return "\n".join(parts)


@pytest.fixture
def page(qtbot: QtBot, tmp_path: Path) -> RunnerPage:
    widget = RunnerPage(FakeExecutionService(), FakeSecretStore())
    qtbot.addWidget(widget)
    widget.show()
    widget.set_session(_session(tmp_path, _workflow()))
    return widget


@pytest.fixture
def output_root(tmp_path: Path) -> Path:
    root = tmp_path / "selected-output"
    root.mkdir()
    return root


def test_preflight_error_disables_run_without_starting_worker(
    page: RunnerPage, output_root: Path
) -> None:
    page.set_output_root(output_root)
    page.set_preflight_report(_invalid_preflight())

    assert not page.run_button.isEnabled()
    assert page.worker_start_count == 0
    assert "대상 프로그램을 찾을 수 없습니다." in page.validation_text.text()


def test_runner_displays_only_workflow_configured_credential_reference(
    qtbot: QtBot, tmp_path: Path
) -> None:
    secrets = FakeSecretStore(frozenset({"erp/password"}))
    widget = RunnerPage(FakeExecutionService(), secrets)
    qtbot.addWidget(widget)
    widget.show()

    widget.set_session(_session(tmp_path, _workflow(secret_step=True)))

    assert widget.credential_reference_label.text() == "erp/password"
    assert widget.findChildren(QComboBox, "credentialReferenceChooser") == []
    assert widget.secret_store.exists_calls == ["erp/password"]
    assert "actual-password" not in all_widget_and_model_text(widget)


def test_missing_configured_credential_disables_run_and_links_manager(
    qtbot: QtBot, tmp_path: Path, output_root: Path
) -> None:
    widget = RunnerPage(FakeExecutionService(), FakeSecretStore(frozenset()))
    qtbot.addWidget(widget)
    widget.show()

    widget.set_session(_session(tmp_path, _workflow(secret_step=True)))
    widget.set_output_root(output_root)
    widget.set_preflight_report(ValidationReport())

    assert not widget.run_button.isEnabled()
    assert widget.open_credential_manager_button.isVisible()


def test_output_directory_selection_is_required(page: RunnerPage, output_root: Path) -> None:
    page.set_preflight_report(ValidationReport())
    assert not page.run_button.isEnabled()

    assert page.set_output_root(output_root) is True

    assert page.run_button.isEnabled()
    assert page.build_request().inputs.output_directory == output_root.resolve()
    assert str(output_root) in page.output_root_label.text()


def test_missing_output_directory_is_rejected(page: RunnerPage, tmp_path: Path) -> None:
    page.set_preflight_report(ValidationReport())

    assert page.set_output_root(tmp_path / "does-not-exist") is False
    assert not page.run_button.isEnabled()


def test_typed_run_form_collects_every_supported_variable_type(
    qtbot: QtBot, tmp_path: Path, output_root: Path
) -> None:
    variables = (
        VariableDefinition(
            variable_id="factory", label="공장", value_type="text", source=RunInputSource()
        ),
        VariableDefinition(
            variable_id="period", label="기간", value_type="date", source=RunInputSource()
        ),
        VariableDefinition(
            variable_id="count", label="건수", value_type="integer", source=RunInputSource()
        ),
        VariableDefinition(
            variable_id="rate", label="비율", value_type="decimal", source=RunInputSource()
        ),
        VariableDefinition(
            variable_id="folder", label="폴더", value_type="path", source=RunInputSource()
        ),
        VariableDefinition(
            variable_id="mode",
            label="구분",
            value_type="choice",
            source=InlineChoiceSource(options=("월간", "연간")),
        ),
    )
    widget = RunnerPage(FakeExecutionService(), FakeSecretStore())
    qtbot.addWidget(widget)
    widget.show()
    widget.set_session(_session(tmp_path, _workflow(variables=variables)))
    widget.set_output_root(output_root)
    widget.set_preflight_report(ValidationReport())

    editors = widget.run_form_editors()
    assert isinstance(editors["factory"], QLineEdit)
    assert isinstance(editors["period"], QDateEdit)
    assert isinstance(editors["count"], QAbstractSpinBox)
    assert isinstance(editors["rate"], QAbstractSpinBox)
    assert isinstance(editors["folder"], QLineEdit)
    assert isinstance(editors["mode"], QComboBox)

    editors["factory"].setText("F-001")
    editors["period"].setDate(date(2026, 7, 1))
    editors["folder"].setText("exports")
    values = widget.build_request().inputs.variable_values

    assert values["factory"] == "F-001"
    assert values["period"] == "2026-07-01"
    assert values["folder"] == "exports"
    assert values["mode"] == "월간"


def test_credential_variables_never_appear_in_the_run_form(qtbot: QtBot, tmp_path: Path) -> None:
    variables = (
        VariableDefinition(
            variable_id="erp_password",
            label="ERP 비밀번호",
            value_type="secret",
            source=CredentialSource(credential_ref="erp/password"),
        ),
    )
    widget = RunnerPage(FakeExecutionService(), FakeSecretStore(frozenset({"erp/password"})))
    qtbot.addWidget(widget)
    widget.show()

    widget.set_session(_session(tmp_path, _workflow(variables=variables)))

    assert "erp_password" not in widget.run_form_editors()
    assert widget.credential_reference_label.text() == "erp/password"


@pytest.mark.parametrize(
    "reason", ["requires_prior_action_output", "row_cursor_required", "unknown_step"]
)
def test_failure_step_retest_is_disabled_when_context_cannot_be_rebuilt(
    qtbot: QtBot, tmp_path: Path, output_root: Path, reason: str
) -> None:
    service = FakeExecutionService(
        eligibility=StepTestEligibility(False, reason, "이 단계는 테스트할 수 없습니다.")
    )
    widget = RunnerPage(service, FakeSecretStore())
    qtbot.addWidget(widget)
    widget.show()
    widget.set_session(_session(tmp_path, _workflow()))
    widget.set_output_root(output_root)

    widget.set_report(_failed_report())

    assert not widget.retest_button.isEnabled()
    assert "이 단계는 테스트할 수 없습니다." in widget.retest_reason.text()


def test_retest_is_disabled_without_a_failed_step(page: RunnerPage) -> None:
    assert not page.retest_button.isEnabled()


def test_row_bound_retest_sends_exact_cursor_and_rebuilds_snapshot(
    qtbot: QtBot, tmp_path: Path, output_root: Path
) -> None:
    service = FakeExecutionService()
    widget = RunnerPage(service, FakeSecretStore())
    qtbot.addWidget(widget)
    widget.show()
    widget.set_session(_session(tmp_path, _workflow(loop=True)))
    widget.set_output_root(output_root)
    cursor = (LoopCursor(loop_step_id=LOOP_ID, row_index=3),)

    widget.set_report(_failed_report(cursor))
    assert widget.retest_button.isEnabled()

    with qtbot.waitSignal(widget.step_test_finished, timeout=5_000):
        widget.retest_button.click()

    request = service.test_step_calls[0]
    assert isinstance(request, StepTestRequest)
    assert request.step_id == STEP_ID
    assert request.cursor == cursor
    assert request.run_request.inputs.output_directory == output_root.resolve()


def test_unsafe_resume_is_disabled_with_manual_recovery_message(page: RunnerPage) -> None:
    page.set_resume_compatibility(_resume_unsafe())

    assert not page.resume_button.isEnabled()
    assert page.resume_error.property("errorCode") == ErrorCode.RESUME_UNSAFE
    assert "수동" in page.resume_error.text()


@pytest.mark.parametrize(
    "mismatch", ["workflow", "inputs", "data", "adapter", "environment", "output"]
)
def test_resume_disabled_for_every_fingerprint_mismatch(page: RunnerPage, mismatch: str) -> None:
    page.set_resume_compatibility(_resume_mismatch(mismatch))

    assert not page.resume_button.isEnabled()
    assert page.resume_error.property("errorCode") == ErrorCode.RESUME_MISMATCH


def test_corrupt_checkpoint_is_distinct_from_unsafe_and_mismatch(page: RunnerPage) -> None:
    page.set_resume_compatibility(
        ResumeCompatibility(
            workflow_id=WORKFLOW_ID,
            run_id=RUN_ID,
            resumable=False,
            error_code=ErrorCode.CHECKPOINT_INVALID,
            safe_message="재개할 실행 상태를 읽을 수 없습니다.",
        )
    )

    assert not page.resume_button.isEnabled()
    assert page.resume_error.property("errorCode") == ErrorCode.CHECKPOINT_INVALID


def test_compatible_checkpoint_enables_resume_and_builds_a_resume_request(
    page: RunnerPage, output_root: Path
) -> None:
    page.set_output_root(output_root)
    page.set_preflight_report(ValidationReport())

    page.set_resume_compatibility(_resume_ready())

    assert page.resume_button.isEnabled()
    assert page.resume_error.text() == ""
    request = page.build_request(resume=True)
    assert request.resume is not None
    assert request.resume.run_id == RUN_ID


def test_resume_discovery_runs_in_a_worker_and_publishes_the_latest_candidate(
    qtbot: QtBot, tmp_path: Path, output_root: Path
) -> None:
    service = FakeExecutionService(resumable=(_resume_ready(),))
    widget = RunnerPage(service, FakeSecretStore())
    qtbot.addWidget(widget)
    widget.show()
    widget.set_session(_session(tmp_path, _workflow()))
    widget.set_output_root(output_root)
    widget.set_preflight_report(ValidationReport())

    with qtbot.waitSignal(widget.resume_discovered, timeout=5_000):
        widget.discover_resume()

    assert len(service.discover_calls) == 1
    assert widget.resume_button.isEnabled()


def test_control_chords_toggle_pause_and_cancel_only_while_running(
    qtbot: QtBot, page: RunnerPage, output_root: Path
) -> None:
    del qtbot
    page.set_output_root(output_root)
    page.set_preflight_report(ValidationReport())

    assert not page.pause_button.isEnabled()
    assert not page.cancel_button.isEnabled()

    page.on_control_command("toggle_pause")
    page.on_control_command("cancel")

    assert page.worker_start_count == 0
