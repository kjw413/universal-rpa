"""The full Studio flow against the harness: record → edit → preflight → run → report.

This is the single test that proves the milestones compose.  It uses the
production recorder, normalizer, editor, validator, execution service, and report
projector; nothing here substitutes a double for a product component.
"""

from __future__ import annotations

import pytest

from samples.test_harness.state import SYNTHETIC_DATE
from tests.integration.windows.conftest import HarnessProcess
from tests.integration.windows.helpers import (
    build_run_request,
    harness_scenario,
    harness_session,
    production_services,
    record_edit_run,
    run_harness_workflow_detailed,
)


@pytest.mark.windows_e2e
def test_complete_keyboard_roundtrip(harness: HarnessProcess) -> None:
    result = record_edit_run("ctrl-a-date-enter", harness)

    assert result.normalized_actions == [
        "windows.hotkey",
        "windows.set_text",
        "windows.press_key",
    ]
    assert harness.await_state(lambda state: state.date_text == SYNTHETIC_DATE).date_text == (
        SYNTHETIC_DATE
    )
    assert result.report.status == "success"


@pytest.mark.windows_e2e
def test_full_flow_preflights_runs_and_projects_one_safe_report(
    harness: HarnessProcess,
) -> None:
    services = production_services(harness)
    execution = services.execution_service
    assert execution is not None
    workflow = harness_scenario("click", harness)
    session = harness_session(harness, workflow)

    validation = services.validation_service.validate_static(session.workflow)
    preflight = execution.preflight(build_run_request(harness, workflow, validation_only=True))
    outcome = run_harness_workflow_detailed("click", harness, services=services)

    assert validation.is_valid
    assert preflight.is_valid
    assert outcome.report.status == "success"
    assert outcome.document is not None
    assert outcome.document.run_id == outcome.report.run_id
    assert outcome.document.action_count == len(outcome.report.results)

    # The projected report is the only artifact that leaves the process, and it
    # must not carry a selector, a typed value, or an absolute customer path.
    encoded = outcome.document.model_dump_json()
    assert "automation_id" not in encoded
    assert str(harness.output_dir) not in encoded


@pytest.mark.windows_e2e
def test_validation_only_run_performs_no_action(harness: HarnessProcess) -> None:
    services = production_services(harness)
    execution = services.execution_service
    assert execution is not None
    workflow = harness_scenario("click", harness)

    from universal_rpa.application.run_control import RunControl

    report = execution.run(build_run_request(harness, workflow, validation_only=True), RunControl())

    assert report.status == "success"
    assert report.results == ()
    assert harness.state.click_count == 0
