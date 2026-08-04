"""Interactive runner coverage against the deterministic harness.

Every test here sends real native input to a real desktop, so all of them carry
``windows_e2e`` and run only on an unlocked self-hosted session.  The single
unmarked test is the gate itself: it proves the fixture refuses to run when the
operator has not opted in, which is what keeps a hosted runner from silently
"passing" this suite without ever touching a window.
"""

from __future__ import annotations

import pytest

from samples.test_harness.state import SYNTHETIC_KOREAN
from tests.integration.windows.conftest import (
    HarnessOptions,
    HarnessProcess,
    require_interactive_desktop,
)
from tests.integration.windows.helpers import run_harness_workflow
from universal_rpa.domain.errors import ErrorCode


def test_windows_e2e_fixture_requires_interactive_self_hosted_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RPA_INTERACTIVE_DESKTOP", raising=False)

    with pytest.raises(pytest.skip.Exception):
        require_interactive_desktop()


@pytest.mark.windows_e2e
def test_selector_only_click_succeeds_and_is_observed(harness: HarnessProcess) -> None:
    report = run_harness_workflow("click", harness)

    assert report.status == "success"
    assert harness.await_state(lambda state: state.click_count == 1).click_count == 1


@pytest.mark.windows_e2e
@pytest.mark.parametrize(
    "harness_options", [HarnessOptions(duplicate_selector=True)], ids=["duplicate"]
)
def test_duplicate_selector_fails_before_click(harness: HarnessProcess) -> None:
    report = run_harness_workflow("duplicate-selector", harness)

    assert report.status == "failed"
    assert report.results[-1].error_code is ErrorCode.TARGET_AMBIGUOUS
    assert harness.state.click_count == 0


@pytest.mark.windows_e2e
def test_uia_survives_a_window_move(harness: HarnessProcess, move_harness_window: object) -> None:
    move_harness_window(harness, dx=60, dy=40)  # type: ignore[operator]

    assert run_harness_workflow("uia-after-move", harness).status == "success"


@pytest.mark.windows_e2e
def test_coordinate_fallback_refuses_after_a_resize_beyond_tolerance(
    harness: HarnessProcess, resize_harness_window: object
) -> None:
    resize_harness_window(harness, width=1040, height=880)  # type: ignore[operator]

    report = run_harness_workflow("coordinate-fallback", harness)

    assert report.status == "failed"
    assert report.results[-1].error_code is ErrorCode.ENVIRONMENT_MISMATCH


@pytest.mark.windows_e2e
def test_wait_condition_polls_until_a_delayed_element_appears(
    harness: HarnessProcess,
) -> None:
    report = run_harness_workflow("delayed-element", harness)

    assert report.status == "success"
    assert harness.state.delayed_control_visible is True


@pytest.mark.windows_e2e
@pytest.mark.parametrize(
    "harness_options", [HarnessOptions(intentional_timeout=True)], ids=["never-appears"]
)
def test_absent_element_times_out_with_a_typed_error(harness: HarnessProcess) -> None:
    report = run_harness_workflow("intentional-timeout", harness)

    assert report.status == "failed"
    assert report.results[-1].error_code is ErrorCode.CONDITION_TIMEOUT
    assert harness.state.delayed_control_visible is False


@pytest.mark.windows_e2e
def test_owned_modal_is_opened_and_closed_through_uia(harness: HarnessProcess) -> None:
    report = run_harness_workflow("modal", harness)

    observed = harness.await_state(lambda state: state.modal_close_count == 1)
    assert report.status == "success"
    assert observed.modal_open_count == 1
    assert observed.modal_close_count == 1


@pytest.mark.windows_e2e
def test_korean_text_round_trips_and_verifies(harness: HarnessProcess) -> None:
    report = run_harness_workflow("korean-verification", harness)

    assert report.status == "success"
    assert harness.await_state(lambda state: state.korean_text == SYNTHETIC_KOREAN).korean_text == (
        SYNTHETIC_KOREAN
    )


@pytest.mark.windows_e2e
def test_drag_scroll_and_hotkey_each_have_an_observable_effect(
    harness: HarnessProcess,
) -> None:
    report = run_harness_workflow("drag-scroll-hotkey", harness)

    observed = harness.await_state(
        lambda state: state.drag_count >= 1 and state.scroll_count >= 1 and state.hotkey_count >= 1
    )
    assert report.status == "success"
    assert observed.drag_count >= 1
    assert observed.scroll_count >= 1
    assert observed.hotkey_count >= 1


@pytest.mark.windows_e2e
def test_double_click_is_distinct_from_two_clicks(harness: HarnessProcess) -> None:
    report = run_harness_workflow("double-click", harness)

    observed = harness.await_state(lambda state: state.double_click_count == 1)
    assert report.status == "success"
    assert observed.double_click_count == 1


@pytest.mark.windows_e2e
def test_failure_screenshot_masks_the_password_field(harness: HarnessProcess) -> None:
    from tests.integration.windows.helpers import run_harness_workflow_detailed

    outcome = run_harness_workflow_detailed("password-masking", harness)

    assert outcome.report.status == "failed"
    store = outcome.services.artifact_store
    assert store is not None
    # A screenshot is optional (it fails closed), but the harness must never have
    # recorded the password's characters regardless of what was captured.
    assert harness.state.password_present is False
