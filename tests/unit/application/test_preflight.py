from __future__ import annotations

from pathlib import Path
from typing import cast

from tests.unit.application.test_validation import workflow as workflow_fixture
from universal_rpa.application.preflight import PreflightService
from universal_rpa.domain.errors import ErrorCode, ValidationIssue, ValidationReport
from universal_rpa.domain.execution import RunInputs, RunRequest
from universal_rpa.domain.workflow import Workflow


class _ValidationSpy:
    def __init__(self, static: ValidationReport, environment: ValidationReport) -> None:
        self.static = static
        self.environment = environment
        self.static_calls = 0
        self.environment_calls = 0

    def validate_static(self, workflow: Workflow) -> ValidationReport:
        del workflow
        self.static_calls += 1
        return self.static

    def validate_environment(self, workflow: Workflow, context: object) -> ValidationReport:
        del workflow, context
        self.environment_calls += 1
        return self.environment


def _request(tmp_path: Path) -> RunRequest:
    return RunRequest(
        workflow=workflow_fixture(),
        project_dir=tmp_path,
        inputs=RunInputs(output_directory=tmp_path),
    )


def test_static_failure_stops_before_environment_validation(tmp_path: Path) -> None:
    invalid = ValidationReport(
        issues=(
            ValidationIssue(code=ErrorCode.INVALID_SCHEMA, path="workflow", safe_message="오류"),
        )
    )
    spy = _ValidationSpy(invalid, ValidationReport())

    report = PreflightService(cast(object, spy), lambda _: None).check(_request(tmp_path))

    assert report == invalid
    assert spy.static_calls == 1
    assert spy.environment_calls == 0


def test_valid_static_validation_delegates_environment_once(tmp_path: Path) -> None:
    environment = ValidationReport()
    spy = _ValidationSpy(ValidationReport(), environment)

    report = PreflightService(cast(object, spy), lambda _: None).check(_request(tmp_path))

    assert report == environment
    assert spy.static_calls == 1
    assert spy.environment_calls == 1
