from __future__ import annotations

from pathlib import Path
from uuid import UUID

from tests.helpers.validation_fakes import ValidationSpyAdapter, fake_target
from tests.unit.application.test_step_test import _service
from tests.unit.application.test_validation import action_step, workflow
from universal_rpa.application.run_control import RunControl
from universal_rpa.domain.errors import ErrorCode, RpaError
from universal_rpa.domain.execution import RunInputs, RunRequest
from universal_rpa.domain.workflow import IfPresentStep, PresenceSpec


def _request(tmp_path: Path, *steps: object) -> RunRequest:
    project = tmp_path / "project"
    output = tmp_path / "output"
    project.mkdir()
    output.mkdir()
    return RunRequest(
        workflow=workflow(*steps),  # type: ignore[arg-type]
        project_dir=project,
        inputs=RunInputs(output_directory=output),
    )


def test_failed_action_is_preserved_in_run_report(tmp_path: Path) -> None:
    adapter = ValidationSpyAdapter()
    adapter.script.append(RpaError(ErrorCode.ACTION_FAILED, "테스트 작업 실패"))

    report = _service(tmp_path, adapter).run(_request(tmp_path, action_step()), RunControl())

    assert report.status == "failed"
    assert report.error_code is ErrorCode.ACTION_FAILED
    assert len(report.results) == 1
    assert report.results[0].error_code is ErrorCode.ACTION_FAILED


def test_absent_if_present_records_skip_without_executing_child(tmp_path: Path) -> None:
    adapter = ValidationSpyAdapter()
    optional = IfPresentStep(
        step_id=UUID("00000000-0000-0000-0000-000000000903"),
        label="선택 그룹",
        condition=PresenceSpec(
            condition_type="fake.element_exists",
            target=fake_target(matches=0),
            timeout_ms=1,
            poll_interval_ms=1,
        ),
        steps=(action_step(),),
    )

    report = _service(tmp_path, adapter).run(_request(tmp_path, optional), RunControl())

    assert report.status == "success"
    assert report.results[0].status == "skipped"
    assert report.results[0].skip_reason == "if_present_absent"
    assert not any(call.operation == "execute" for call in adapter.calls)
